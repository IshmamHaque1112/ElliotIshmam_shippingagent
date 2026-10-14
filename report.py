"""Self-contained static HTML snapshot (inline CSS and SVG, no network) of the dashboard, queue and saved cases."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import charts
import config
import pipeline
import store
import views

ROOT = Path(__file__).parent


def render() -> str:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    env.filters.update(usd=views.usd, css=views.css, words=views.words, num=views.num, hrs=views.hrs)
    snap = pipeline.snapshot()
    saved = store.list_cases(snap.dataset_name, latest_only=True)
    counts = {s: sum(1 for r in snap.rows if r["state"] == s) for s in charts.STATE_ORDER}
    queue = sorted((r for r in snap.rows if r["rank"]), key=lambda r: r["rank"])
    for c in saved:
        current = snap.by_id.get(c["shipment_id"])
        c["stale"] = current is None or c["input_hash"] != pipeline.rules_hash(current)
    return env.get_template("report.html").render(
        css=(ROOT / "static" / "app.css").read_text(encoding="utf-8"), snap=snap, var=snap.var, queue=queue,
        state_bar=charts.state_bar(counts), saved=saved)
