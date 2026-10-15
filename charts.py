"""Small inline-SVG chart helpers. No JavaScript, no external assets; colors come from CSS classes."""

from datetime import datetime, timedelta
from html import escape
from typing import Any

from markupsafe import Markup

import rules

STATE_CLASS = {"delayed": "c-bad", "at_risk": "c-warn", "unable_to_verify": "c-unk", "delivered_late": "c-late", "on_track": "c-ok"}
STATE_ORDER = ["delayed", "at_risk", "unable_to_verify", "delivered_late", "on_track"]


def _svg(width: int, height: int, body: str, label: str) -> Markup:
    return Markup(f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">{body}</svg>')


def bar_chart(items: list[tuple[str, float, str]], fmt=lambda v: f"{v:,.0f}", width: int = 560, label: str = "Bar chart") -> Markup:
    """Horizontal bars: (label, value, css class). Values are printed next to each bar."""
    if not items:
        return Markup('<p class="muted">No data.</p>')
    row, left, right = 28, 150, 90
    top = max((v for _, v, _ in items), default=0) or 1
    parts = []
    for i, (name, value, cls) in enumerate(items):
        y = i * row + 4
        w = max(1.0, (width - left - right) * value / top) if value else 0
        parts.append(f'<text class="ct" x="{left - 8}" y="{y + 15}" text-anchor="end">{escape(name)}</text>'
                     f'<rect class="{cls}" x="{left}" y="{y}" width="{w:.1f}" height="18" rx="3"/>'
                     f'<text class="ct" x="{left + w + 6:.1f}" y="{y + 14}">{escape(fmt(value))}</text>')
    return _svg(width, len(items) * row + 8, "".join(parts), label)


def state_bar(counts: dict[str, int], width: int = 560) -> Markup:
    total = sum(counts.values()) or 1
    x, parts, legend = 0.0, [], []
    for state in STATE_ORDER:
        n = counts.get(state, 0)
        if not n:
            continue
        w = width * n / total
        parts.append(f'<rect class="{STATE_CLASS[state]}" x="{x:.1f}" y="0" width="{w:.1f}" height="22"/>')
        legend.append(f'<span class="key"><i class="sw {STATE_CLASS[state]}"></i>{escape(state.replace("_", " "))} {n}</span>')
        x += w
    svg = _svg(width, 22, "".join(parts), "Shipments by state")
    return Markup(f'{svg}<p class="legend">{"".join(legend)}</p>')


def _short(dt: datetime) -> str:
    return f"{dt:%b} {dt.day} {dt:%H:%M}"


def eta_chart(history: list[dict[str, Any]], promise_utc: str | None, now_utc: str, width: int = 560, height: int = 220) -> Markup:
    """ETA over time: each point is what the carrier promised at that moment. Later moves are drawn in the warning color."""
    if not history or not promise_utc:
        return Markup('<p class="muted">No ETA history.</p>')
    pts = sorted((rules.parse_ts(h["observed_at"]), rules.parse_ts(h["eta"])) for h in history)
    promise, now = rules.parse_ts(promise_utc), rules.parse_ts(now_utc)
    xs = [p[0] for p in pts] + [now]
    ys = [p[1] for p in pts] + [promise]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys) - timedelta(hours=4), max(ys) + timedelta(hours=4)
    l, r, t, b = 64, 12, 12, 30
    sx = lambda d: l + (width - l - r) * ((d - x0).total_seconds() / max((x1 - x0).total_seconds(), 1))
    sy = lambda d: t + (height - t - b) * (1 - (d - y0).total_seconds() / max((y1 - y0).total_seconds(), 1))
    parts = [f'<line class="axis" x1="{l}" y1="{t}" x2="{l}" y2="{height - b}"/><line class="axis" x1="{l}" y1="{height - b}" x2="{width - r}" y2="{height - b}"/>']
    py = sy(promise)
    parts.append(f'<line class="promise" x1="{l}" y1="{py:.1f}" x2="{width - r}" y2="{py:.1f}"/>'
                 f'<text class="ct" x="{width - r}" y="{py - 4:.1f}" text-anchor="end">Promise {escape(_short(promise))} UTC</text>')
    nx = sx(now)
    parts.append(f'<line class="nowline" x1="{nx:.1f}" y1="{t}" x2="{nx:.1f}" y2="{height - b}"/><text class="ct" x="{nx - 4:.1f}" y="{t + 10}" text-anchor="end">now</text>')
    for i in range(1, len(pts)):
        later = pts[i][1] - pts[i - 1][1] > timedelta(seconds=60)
        parts.append(f'<line class="{"seg-later" if later else "seg"}" x1="{sx(pts[i-1][0]):.1f}" y1="{sy(pts[i-1][1]):.1f}" x2="{sx(pts[i][0]):.1f}" y2="{sy(pts[i][1]):.1f}"/>')
    for obs, eta in pts:
        parts.append(f'<circle class="pt" cx="{sx(obs):.1f}" cy="{sy(eta):.1f}" r="4"><title>{escape(_short(obs))}: ETA {escape(_short(eta))} UTC</title></circle>')
    parts.append(f'<text class="ct" x="{l - 6}" y="{t + 10}" text-anchor="end">{escape(_short(y1))}</text><text class="ct" x="{l - 6}" y="{height - b}" text-anchor="end">{escape(_short(y0))}</text>'
                 f'<text class="ct" x="{l}" y="{height - 8}">{escape(_short(x0))}</text><text class="ct" x="{width - r}" y="{height - 8}" text-anchor="end">{escape(_short(x1))} UTC</text>')
    return _svg(width, height, "".join(parts), "ETA history against the promise")


def penalty_chart(order: dict[str, Any], late_now: float | None, projected_late: float | None, width: int = 560, height: int = 220) -> Markup:
    """Penalty as a function of hours late, with grace, escalation, cap, and the now/projected positions."""
    esc = order.get("penalty_escalation")
    marks = [m for m in (late_now, projected_late) if m]
    x_max = max([10.0] + [m * 1.25 for m in marks] + ([esc["after_hours_late"] * 1.3] if esc else []))
    cap = order["penalty_cap_usd"]
    l, r, t, b = 64, 12, 14, 30
    sx = lambda h: l + (width - l - r) * h / x_max
    sy = lambda v: t + (height - t - b) * (1 - v / (cap * 1.1))
    pts = " ".join(f"{sx(h):.1f},{sy(rules.penalty(order, h)):.1f}" for h in [x_max * i / 80 for i in range(81)])
    parts = [f'<line class="axis" x1="{l}" y1="{t}" x2="{l}" y2="{height - b}"/><line class="axis" x1="{l}" y1="{height - b}" x2="{width - r}" y2="{height - b}"/>',
             f'<line class="promise" x1="{l}" y1="{sy(cap):.1f}" x2="{width - r}" y2="{sy(cap):.1f}"/><text class="ct" x="{width - r}" y="{sy(cap) - 4:.1f}" text-anchor="end">Cap ${cap:,.0f}</text>',
             f'<polyline class="seg" points="{pts}"/>']
    if esc:
        ex = sx(esc["after_hours_late"])
        parts.append(f'<line class="nowline" x1="{ex:.1f}" y1="{t}" x2="{ex:.1f}" y2="{height - b}"/><text class="ct" x="{ex + 4:.1f}" y="{t + 10}">escalates at {esc["after_hours_late"]}h</text>')
    for name, h, cls in (("now", late_now, "pt"), ("projected", projected_late, "pt-bad")):
        if h:
            v = rules.penalty(order, h)
            parts.append(f'<circle class="{cls}" cx="{sx(h):.1f}" cy="{sy(v):.1f}" r="5"><title>{name}: {h:.1f}h late, penalty ${v:,.2f}</title></circle>'
                         f'<text class="ct" x="{sx(h):.1f}" y="{sy(v) - 9:.1f}" text-anchor="middle">{name} ${v:,.0f}</text>')
    parts.append(f'<text class="ct" x="{l}" y="{height - 8}">0h late</text><text class="ct" x="{width - r}" y="{height - 8}" text-anchor="end">{x_max:.0f}h late</text>'
                 f'<text class="ct" x="{l - 6}" y="{height - b}" text-anchor="end">$0</text>')
    return _svg(width, height, "".join(parts), "Penalty as a function of hours late")


def forecast_chart(points: list[dict[str, Any]], width: int = 560, height: int = 200) -> Markup:
    """Active exposure now and at each horizon (x spacing proportional to hours). Pure SVG, colors from CSS classes."""
    if len(points) < 2:
        return Markup('<p class="muted">No forecast.</p>')
    l, r, t, b = 64, 64, 20, 30
    x_max = max(p["hours"] for p in points) or 1
    lo = min(p["active_exposure_usd"] for p in points)
    hi = max(p["active_exposure_usd"] for p in points)
    span = max(hi - lo, 1.0)
    sx = lambda h: l + (width - l - r) * h / x_max
    sy = lambda v: t + (height - t - b) * (1 - (v - lo) / span)
    line = " ".join(f"{sx(p['hours']):.1f},{sy(p['active_exposure_usd']):.1f}" for p in points)
    parts = [f'<line class="axis" x1="{l}" y1="{t}" x2="{l}" y2="{height - b}"/><line class="axis" x1="{l}" y1="{height - b}" x2="{width - r}" y2="{height - b}"/>',
             f'<polyline class="seg-later" points="{line}"/>']
    last_x = -1000.0
    for p in points:
        label = "now" if p["hours"] == 0 else f"+{p['hours']}h"
        x, y = sx(p["hours"]), sy(p["active_exposure_usd"])
        show_value = x - last_x >= 60  # skip a value label that would overlap the previous one (the dot keeps a tooltip)
        if show_value:
            last_x = x
        parts.append(f'<circle class="pt" cx="{x:.1f}" cy="{y:.1f}" r="4"><title>{escape(label)}: ${p["active_exposure_usd"]:,.0f}</title></circle>'
                     f'<text class="ct" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{escape(label)}</text>'
                     + (f'<text class="ct" x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle">${p["active_exposure_usd"]:,.0f}</text>' if show_value else ""))
    return _svg(width, height, "".join(parts), "Active exposure if nothing changes")


def trend_chart(series: dict[str, list[float | None]], fmt=lambda v: f"{v:,.0f}", label: str = "Trend", width: int = 560, height: int = 180) -> Markup:
    """One line per series (for example per model) across stored runs, oldest on the left. None values leave a gap."""
    n = max((len(v) for v in series.values()), default=0)
    values = [x for v in series.values() for x in v if x is not None]
    if n < 1 or not values:
        return Markup('<p class="muted">Not enough runs yet.</p>')
    l, r, t, b = 64, 16, 16, 26
    lo, hi = min(0.0, min(values)), max(values) or 1.0
    sx = lambda i: l + (width - l - r) * (i / (n - 1) if n > 1 else 0.5)
    sy = lambda v: t + (height - t - b) * (1 - (v - lo) / ((hi - lo) or 1.0))
    parts = [f'<line class="axis" x1="{l}" y1="{t}" x2="{l}" y2="{height - b}"/><line class="axis" x1="{l}" y1="{height - b}" x2="{width - r}" y2="{height - b}"/>',
             f'<text class="ct" x="{l - 6}" y="{t + 10}" text-anchor="end">{escape(fmt(hi))}</text><text class="ct" x="{l - 6}" y="{height - b}" text-anchor="end">{escape(fmt(lo))}</text>',
             f'<text class="ct" x="{l}" y="{height - 8}">oldest</text><text class="ct" x="{width - r}" y="{height - 8}" text-anchor="end">newest run</text>']
    legend = []
    for k, (name, vals) in enumerate(sorted(series.items())):
        cls = f"tr{k % 4}"
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) > 1:
            parts.append(f'<polyline class="{cls}" points="{" ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in pts)}"/>')
        for i, v in pts:
            parts.append(f'<circle class="{cls}d" cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="4"><title>{escape(name)}: {escape(fmt(v))}</title></circle>')
        legend.append(f'<span class="key"><i class="sw {cls}d"></i>{escape(name)}</span>')
    return Markup(f'{_svg(width, height, "".join(parts), label)}<p class="legend">{"".join(legend)}</p>')
