"""Command-line entry point: `queue` (rules only), `analyze` (case files) and `report` (static HTML)."""

import argparse
import json
import os
import sys

from anthropic import APIError

import agent
import case_file
import config
import pipeline
from api_errors import MissingKeyError, api_error_message


def format_header(var: dict) -> list[str]:
    return [
        f"Active exceptions: {var['active_exceptions']} | Revenue exposed: ${var['revenue_exposed_usd']:,.2f}"
        f" | Accrued penalties: ${var['accrued_penalties_usd']:,.2f}",
        f"Closed late cases: {var['closed_late_cases']} | Penalties owed: ${var['closed_late_penalties_usd']:,.2f}"
        f" | On track: {var['on_track']}",
    ]


def format_queue(queue: list[dict]) -> list[str]:
    lines = []
    for rank, item in enumerate(queue, 1):
        countdown = item["next_milestone"]["minutes_from_now"] if item["next_milestone"] else None
        lines.append(
            f"{rank:>2}. {item['shipment_id']}  {item['state']:<16} {item['urgency_badge'] or '-':<24} "
            f"{item['severity'] or '-':<6} ${item['total_exposure_usd'] or 0:>10,.2f}  "
            f"{'T-%dm' % countdown if countdown is not None else '-':<8} {item['recommended_action_id'] or '-'}"
        )
    return lines


def run_queue(as_json: bool) -> int:
    assessments = pipeline.assess_all(pipeline.load_service())
    queue = pipeline.ranked_queue(assessments)
    var = pipeline.value_at_risk(assessments)
    if as_json:
        clean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in queue]
        print(json.dumps({"value_at_risk": var, "queue": clean}, indent=2))
    else:
        print("\n".join(format_header(var) + [""] + format_queue(queue)))
    return 0


def format_case(case: case_file.CaseFile) -> str:
    d = case.model_dump()
    out = [f"{d['shipment_id']} | {d['state']} | {d['severity']} | {d['urgency_badge']} | {d['run_status']} | narrative: {d['narrative']}",
           f"  Cause: {d['classification']['root_cause']} / {d['classification']['accountability']} (confidence {d['classification']['confidence']})",
           f"  Exposure: ${d['financial']['total_exposure_usd'] or 0:,.2f}  Recommended: {d['recommendation']['action_id']}",
           f"  Summary: {d['timeline_summary']}"]
    out += [f"  - {note}" for note in d["evidence_footnotes"]] + [f"  ? {u}" for u in d["unknowns"]]
    for name, text in d["drafts"].items():
        if text:
            out += [f"  --- {name} (draft; nothing is sent) ---", text]
    return "\n".join(out)


def run_analyze(shipment_id: str | None, no_llm: bool, as_json: bool) -> int:
    service = pipeline.load_service()
    assessments = pipeline.assess_all(service)
    targets = [a for a in pipeline.ranked_queue(assessments) if shipment_id in (None, a["shipment_id"])]
    if shipment_id and not targets:
        print(f"{shipment_id}: not an exception (or unknown id); nothing to analyze.", file=sys.stderr)
        return 1
    client = None
    try:
        if not no_llm:
            client = agent.make_client()
        cases = []
        for item in targets:
            analyze = None if client is None else (lambda hint, item=item: agent.investigate(client, service, item, hint))
            cases.append(case_file.build_case_file(item, analyze))
    except MissingKeyError as error:
        print(f"Could not analyze: {error} Use --no-llm for rules only.", file=sys.stderr)
        return 2
    except APIError as error:
        print(api_error_message(error), file=sys.stderr)
        return 1
    print(json.dumps([c.model_dump() for c in cases], indent=2) if as_json else "\n\n".join(format_case(c) for c in cases))
    return 0


def run_report(out: str) -> int:
    import report

    with open(out, "w", encoding="utf-8") as handle:
        handle.write(report.render())
    print(f"Wrote {out} (static snapshot; open it in a browser).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delayed Shipment Exception Co-Pilot (read-only).")
    commands = parser.add_subparsers(dest="command", required=True)
    queue = commands.add_parser("queue", help="ranked exception queue computed by rules only")
    queue.add_argument("--no-llm", action="store_true", help="accepted for clarity; the queue never calls Claude")
    queue.add_argument("--json", action="store_true")
    analyze = commands.add_parser("analyze", help="case files for exceptions (Claude explains; rules decide)")
    analyze.add_argument("shipment_id", nargs="?")
    analyze.add_argument("--no-llm", action="store_true", help="rules and fallback case files only; no API key needed")
    analyze.add_argument("--json", action="store_true")
    snapshot = commands.add_parser("report", help="write a self-contained static HTML report of the queue and saved cases")
    snapshot.add_argument("--out", default="report.html")
    parser.add_argument("--dataset", choices=sorted(config.DATASET_FILES), help="override DATASET for this run (dev or extended)")
    args = parser.parse_args(argv)
    if args.dataset:
        os.environ["DATASET"] = args.dataset
    if args.command == "report":
        return run_report(args.out)
    if args.command == "queue":
        return run_queue(args.json)
    return run_analyze(args.shipment_id, args.no_llm, args.json)


if __name__ == "__main__":
    sys.exit(main())
