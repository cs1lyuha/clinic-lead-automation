"""Simulare: o săptămână de lead-uri la o clinică, trecute prin tot fluxul (webhook semnat → raport).

    python -m leadflow.simulate              # raportul în terminal + reports/simulare.md

ATENȚIE: datele sunt generate (seed fix), nu provin de la o clinică reală. Ipotezele sunt
scrise mai jos, ca cifrele să poată fi verificate și schimbate.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from leadflow import report, store
from leadflow.api import create_app
from leadflow.integrations import MemoryCRM, MemoryNotifier
from leadflow.pipeline import LeadPipeline
from leadflow.security import sign

# ---------- ipoteze ----------
LEADS_PER_DAY = (6, 12)
SOURCES = [("facebook", 0.35), ("instagram", 0.25), ("website", 0.25), ("google_ads", 0.15)]
SPAM_SHARE = 0.08
# minute până la primul contact: recepția lucrează din lista ordonată (urgențe, apoi scor)
RESPONSE_MIN = {"hot": (2, 15), "warm": (10, 60), "cold": (30, 240)}
BOOK_PROB = {"hot": 0.7, "warm": 0.45, "cold": 0.2}
SHOW_PROB = 0.85
MANUAL_TRIAGE_MIN = 3.0  # cât durează să citești, să clasifici și să introduci manual un lead în CRM

# (limbă, mesaj, pondere): mixul tipic de cereri la o clinică de familie
MESSAGES = [
    ("ro", "Mă doare măseaua de două zile, se poate azi?", 8),
    ("ro", "Mi s-a umflat obrazul și doare tare, urgent vă rog", 5),
    ("ro", "Bună ziua, vreau un implant, îmi lipsește un dinte de jos", 6),
    ("ro", "Cât costă un aparat dentar pentru adult?", 5),
    ("ro", "Aș dori albire profesională înainte de nuntă, în noiembrie", 5),
    ("ro", "Fiica mea are 7 ani, vrem un control și sigilare", 5),
    ("ro", "Detartraj și igienizare, când aveți loc?", 9),
    ("ro", "Bună, aș vrea o consultație generală", 12),
    ("ro", "Cât costă detartrajul?", 6),
    ("ru", "Здравствуйте, сильно болит зуб, можно сегодня?", 7),
    ("ru", "Сколько стоит имплант под ключ?", 4),
    ("ru", "Хочу поставить брекеты, нужна консультация", 4),
    ("ru", "Ребенку 5 лет, нужен детский стоматолог", 4),
    ("ru", "Нужна профессиональная чистка зубов на этой неделе", 6),
]
SPAM = ["Best SEO backlinks for your clinic http://seo.example", "Crypto investment, 300% profit http://x.example"]
NAMES = ["Ana Popescu", "Ion Rusu", "Maria Ceban", "Victor Lungu", "Elena Moraru", "Олег Иванов",
         "Ирина Попова", "Andrei Cojocaru", "Natalia Grosu", "Сергей Ткач", "Cristina Bivol", "Dumitru Sârbu"]


def simulate(seed: int = 7, days: int = 7, start: date = date(2026, 10, 12)) -> dict:
    rng = random.Random(seed)
    pipe = LeadPipeline(store.connect(), MemoryCRM(), MemoryNotifier())
    secret = "whsec_sim"
    client = TestClient(create_app(pipe, webhook_secret=secret, api_key="sim"))
    n = 0
    for day in range(days):
        for _ in range(rng.randint(*LEADS_PER_DAY)):
            n += 1
            created = datetime(start.year, start.month, start.day, 8, tzinfo=timezone.utc) + timedelta(
                days=day, minutes=rng.randint(0, 11 * 60))
            spam = rng.random() < SPAM_SHARE
            lang, msg = ("ro", rng.choice(SPAM)) if spam else rng.choices(
                [m[:2] for m in MESSAGES], [m[2] for m in MESSAGES])[0]
            name = rng.choice([x for x in NAMES if (x[0] in "ОИС") == (lang == "ru")])
            payload = {"delivery_id": f"sim-{n:06d}", "name": name,
                       "phone": f"06{rng.randint(0, 9)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
                       "email": None if rng.random() < 0.6 else f"{name.split()[0].lower()}{n}@mail.md",
                       "message": msg,
                       "source": rng.choices([s for s, _ in SOURCES], [w for _, w in SOURCES])[0],
                       "created_at": created.isoformat()}
            body = json.dumps(payload, ensure_ascii=False).encode()
            ts, sig = sign(secret, body)
            res = client.post("/webhooks/lead", content=body, headers={
                "Content-Type": "application/json", "X-Leadflow-Timestamp": ts, "X-Leadflow-Signature": sig}).json()
            if res["priority"] == "spam" or res["status"] != "created":
                continue
            # recepția: primul contact, apoi programare și prezentare
            lo, hi = RESPONSE_MIN[res["priority"]]
            contacted = created + timedelta(minutes=rng.uniform(lo, hi))
            store.set_status(pipe.conn, res["lead_id"], "contacted", contacted)
            if rng.random() < BOOK_PROB[res["priority"]]:
                store.set_status(pipe.conn, res["lead_id"], "booked", contacted + timedelta(minutes=3))
                store.set_status(pipe.conn, res["lead_id"],
                                 "attended" if rng.random() < SHOW_PROB else "no_show", contacted + timedelta(days=2))
            elif rng.random() < 0.5:
                store.set_status(pipe.conn, res["lead_id"], "lost", contacted + timedelta(days=1))
    rep = report.build(pipe.conn, start, start + timedelta(days=days - 1))
    rep["triage_minutes_saved"] = round((rep["leads"] + rep["spam_blocked"]) * MANUAL_TRIAGE_MIN)
    rep["telegram_alerts"] = len(pipe.notifier.messages)
    rep["crm_deals"] = len(pipe.crm.deals)
    return rep


def main() -> None:
    rep = simulate()
    md = report.to_markdown(rep)
    md += (f"\nTimp de triere economisit: ~{rep['triage_minutes_saved']} min/săptămână "
           f"({rep['triage_minutes_saved'] / 60:.1f} ore). Alerte Telegram: {rep['telegram_alerts']}. "
           f"Deal-uri create în CRM: {rep['crm_deals']}.\n\n_Date simulate (seed 7), vezi ipotezele din simulate.py._\n")
    out = Path(__file__).resolve().parent.parent / "reports" / "simulare.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
