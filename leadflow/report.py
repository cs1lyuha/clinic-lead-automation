"""Raportul pentru managerul clinicii: ce a intrat, din ce sursă, cât de repede am răspuns, câți au venit."""

from __future__ import annotations

import sqlite3
import statistics
from collections import Counter
from datetime import date, datetime
from typing import Any

FUNNEL = ("new", "contacted", "booked", "attended")
REACHED = {"contacted": 1, "booked": 2, "attended": 3, "no_show": 2, "lost": 1}  # cât de departe a ajuns lead-ul


def build(conn: sqlite3.Connection, start: date, end: date) -> dict[str, Any]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM leads WHERE duplicate_of IS NULL AND substr(created_at, 1, 10) BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()))]
    real = [r for r in rows if r["priority"] != "spam"]
    response_min = [
        (datetime.fromisoformat(r["contacted_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60
        for r in real if r["contacted_at"]]
    urgent_min = [
        (datetime.fromisoformat(r["contacted_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60
        for r in real if r["contacted_at"] and r["urgent"]]
    stage = Counter(REACHED.get(r["status"], 0) for r in real)
    funnel = {name: sum(n for level, n in stage.items() if level >= i) for i, name in enumerate(FUNNEL)}
    by_source = Counter(r["source"] for r in real)
    booked_by_source = Counter(r["source"] for r in real if REACHED.get(r["status"], 0) >= 2)
    return {
        "period": f"{start.isoformat()} – {end.isoformat()}",
        "leads": len(real),
        "spam_blocked": len(rows) - len(real),
        "hot": sum(r["priority"] == "hot" for r in real),
        "urgent": sum(bool(r["urgent"]) for r in real),
        "by_intent": dict(Counter(r["intent"] for r in real).most_common()),
        "by_source": {s: {"leads": n, "booked": booked_by_source[s],
                          "rate": round(booked_by_source[s] / n, 2) if n else 0.0}
                      for s, n in by_source.most_common()},
        "by_language": dict(Counter(r["language"] for r in real)),
        "funnel": funnel,
        "booking_rate": round(funnel["booked"] / funnel["new"], 2) if funnel["new"] else 0.0,
        "median_response_min": round(statistics.median(response_min), 1) if response_min else None,
        "median_urgent_response_min": round(statistics.median(urgent_min), 1) if urgent_min else None,
        "pipeline_value": sum(r["estimated_value"] for r in real if r["status"] in ("new", "contacted", "booked")),
        "booked_value": sum(r["estimated_value"] for r in real if REACHED.get(r["status"], 0) >= 2
                            and r["status"] != "no_show"),
    }


def _lei(value: int) -> str:
    return f"{value:,} lei".replace(",", " ")


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# Raport lead-uri · {rep['period']}", "",
             f"- **{rep['leads']}** lead-uri reale ({rep['hot']} fierbinți, {rep['urgent']} urgențe), "
             f"**{rep['spam_blocked']}** mesaje spam blocate",
             f"- rată de programare: **{rep['booking_rate']:.0%}**",
             f"- timp median până la primul contact: **{rep['median_response_min']} min** "
             f"(urgențe: {rep['median_urgent_response_min']} min)",
             f"- valoare programată: **{_lei(rep['booked_value'])}**, în lucru: {_lei(rep['pipeline_value'])}",
             "", "## Pâlnie", "", "| etapă | lead-uri |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in rep["funnel"].items()]
    lines += ["", "## Surse", "", "| sursă | lead-uri | programați | rată |", "|---|---:|---:|---:|"]
    lines += [f"| {s} | {d['leads']} | {d['booked']} | {d['rate']:.0%} |" for s, d in rep["by_source"].items()]
    lines += ["", "## Ce cer pacienții", "", "| intenție | lead-uri |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in rep["by_intent"].items()]
    lines += ["", f"Limbă: {', '.join(f'{k}: {v}' for k, v in rep['by_language'].items())}"]
    return "\n".join(lines) + "\n"
