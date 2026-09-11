# Statement-Based Card Reward & Spend Optimizer (v1)

Load a demo statement (or drop an unlocked DBS / OCBC / UOB PDF e-statement), and in
seconds see how much cashback your wallet left on the table, which card each purchase
should have gone on, and a three-rule plan for next month. No bank logins, nothing
saved to disk, everything processed in memory.

Optionally, a reasoning-model agent (DeepSeek via OpenCode by default) acts as your
strategist: it reads the card rules, tests candidate wallet strategies against the local
rules engine through function calling, and explains the trade-offs. Every number it
quotes is re-scored by the engine before you see it.

## Run it

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> and click **Load demo fixture**.

Every statement you analyze becomes a **month** you can keep alongside others: load a
fixture or upload a PDF, then click **+ Add month** to add the next one. Month tabs let
you switch between them, rename or remove any month, and an *Across your months* table
totals spend, rewards and missed value once you hold two or more. Months live in memory
only (30 minutes of inactivity each) and disappear when the process exits.

The AI strategist needs an OpenCode key. Copy `.env.example` to `.env` and set
`OPENCODE_API_KEY`. Without it the app runs fully local - the dashboard, CSV export and
rules-engine cheat sheet are unaffected; the strategist card simply says it is unavailable.

### Terminal / no browser

```bash
python tools/analyze_cli.py --list
python tools/analyze_cli.py --fixture sg_multi_card_cycle
python tools/analyze_cli.py --pdf samples/dbs_live_fresh_statement.pdf --wallet uob_one,ocbc_365
python tools/analyze_cli.py --fixture sg_multi_card_cycle \
    --strategy dining=ocbc_365,groceries=uob_one --default dbs_live_fresh
```

### Tests

```bash
.venv\Scripts\python -m pytest -q
```

## The 5-user demo script (about 3 minutes each)

1. Open the app. Point at the privacy pill: *no bank logins, in-memory only.*
2. Click **Load demo fixture** on the 3-card cycle. Time it - dashboard in under a second.
3. Read the headline: **"$72.24 left on the table this cycle"** on $2,822 of spend. Ask: *"Would you
   want to know this number for your own cards?"*
4. Scroll to **Suboptimal transactions**: groceries on DBS Live Fresh instead of UOB One
   ($17.88 on one NTUC trip). Ask: *"Did you know that was the wrong card?"*
5. Show **Cards this cycle**: OCBC 365 `min spend missed` - $371 on a card that needs $800,
   so its 6% dining earned 0.3%.
6. Show **Three rules for next month** and, if the strategist is on, the AI plan
   with its engine-verified projection and watch-outs.
7. Click **Download CSV audit**. Then **Clear session**.
8. The question that tests the riskiest assumption: *"Would you upload your real
   e-statement PDF to see this, given it never leaves your machine unless you switch
   on the AI strategist?"*

Sample unlocked PDFs for the upload path are in `samples/` (synthetic data; regenerate
with `python tools/make_sample_pdfs.py`).

### Configurable demo statements

The two fixtures above are fixed so the numbers in the script never move. Next to them the
start screen lists **generated** scenarios (3-card household, everything on one card,
grocery-heavy family, online shopper, rate-chasing per purchase). Pick one and click
**Generate & analyze**; open **Fine-tune** to change the cards in the wallet, the card habit
(spread, one primary card, mostly one card, best-rate per purchase), the transaction count,
total spend, category mix and the share of unfamiliar merchants. **Shuffle** draws a new seed;
the same seed always reproduces the same statement, and the dashboard shows the seed it used.

- Scenarios and defaults live in `data/demo_presets.json`; the merchant pool (with typical
  SGD ranges per category) in `data/demo_merchants.json`. Tests check that every pooled
  merchant categorizes to the category it is filed under.
- API: `POST /api/analyze/demo` with `{"preset": "household_3_card", "seed": 3, ...}` - any
  knob may be overridden; `/api/bootstrap` returns the presets, limits and card modes.
- CLI: `python tools/analyze_cli.py --demo online_shopper --set mix.dining=50 --seed 9`, and
  `--save-fixture path.json` freezes a generated statement as a regular fixture.

## What is in the box

```
app/
  main.py            FastAPI routes: bootstrap, analyze (fixture / demo / upload), months CRUD, advisor, CSV
  demo.py            seeded synthetic statement generator behind the configurable demo
  analysis.py        pipeline: redact -> categorize -> score actual -> allocate optimal -> payload
  rules_engine.py    pure reward maths: caps, minimum spends, optimal allocation, strategy scoring
  categorize.py      keyword rules with General Spend guardrail (never fails on an unknown merchant)
  redaction.py       names / addresses / card numbers removed at extraction
  advisor.py         LLM agent (OpenAI-style function calling over httpx) whose tools are the rules engine
  wallet.py          3-rule cheat sheet from the optimal allocation
  export.py          CSV audit
  session_store.py   in-memory months (one per analysis), TTL 30 min from last touch, no disk
  parsing/           pdfplumber text -> date / merchant / amount rows for DBS, OCBC, UOB layouts
data/
  cards.json         card profiles + stated v1 assumptions (the only reward "API")
  demo_presets.json  generated-demo scenarios and knob defaults
  demo_merchants.json synthetic merchant pool per category for the generator
  merchant_rules.json
  fixtures/          two synthetic statement cycles
samples/             three synthetic unlocked PDF e-statements
tools/               analyze_cli.py, make_sample_pdfs.py
web/                 one static page (app.js + demo.js), no framework, no build step
tests/               engine known-answer, categorizer, parser, API + stubbed advisor
.claude/skills/card-reward-planner/   Claude Code skill: financial-planner workflow on top of the engine
```

## How the numbers are produced

- **Rewards earned** - each transaction on the card it was actually charged to, scored
  chronologically with that card's category rate, monthly cap and minimum spend.
- **Optimal card yield** - the same spend re-allocated across the wallet, the better of
  two searches: a cap-aware greedy per-transaction pass (for every combination of cards
  that could be pushed to their minimum spend), and an exhaustive search over every plan
  with up to three category rules plus a default card. The greedy pass is a heuristic and
  the exact rule search sometimes beats it, so "optimal" is never below a plan we can name.
- **AI strategist** - a category-level plan (three rules + a default card) a person *can*
  follow. The model explores plans with `score_allocation`, a tool that runs the engine;
  the server re-scores the final recommendation, so the displayed projection is never the
  model's own arithmetic. If the provider rejects function calling, it falls back to a
  single request over engine-precomputed candidates (shown as "single-shot mode").
- **Assumptions** - every simplification of a real card's T&Cs is listed in
  `data/cards.json` and shown under *How these numbers were produced*.

## Privacy

- No credentials are requested or stored; there is no bank connection.
- Redaction runs on the raw statement text before categorization.
- Months (analysed statements) live in a process dict for 30 minutes after they were
  last viewed and die with the process. `DELETE /api/months` drops them all at once.
- With the strategist enabled, only redacted rows (date, merchant, amount, category,
  card used) are sent to the model provider - the UI states this next to the toggle.

## Configuration

See `.env.example`.

| Variable | Default | Effect |
|---|---|---|
| `OPENCODE_API_KEY` | (blank = strategist off) | Bearer token for the provider |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | OpenAI-compatible chat-completions base URL |
| `LLM_MODEL` | `deepseek-v4.1-flash` | Model id the provider serves |

Any OpenAI-compatible endpoint works. OpenCode Go additionally requires an
`x-opencode-session` header; the client sends one stable id per strategist run, which is
harmless for other providers. Google keys are reserved slots, unused in v1.

Measured on 2026-09-11 with `deepseek-v4.1-flash` at the provider's default reasoning
depth: about 17 s for the full tool loop (down from ~94 s before the engine started handing
the model exhaustive baselines and the loop capped scoring at one round). The dashboard
itself renders in well under a second; the strategist card fills in asynchronously.
`python tools/time_advisor.py` prints the per-turn token and latency audit.

## Out of scope (v1)

Encrypted-PDF unlocking, live bank linking, OCR, live reward-rule APIs, product
applications or referrals.
