"""FastAPI app: fixtures, PDF upload, dashboard, advisor, CSV export."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import catalog
from .config import load_dotenv

load_dotenv()
from .advisor import advisor_status, run_advisor
from .analysis import analyze
from .export import transactions_csv
from .parsing.pdf_parser import MAX_UPLOAD_BYTES, StatementParseError, parse_statement_pdf
from .session_store import store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SAMPLES_DIR = WEB_DIR.parent / "samples"

app = FastAPI(
    title="Card Reward & Spend Optimizer",
    description="Statement-based reward leakage analysis. Local rules, in-memory only.",
    version="1.0.0",
)


class FixtureRequest(BaseModel):
    fixture_id: str
    wallet: Optional[list[str]] = None


def _analyze_and_store(raw_rows, wallet_ids, source, source_meta, parse_quality=None) -> dict:
    result = analyze(
        raw_rows,
        wallet_ids=wallet_ids,
        source=source,
        source_meta=source_meta,
        parse_quality=parse_quality,
    )
    session = store.put(result)
    payload = dict(result.payload)
    payload["session_id"] = session.id
    payload["advisor_status"] = advisor_status()
    return payload


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    """Everything the first screen needs in one round trip."""
    cards = catalog.cards()
    labels = catalog.category_labels()
    return {
        "fixtures": catalog.fixtures(),
        "cards": [
            {
                "id": card.id,
                "name": card.name,
                "issuer": card.issuer,
                "min_spend": card.min_spend,
                "monthly_cap": card.monthly_cap,
                "headline_rates": {
                    labels.get(k, k): f"{v * 100:.3g}%"
                    for k, v in sorted(card.category_rates.items(), key=lambda kv: -kv[1])
                },
                "base_rate_label": f"{card.base_rate * 100:.3g}%",
            }
            for card in cards.values()
        ],
        "samples": sorted(p.name for p in SAMPLES_DIR.glob("*.pdf")) if SAMPLES_DIR.exists() else [],
        "advisor_status": advisor_status(),
    }


@app.post("/api/analyze/fixture")
def analyze_fixture(request: FixtureRequest) -> dict:
    try:
        fixture = catalog.fixture(request.fixture_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown fixture '{request.fixture_id}'.")

    return _analyze_and_store(
        fixture["transactions"],
        request.wallet or fixture["wallet"],
        "fixture",
        {
            "kind": "fixture",
            "label": fixture["label"],
            "cycle_label": fixture["cycle_label"],
            "description": fixture["description"],
        },
    )


@app.post("/api/analyze/upload")
async def analyze_upload(
    files: list[UploadFile] = File(...),
    card_id: Optional[str] = Form(None),
    wallet: Optional[str] = Form(None),
) -> dict:
    """Parse one or more unlocked PDF e-statements.

    `card_id` forces the card for every uploaded file; otherwise the issuer is
    detected from the statement header. `wallet` is a comma-separated list of
    the cards the user holds.
    """
    if card_id and card_id not in catalog.cards():
        raise HTTPException(status_code=422, detail=f"Unknown card '{card_id}'.")

    rows: list[dict] = []
    quality = None
    names: list[str] = []
    for upload in files:
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )
        try:
            parsed = parse_statement_pdf(data, upload.filename or "statement.pdf", card_id)
        except StatementParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        rows.extend(parsed.rows)
        names.append(upload.filename or "statement.pdf")
        quality = parsed.merge_quality(quality)

    if not rows:
        raise HTTPException(
            status_code=422,
            detail=(
                "No transactions could be read from that file. v1 reads unlocked, "
                "text-based PDF e-statements from DBS, OCBC and UOB - scanned or "
                "password-protected files are out of scope."
            ),
        )

    wallet_ids = [w.strip() for w in wallet.split(",") if w.strip()] if wallet else None
    return _analyze_and_store(
        rows,
        wallet_ids,
        "pdf",
        {"kind": "upload", "label": ", ".join(names), "cycle_label": "uploaded statement"},
        quality,
    )


@app.post("/api/advisor/{session_id}")
def advisor(session_id: str) -> dict:
    """Run the reasoning-model strategist over an existing analysis."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session expired. Re-run the analysis.")
    if session.advisor is None:
        result = run_advisor(session.result.transactions, session.result.wallet, session.result.payload)
        session.advisor = result.to_dict()
    return session.advisor


@app.get("/api/export/{session_id}.csv")
def export_csv(session_id: str) -> PlainTextResponse:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session expired. Re-run the analysis.")
    csv_text = transactions_csv(
        session.result.transactions,
        session.result.payload.get("wallet_rules"),
        session.advisor,
    )
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="reward-audit-{session_id}.csv"'},
    )


@app.delete("/api/session/{session_id}")
def drop_session(session_id: str) -> dict:
    return {"dropped": store.drop(session_id)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
