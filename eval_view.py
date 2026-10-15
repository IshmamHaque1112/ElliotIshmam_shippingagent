"""Pure summaries of stored eval results for the /eval page (scorecards and trends). Never reads labels."""

from typing import Any


def scorecards(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per scenario: how many checks passed, failed or were skipped, and the pass rate over the checks that ran."""
    by: dict[str, dict[str, int]] = {}
    for r in results:
        s = by.setdefault(r.get("scenario") or "unknown", {"PASS": 0, "FAIL": 0, "SKIP": 0})
        s[r["result"]] = s.get(r["result"], 0) + 1
    out = []
    for name, c in sorted(by.items()):
        ran = c["PASS"] + c["FAIL"]
        out.append({"scenario": name, "passed": c["PASS"], "failed": c["FAIL"], "skipped": c["SKIP"], "rate": c["PASS"] / ran if ran else None})
    return sorted(out, key=lambda s: (s["rate"] is None, s["rate"] if s["rate"] is not None else 0, s["scenario"]))  # weakest first


def tokens_of(record: dict[str, Any]) -> int | None:
    usage = record.get("token_usage")
    return (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) if isinstance(usage, dict) else None


def trend(history: list[dict[str, Any]]) -> dict[str, Any]:
    """`history` newest first (as stored on the page). Returns runs oldest first with a series per model."""
    runs = list(reversed(history))
    names = sorted({(r.get("model") or "rules only") for r in runs})
    rate, tokens = {n: [None] * len(runs) for n in names}, {n: [None] * len(runs) for n in names}
    for i, r in enumerate(runs):
        n = r.get("model") or "rules only"
        ran = (r.get("passed") or 0) + (r.get("failed") or 0)
        rate[n][i] = 100.0 * (r.get("passed") or 0) / ran if ran else None
        tokens[n][i] = tokens_of(r)
    return {"runs": len(runs), "labels": [r.get("at", "") for r in runs], "pass_rate": rate, "tokens": tokens}
