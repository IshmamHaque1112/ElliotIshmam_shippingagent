"""Incident clustering: exceptions that likely share one cause. Pure code; no model, no I/O.

Two rules, each with a threshold in config.py:
  * weather: the same weather note (first sentence) on the same origin-destination corridor;
  * facility: the same last facility, last scan at the facility, dwell over that facility's baseline.
Members' last scans must chain together with gaps of at most CLUSTER_WINDOW_H. A shipment belongs to one cluster at
most (weather is assigned first). A lone shipment is never a cluster. Ids come from the cause, not the members, so
they stay the same while members join or leave.
"""

import hashlib
from datetime import datetime
from typing import Any

import config
import rules

ACTIVE = ("delayed", "at_risk")


def note_key(note: str | None) -> str:
    """First sentence of a carrier note, lowercased: whatever follows (such as injected text) never changes the key."""
    text = (note or "").strip()
    return text.split(". ")[0].rstrip(".").strip().lower()


def is_weather(note: str | None) -> bool:
    words = note_key(note).replace(";", " ").replace(",", " ").split()
    return any(token.startswith(w) for token in words for w in config.WEATHER_WORDS)


def facts_from(snap) -> list[dict[str, Any]]:
    """One fact row per delayed or at-risk shipment that has a usable timeline."""
    facilities, out = snap.dataset["facilities"], []
    for a in snap.assessments:
        data = a.get("_data") or {}
        tracking, timeline = data.get("get_tracking_status"), data.get("event_timeline")
        if a["state"] not in ACTIVE or not tracking or not timeline:
            continue
        ts, last = rules.normalized_events(timeline["events"], facilities)[-1]
        carrier = snap.dataset["carriers"].get(tracking["carrier_id"], {})
        origin, dest = tracking.get("origin_facility_id") or "", tracking["destination_facility_id"]
        out.append({
            "shipment_id": a["shipment_id"], "state": a["state"], "exposure_usd": a["total_exposure_usd"] or 0.0,
            "facility_id": last["facility_id"], "facility": facilities[last["facility_id"]]["name"], "code": last["code"],
            "last_ts": rules.iso(ts), "dwell_hours": a["dwell_hours"], "baseline_hours": a["dwell_baseline_hours"],
            "carrier_id": tracking["carrier_id"], "carrier": carrier.get("name") or tracking["carrier_id"],
            "origin": origin, "origin_name": facilities.get(origin, {}).get("name", origin),
            "destination": dest, "destination_name": facilities[dest]["name"], "note": last.get("carrier_note") or "",
        })
    return out


def _chains(members: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split by time: a gap longer than the window starts a new chain."""
    ordered = sorted(members, key=lambda m: (m["last_ts"], m["shipment_id"]))
    chains: list[list[dict[str, Any]]] = []
    for m in ordered:
        if chains and (rules.parse_ts(m["last_ts"]) - rules.parse_ts(chains[-1][-1]["last_ts"])).total_seconds() <= config.CLUSTER_WINDOW_H * 3600:
            chains[-1].append(m)
        else:
            chains.append([m])
    return chains


def _build(cluster_id: str, kind: str, title: str, members: list[dict[str, Any]], recommendation: str, why: str, **extra: Any) -> dict[str, Any]:
    members = sorted(members, key=lambda m: (-m["exposure_usd"], m["shipment_id"]))
    times = sorted(m["last_ts"] for m in members)
    carriers = sorted({m["carrier"] for m in members})
    return {"id": cluster_id, "kind": kind, "title": title, "size": len(members), "members": members,
            "exposure_usd": round(sum(m["exposure_usd"] for m in members), 2), "carriers": carriers,
            "first_scan_utc": times[0], "last_scan_utc": times[-1], "why": why, "recommendation": recommendation, **extra}


def cluster(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    taken: set[str] = set()

    weather: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for f in facts:
        if is_weather(f["note"]):
            weather.setdefault((note_key(f["note"]), f["origin"], f["destination"]), []).append(f)
    for (key, origin, dest), members in sorted(weather.items()):
        base = "INC-WX-" + hashlib.sha1(f"{key}|{origin}|{dest}".encode()).hexdigest()[:8]
        for i, chain in enumerate(c for c in _chains(members) if len(c) >= config.CLUSTER_WEATHER_MIN_SIZE):
            first = chain[0]
            carriers = sorted({m["carrier"] for m in chain})
            clusters.append(_build(
                base if i == 0 else f"{base}-{i + 1}", "weather", f"Weather on {first['origin_name']} to {first['destination_name']}", chain,
                f"Ask {' and '.join(carriers)} for a corridor recovery estimate; hold firm customer promises on these {len(chain)} shipments until it arrives.",
                f"{len(chain)} shipments carry the same weather note on the same corridor.", corridor=f"{first['origin_name']} to {first['destination_name']}",
                carrier_note=chain[0]["note"]))
            taken.update(m["shipment_id"] for m in chain)

    by_facility: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        if f["shipment_id"] not in taken and f["code"] in config.FACILITY_CODES and f["dwell_hours"] is not None and f["dwell_hours"] > f["baseline_hours"]:
            by_facility.setdefault(f["facility_id"], []).append(f)
    for facility_id, members in sorted(by_facility.items()):
        for i, chain in enumerate(c for c in _chains(members) if len(c) >= config.CLUSTER_MIN_SIZE):
            carriers = sorted({m["carrier"] for m in chain})
            name = chain[0]["facility"]
            customs = all(m["code"] == "CUSTOMS_HOLD" for m in chain)  # decided by the scan code, never by note text
            action = (f"Ask the broker or {' and '.join(carriers)} which documents are missing and resubmit them once for all {len(chain)} shipments."
                      if customs else f"Escalate {name} with {' and '.join(carriers)}: {len(chain)} shipments are held beyond the facility's dwell baseline.")
            clusters.append(_build(
                f"INC-HUB-{facility_id}" if i == 0 else f"INC-HUB-{facility_id}-{i + 1}", "customs" if customs else "facility", f"{name} holds",
                chain, action,
                f"{len(chain)} shipments last scanned at {name} are past its {chain[0]['baseline_hours']} h dwell baseline.",
                facility_id=facility_id, facility=name))
    return sorted(clusters, key=lambda c: (-c["exposure_usd"], c["id"]))


def for_snapshot(snap) -> list[dict[str, Any]]:
    return cluster(facts_from(snap))


def find(clusters: list[dict[str, Any]], cluster_id: str) -> dict[str, Any] | None:
    return next((c for c in clusters if c["id"] == cluster_id), None)
