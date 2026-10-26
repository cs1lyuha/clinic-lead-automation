"""Fluxul complet pentru un lead:

    livrare deja văzută? → același rezultat (retry de la n8n/Facebook)
    normalizare → duplicat în ultimele 30 de zile? → doar eveniment pe lead-ul existent
    calificare (AI / reguli) → scor → salvare → CRM → notificare (urgent: imediat; spam: deloc)

CRM-ul și Telegram-ul pot pica. Lead-ul e salvat ÎNAINTE de ele, iar eșecul se
notează ca eveniment (`crm_failed`), ca `retry_crm()` să-l reia. Nu pierdem lead-uri.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from leadflow import store
from leadflow.integrations import CRM, Notifier, lead_card
from leadflow.normalize import InvalidPhone, LeadIn, normalize_name, normalize_phone, utc
from leadflow.qualify import Qualifier, RuleQualifier, score

log = logging.getLogger("leadflow")


@dataclass
class Result:
    lead_id: int
    status: str        # created | duplicate | replayed
    priority: str
    notified: bool = False
    crm_synced: bool = False


class LeadPipeline:
    def __init__(self, conn: sqlite3.Connection, crm: CRM, notifier: Notifier,
                 qualifier: Qualifier | None = None) -> None:
        self.conn = conn
        self.crm = crm
        self.notifier = notifier
        self.qualifier = qualifier or RuleQualifier()

    def process(self, payload: LeadIn, now: datetime | None = None) -> Result:
        now = utc(now or payload.created_at)
        seen = store.delivery_seen(self.conn, payload.delivery_id)
        if seen is not None:
            lead = self._lead(seen)
            return Result(seen, "replayed", lead["priority"], crm_synced=lead["crm_id"] is not None)

        try:
            phone = normalize_phone(payload.phone)
        except InvalidPhone:
            phone = None  # păstrăm lead-ul; recepția vede mesajul original
        email = payload.email.lower() if payload.email else None
        name = normalize_name(payload.name)

        q = self.qualifier.qualify(name, payload.message)
        scored = score(q, has_phone=bool(phone), has_email=bool(email), name=name, qualifier=self.qualifier.name)
        original = store.find_recent_duplicate(self.conn, phone, email, now)

        row: dict[str, Any] = {
            "name": name, "phone": phone, "email": email, "message": payload.message, "source": payload.source,
            "utm_campaign": payload.utm_campaign, "intent": q.intent, "urgent": int(q.urgent),
            "language": q.language, "score": scored.score, "priority": scored.priority,
            "estimated_value": scored.estimated_value, "suggested_reply": scored.suggested_reply,
            "qualifier": scored.qualifier, "status": "spam" if scored.priority == "spam" else "new",
            "duplicate_of": original["id"] if original else None, "created_at": now.isoformat(),
        }
        lead_id = store.insert_lead(self.conn, payload.delivery_id, row)

        if original is not None:
            store.log_event(self.conn, original["id"], "repeat_contact", now, new_lead_id=lead_id, intent=q.intent)
            # un pacient care revine cu durere e tot urgență, chiar dacă e duplicat
            if q.urgent:
                self._notify({**row, "id": lead_id}, prefix=f"(revine, vezi #{original['id']}) ")
            return Result(lead_id, "duplicate", scored.priority, notified=bool(q.urgent))

        result = Result(lead_id, "created", scored.priority)
        if scored.priority == "spam":
            return result
        result.crm_synced = self._sync_crm(lead_id)
        result.notified = self._notify({**row, "id": lead_id})
        return result

    def _lead(self, lead_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    def _sync_crm(self, lead_id: int) -> bool:
        lead = dict(self._lead(lead_id))
        try:
            crm_id = self.crm.upsert_lead(lead)
        except Exception as err:
            log.warning("CRM a eșuat pentru lead %s: %s", lead_id, err)
            store.log_event(self.conn, lead_id, "crm_failed", utc(None), error=str(err)[:200])
            return False
        with self.conn:
            self.conn.execute("UPDATE leads SET crm_id = ? WHERE id = ?", (crm_id, lead_id))
        return True

    def _notify(self, lead: dict[str, Any], prefix: str = "") -> bool:
        try:
            self.notifier.send(prefix + lead_card(lead))
            return True
        except Exception as err:
            log.warning("notificarea a eșuat pentru lead %s: %s", lead["id"], err)
            store.log_event(self.conn, lead["id"], "notify_failed", utc(None), error=str(err)[:200])
            return False

    def retry_crm(self) -> int:
        """Reia sincronizarea pentru lead-urile care nu au ajuns în CRM. Întoarce câte au reușit."""
        rows = self.conn.execute("SELECT id FROM leads WHERE crm_id IS NULL AND duplicate_of IS NULL "
                                 "AND status != 'spam' ORDER BY id").fetchall()
        return sum(self._sync_crm(r["id"]) for r in rows)
