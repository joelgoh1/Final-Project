"""FastAPI app: fixtures, PDF upload, dashboard, advisor, CSV export."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import catalog, demo
from .config import load_dotenv

load_dotenv()
from .advisor import advisor_status, run_advisor
from .analysis import analyze
from .export import transactions_csv
from .parsing.pdf_parser import MAX_UPLOAD_BYTES, StatementParseError, parse_statement_pdf
from .session_store import Session, clean_label, store

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
    label: Optional[str] = None


class DemoRequest(BaseModel):
    """Start from a preset (or the defaults) and override any generator knob."""

    preset: Optional[str] = None
    wallet: Optional[list[str]] = None
    month: Optional[str] = None
    transaction_count: Optional[int] = None
    total_spend: Optional[float] = None
    mix: Optional[dict[str, float]] = None
    unmapped_pct: Optional[float] = None
    card_mode: Optional[str] = None
    primary_card: Optional[str] = None
    seed: Optional[int] = None
    label: Optional[str] = None


class MonthUpdate(BaseModel):
    label: str


def _default_label(payload: dict) -> str:
    return payload.get("period", {}).get("label") or payload["source"].get("cycle_label") or "Statement"


def _session_payload(session: Session) -> dict:
    """Full dashboard payload for one stored month."""
    payload = dict(session.result.payload)
    payload["session_id"] = session.id
    payload["label"] = session.label
    payload["created_at"] = session.created_at
    payload["advisor_status"] = advisor_status()
    payload["advisor"] = session.advisor
    return payload


def _month_summary(session: Session) -> dict:
    payload = session.result.payload
    return {
        "session_id": session.id,
        "label": session.label,
        "period": payload.get("period", {}),
        "source": payload["source"],
        "summary": payload["summary"],
        "created_at": session.created_at,
        "advisor_ran": session.advisor is not None,
    }


def _analyze_and_store(raw_rows, wallet_ids, source, source_meta, parse_quality=None, label=None) -> dict:
    result = analyze(
        raw_rows,
        wallet_ids=wallet_ids,
        source=source,
        source_meta=source_meta,
        parse_quality=parse_quality,
    )
    session = store.put(result, label=clean_label(label, _default_label(result.payload)))
    return _session_payload(session)


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
        "demo": demo.bootstrap_payload(),
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
        label=request.label,
    )


@app.post("/api/analyze/demo")
def analyze_demo(request: DemoRequest) -> dict:
    """Generate a synthetic statement from the requested knobs and analyze it."""
    overrides = request.model_dump(exclude={"preset"})
    try:
        if request.preset and overrides.get("label") is None:
            overrides["label"] = demo.preset(request.preset)["label"]
        config = demo.resolve_config(request.preset, overrides)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown demo preset '{request.preset}'.")
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "config"
            messages.append(f"{field}: {error['msg'].removeprefix('Value error, ')}")
        raise HTTPException(status_code=422, detail=" ".join(messages))

    statement = demo.generate(config)
    payload = _analyze_and_store(statement.rows, config.wallet, "demo", statement.meta, label=config.label)
    payload["demo_config"] = config.model_dump()
    payload["demo_config"]["preset"] = request.preset
    return payload


@app.post("/api/analyze/upload")
async def analyze_upload(
    files: list[UploadFile] = File(...),
    card_id: Optional[str] = Form(None),
    wallet: Optional[str] = Form(None),
    label: Optional[str] = Form(None),
) -> dict:
    """Parse one or more unlocked PDF e-statements into one month.

    `card_id` forces the card for every uploaded file; otherwise the issuer is
    detected from the statement header. `wallet` is a comma-separated list of
    the cards the user holds. `label` names the month; it defaults to the
    period covered by the transaction dates.
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
        {"kind": "upload", "label": ", ".join(names)},
        quality,
        label=label,
    )


# --- Months: every analysis is one month; keep several and switch between them ---


@app.get("/api/months")
def list_months() -> dict:
    """All months held in memory, in statement-period order, with running totals."""
    sessions = sorted(
        store.list(),
        key=lambda s: (s.result.payload.get("period", {}).get("key", ""), s.created_at),
    )
    months = [_month_summary(s) for s in sessions]
    totals = {"total_spend": 0.0, "actual_rewards": 0.0, "optimal_rewards": 0.0,
              "missed_value": 0.0, "transaction_count": 0}
    for month in months:
        for key in totals:
            totals[key] += month["summary"][key]
    for key in ("total_spend", "actual_rewards", "optimal_rewards", "missed_value"):
        totals[key] = round(totals[key], 2)
    spend = totals["total_spend"]
    totals["actual_yield_pct"] = round(totals["actual_rewards"] / spend * 100, 2) if spend else 0.0
    totals["optimal_yield_pct"] = round(totals["optimal_rewards"] / spend * 100, 2) if spend else 0.0
    return {"months": months, "count": len(months), "totals": totals}


@app.get("/api/months/{session_id}")
def get_month(session_id: str) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That month has expired. Add it again.")
    return _session_payload(session)


@app.patch("/api/months/{session_id}")
def rename_month(session_id: str, update: MonthUpdate) -> dict:
    if not update.label.strip():
        raise HTTPException(status_code=422, detail="Give the month a non-empty name.")
    session = store.rename(session_id, update.label)
    if session is None:
        raise HTTPException(status_code=404, detail="That month has expired. Add it again.")
    return _month_summary(session)


@app.delete("/api/months/{session_id}")
def delete_month(session_id: str) -> dict:
    return {"dropped": store.drop(session_id)}


@app.delete("/api/months")
def delete_all_months() -> dict:
    count = len(store)
    store.clear()
    return {"dropped": count}


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


from .strategist_routes import router as strategist_router  # noqa: E402 - talk-back chat with the strategist

app.include_router(strategist_router)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
