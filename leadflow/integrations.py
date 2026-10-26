"""CRM și notificări. Fiecare are o variantă în memorie (teste, demo) și una reală.

CRM-ul real e HubSpot (API v3): contactul se caută după e-mail sau telefon. Dacă există,
e actualizat, altfel e creat; apoi se creează un deal în pipeline, cu etapa după prioritate.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class CRM(Protocol):
    def upsert_lead(self, lead: dict[str, Any]) -> str: ...


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


@dataclass
class MemoryCRM:
    contacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    deals: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    def upsert_lead(self, lead: dict[str, Any]) -> str:
        if self.fail:
            raise ConnectionError("CRM indisponibil")
        key = lead.get("email") or lead.get("phone") or f"lead-{lead['id']}"
        contact = self.contacts.setdefault(key, {"id": f"c{len(self.contacts) + 1}", "leads": 0})
        contact.update({"name": lead["name"], "phone": lead.get("phone"), "email": lead.get("email")})
        contact["leads"] += 1
        self.deals.append({"contact": contact["id"], "stage": STAGE[lead["priority"]], "intent": lead["intent"],
                           "amount": lead["estimated_value"]})
        return contact["id"]


STAGE = {"hot": "appointmentscheduled", "warm": "qualifiedtobuy", "cold": "presentationscheduled",
         "spam": "closedlost"}


@dataclass
class HubSpotCRM:  # pragma: no cover - cere cont HubSpot (private app token)
    token: str = field(default_factory=lambda: os.environ.get("HUBSPOT_TOKEN", ""))
    base: str = "https://api.hubapi.com"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.base}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read() or b"{}")

    def _find(self, prop: str, value: str | None) -> str | None:
        if not value:
            return None
        res = self._call("POST", "/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}], "limit": 1})
        results = res.get("results") or []
        return results[0]["id"] if results else None

    def upsert_lead(self, lead: dict[str, Any]) -> str:
        first, _, last = lead["name"].partition(" ")
        props = {"firstname": first, "lastname": last, "phone": lead.get("phone") or "",
                 "email": lead.get("email") or "", "hs_lead_status": "NEW"}
        contact_id = self._find("email", lead.get("email")) or self._find("phone", lead.get("phone"))
        if contact_id:
            self._call("PATCH", f"/crm/v3/objects/contacts/{contact_id}", {"properties": props})
        else:
            contact_id = self._call("POST", "/crm/v3/objects/contacts", {"properties": props})["id"]
        deal = self._call("POST", "/crm/v3/objects/deals", {"properties": {
            "dealname": f"{lead['intent']} — {lead['name']}", "pipeline": "default",
            "dealstage": STAGE[lead["priority"]], "amount": str(lead["estimated_value"])}})
        self._call("PUT", f"/crm/v4/objects/deals/{deal['id']}/associations/default/contacts/{contact_id}")
        return contact_id


@dataclass
class MemoryNotifier:
    messages: list[str] = field(default_factory=list)

    def send(self, text: str) -> None:
        self.messages.append(text)


@dataclass
class TelegramNotifier:  # pragma: no cover - cere bot real
    token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))

    def send(self, text: str) -> None:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage", method="POST",
            data=json.dumps({"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()


def lead_card(lead: dict[str, Any]) -> str:
    icon = {"hot": "🔥", "warm": "🟡", "cold": "🔵"}.get(lead["priority"], "•")
    lines = [f"{icon} Lead nou #{lead['id']} · {lead['intent']} · scor {lead['score']}",
             f"{lead['name']} · {lead.get('phone') or '-'} · {lead.get('email') or '-'}",
             f"Sursă: {lead['source']}" + (f" / {lead['utm_campaign']}" if lead.get("utm_campaign") else ""),
             f"„{lead['message'][:200]}”",
             f"Răspuns sugerat: {lead['suggested_reply']}"]
    if lead["urgent"]:
        lines.insert(1, "⚠️ URGENȚĂ — sunați acum")
    return "\n".join(lines)
