"""Run the reward-leakage analysis from a terminal, no server needed.

    python tools/analyze_cli.py --fixture sg_multi_card_cycle
    python tools/analyze_cli.py --pdf samples/dbs_live_fresh_statement.pdf --wallet uob_one,ocbc_365
    python tools/analyze_cli.py --fixture dining_heavy_cycle --json      # full payload
    python tools/analyze_cli.py --fixture sg_multi_card_cycle --strategy dining=ocbc_365,groceries=uob_one --default dbs_live_fresh
    python tools/analyze_cli.py --demo household_3_card --seed 3          # generated demo statement
    python tools/analyze_cli.py --demo online_shopper --set transaction_count=80 --set mix.dining=50
    python tools/analyze_cli.py --demo grocery_family --save-fixture data/fixtures/03_grocery_family.json

Used by the card-reward-planner skill and handy for checking a rules change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog, demo  # noqa: E402
from app.analysis import analyze  # noqa: E402
from app.parsing.pdf_parser import parse_statement_pdf  # noqa: E402
from app.rules_engine import score_strategy  # noqa: E402


def _money(value: float) -> str:
    return f"${value:,.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", help="fixture id from data/fixtures")
    source.add_argument("--pdf", nargs="+", help="one or more unlocked PDF e-statements")
    source.add_argument("--demo", metavar="PRESET", help="generate a synthetic statement from a demo preset (or 'defaults')")
    source.add_argument("--list", action="store_true", help="list fixtures, demo presets and cards, then exit")
    parser.add_argument("--seed", type=int, help="seed for --demo (defaults to the preset's)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a --demo knob, e.g. transaction_count=80, total_spend=3000, "
                             "card_mode=habit, primary_card=uob_one, unmapped_pct=10, mix.dining=40")
    parser.add_argument("--save-fixture", metavar="PATH", help="with --demo: also write the statement as a fixture JSON")
    parser.add_argument("--wallet", help="comma-separated card ids the user holds")
    parser.add_argument("--card", help="force this card id for uploaded PDFs")
    parser.add_argument("--strategy", help="score a plan: category=card_id,category=card_id")
    parser.add_argument("--default", dest="default_card", help="default card id for --strategy")
    parser.add_argument("--json", action="store_true", help="print the full dashboard payload as JSON")
    args = parser.parse_args()

    if args.list:
        print("Fixtures:")
        for fx in catalog.fixtures():
            print(f"  {fx['id']:<24} {fx['label']} ({fx['transaction_count']} rows)")
        print("Demo presets (--demo):")
        for preset in demo.presets():
            cfg = preset["config"]
            print(f"  {preset['id']:<26} {preset['label']} ({cfg['transaction_count']} rows, ${cfg['total_spend']:,.0f})")
        print("Cards:")
        for card in catalog.cards().values():
            print(f"  {card.id:<20} {card.name} - min ${card.min_spend:,.0f}, cap "
                  f"{'none' if card.monthly_cap is None else '$' + format(card.monthly_cap, ',.0f')}")
        return 0

    wallet_ids = [w.strip() for w in args.wallet.split(",")] if args.wallet else None
    if args.demo:
        overrides = {"seed": args.seed}
        if wallet_ids:
            overrides["wallet"] = wallet_ids
        mix = None
        for pair in args.set:
            key, _, value = pair.partition("=")
            if key.startswith("mix."):
                mix = mix or dict(demo.resolve_config(None if args.demo == "defaults" else args.demo).mix)
                mix[key[4:]] = float(value)
            elif key in ("transaction_count", "seed"):
                overrides[key] = int(value)
            elif key in ("total_spend", "unmapped_pct"):
                overrides[key] = float(value)
            else:
                overrides[key] = value
        if mix is not None:
            overrides["mix"] = mix
        config = demo.resolve_config(None if args.demo == "defaults" else args.demo, overrides)
        statement = demo.generate(config)
        rows, wallet_ids, meta, quality = statement.rows, config.wallet, statement.meta, None
        meta = dict(meta, label=config.label or (args.demo if args.demo != "defaults" else "custom demo"))
        if args.save_fixture:
            out = Path(args.save_fixture)
            out.write_text(json.dumps(statement.to_fixture(out.stem), indent=2) + "\n", encoding="utf-8")
            print(f"saved fixture -> {out}")
    elif args.fixture:
        fixture = catalog.fixture(args.fixture)
        rows, wallet_ids = fixture["transactions"], wallet_ids or fixture["wallet"]
        meta = {"kind": "fixture", "label": fixture["label"], "cycle_label": fixture["cycle_label"]}
        quality = None
    else:
        rows, quality, names = [], None, []
        for path in args.pdf:
            parsed = parse_statement_pdf(Path(path).read_bytes(), Path(path).name, args.card)
            rows.extend(parsed.rows)
            names.append(Path(path).name)
            quality = parsed.merge_quality(quality)
        meta = {"kind": "upload", "label": ", ".join(names), "cycle_label": "uploaded statement"}

    result = analyze(rows, wallet_ids, "demo" if args.demo else ("fixture" if args.fixture else "pdf"), meta, quality)
    payload = result.payload

    if args.strategy:
        mapping = dict(pair.split("=", 1) for pair in args.strategy.split(","))
        allocation = score_strategy(result.transactions, result.wallet, mapping, args.default_card)
        payload["strategy_check"] = {
            "rules": mapping,
            "default_card_id": args.default_card,
            "total_reward": round(allocation.total_reward, 2),
            "vs_actual": round(allocation.total_reward - payload["summary"]["actual_rewards"], 2),
            "cards": {
                cid: {"spend": round(l.spend, 2), "reward": round(l.reward, 2), "bonus_active": cid in allocation.bonus_cards}
                for cid, l in allocation.ledgers.items()
            },
        }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    s = payload["summary"]
    print(f"{meta['label']} - {meta['cycle_label']}")
    print(f"  spend {_money(s['total_spend'])} over {s['transaction_count']} rows "
          f"({payload['parse_quality']['general_spend_rows']} on General Spend)")
    print(f"  earned {_money(s['actual_rewards'])} ({s['actual_yield_pct']}%)  "
          f"optimal {_money(s['optimal_rewards'])} ({s['optimal_yield_pct']}%)  "
          f"MISSED {_money(s['missed_value'])}")
    print("  cards:")
    for card in payload["cards"]:
        flags = []
        if card["min_spend"]:
            flags.append("min met" if card["min_spend_met"] else "MIN MISSED")
        if card["monthly_cap"] and card["cap_reached"]:
            flags.append("CAP HIT")
        print(f"    {card['name']:<26} spend {_money(card['actual_spend']):>10}  earned {_money(card['actual_reward']):>8}  "
              f"{' '.join(flags)}")
    print("  wallet cheat sheet:")
    for rule in payload["wallet_rules"]:
        print(f"    {rule['category_label']} -> {rule['card_name']} ({rule['rate_label']}; {rule['condition']})")
    if payload["suboptimal"]:
        print("  biggest leaks:")
        for row in payload["suboptimal"][:5]:
            print(f"    {row['date']} {row['merchant'][:30]:<30} {_money(row['amount']):>9}  "
                  f"{row['actual_card']} -> {row['better_card']}  +{_money(row['missed'])}")
    if "strategy_check" in payload:
        sc = payload["strategy_check"]
        print(f"  strategy check: {sc['rules']} default={sc['default_card_id']} -> "
              f"{_money(sc['total_reward'])} ({sc['vs_actual']:+.2f} vs actual)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
