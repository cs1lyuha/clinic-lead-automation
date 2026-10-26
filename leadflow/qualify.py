"""Calificarea lead-ului: intenție, urgență, scor, prioritate, valoare estimată, răspuns sugerat.

- `RuleQualifier`: cuvinte-cheie în română ȘI rusă (în Moldova pacienții scriu în ambele limbi), determinist.
- `ClaudeQualifier`: înțelege mesaje ambigue („mă doare de 3 zile după ce mi-au pus o coroană”),
  output JSON strict. Dacă API-ul pică sau răspunsul e invalid → fallback pe reguli: un lead
  nu se pierde niciodată din cauza AI-ului.

Prioritatea și valoarea estimată se calculează în cod, din intenție și scor, nu le „ghicește” modelul.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("leadflow.qualify")

Intent = Literal["urgenta", "implant", "ortodontie", "estetica", "igienizare", "copii", "consultatie", "spam"]

KEYWORDS: dict[str, list[str]] = {
    "urgenta": ["durere", "doare", "umflat", "umflătur", "abces", "urgent", "nu pot dormi", "sângerează",
                "болит", "флюс", "опух", "срочно", "острая боль"],
    "implant": ["implant", "lipsă dinte", "lipsesc dinți", "имплант", "нет зуба"],
    "ortodontie": ["aparat dentar", "ortodont", "bracket", "gutier", "aligner", "dinți strâmbi",
                   "брекет", "элайнер", "ортодонт", "кривые зубы"],
    "estetica": ["albire", "fațet", "zâmbet", "отбелива", "винир", "улыбк"],
    "igienizare": ["detartraj", "igienizare", "curățare", "periaj profesional", "чистк", "гигиен", "камень"],
    "copii": ["copil", "fiul", "fiica", "fetița", "băiatul", "ребен", "ребён", "сын", "дочь", "детск"],
}
TIME_WORDS = ["azi", "astăzi", "mâine", "diseară", "săptămâna asta", "сегодня", "завтра", "на этой неделе"]
PRICE_WORDS = ["preț", "pret", "cât costă", "cat costa", "tarif", "сколько стоит", "цена", "стоимость"]
SPAM_PATTERNS = [r"https?://", r"\bseo\b", r"crypto", r"bitcoin", r"backlink", r"casino", r"заработ"]

BASE_SCORE = {"implant": 40, "ortodontie": 35, "estetica": 30, "urgenta": 30, "copii": 20,
              "igienizare": 15, "consultatie": 15, "spam": 0}
AVG_TICKET = {"implant": 15000, "ortodontie": 25000, "estetica": 8000, "urgenta": 1200, "copii": 800,
              "igienizare": 600, "consultatie": 300, "spam": 0}  # lei, bon mediu pe tratament

REPLIES = {
    "ro": {
        "urgenta": "Bună, {name}! Ne pare rău că vă doare. Avem locuri pentru urgențe chiar azi — vă sunăm în câteva minute.",
        "default": "Bună, {name}! Mulțumim pentru mesaj. Un coordonator vă sună în cel mult o oră lucrătoare "
                   "pentru o programare la consultație.",
        "pret": "Bună, {name}! Prețul exact depinde de consultație (300 lei, se scade din tratament). "
                "Vă putem programa chiar săptămâna aceasta.",
    },
    "ru": {
        "urgenta": "Здравствуйте, {name}! Сочувствуем. У нас есть места для срочных случаев сегодня — перезвоним через несколько минут.",
        "default": "Здравствуйте, {name}! Спасибо за обращение. Координатор перезвонит вам в течение часа "
                   "и запишет на консультацию.",
        "pret": "Здравствуйте, {name}! Точная стоимость зависит от консультации (300 лей, вычитается из лечения). "
                "Можем записать вас на этой неделе.",
    },
}


class Qualification(BaseModel):
    intent: Intent
    urgent: bool
    language: Literal["ro", "ru"]
    price_only: bool = False
    wants_time: bool = False
    summary: str = Field(default="", max_length=300)


class Scored(BaseModel):
    qualification: Qualification
    score: int
    priority: Literal["hot", "warm", "cold", "spam"]
    estimated_value: int
    suggested_reply: str
    qualifier: str


class Qualifier(Protocol):
    name: str

    def qualify(self, name: str, message: str) -> Qualification: ...


def detect_language(text: str) -> Literal["ro", "ru"]:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "ro"
    cyrillic = sum("Ѐ" <= c <= "ӿ" for c in letters)
    return "ru" if cyrillic / len(letters) > 0.3 else "ro"


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


class RuleQualifier:
    name = "rules"

    def qualify(self, name: str, message: str) -> Qualification:
        text = message.lower()
        lang = detect_language(message)
        if any(re.search(p, text) for p in SPAM_PATTERNS):
            return Qualification(intent="spam", urgent=False, language=lang, summary="mesaj promoțional / spam")
        urgent = _has(text, KEYWORDS["urgenta"])
        intent = "urgenta" if urgent else "consultatie"
        for candidate in ("implant", "ortodontie", "estetica", "copii", "igienizare"):
            if _has(text, KEYWORDS[candidate]):
                intent = candidate if not urgent else "urgenta"
                break
        return Qualification(intent=intent, urgent=urgent, language=lang,
                             price_only=_has(text, PRICE_WORDS) and len(text.split()) < 12,
                             wants_time=_has(text, TIME_WORDS), summary=message[:120])


SYSTEM = """Ești asistentul de recepție al clinicii stomatologice Zâmbet Sănătos din Chișinău.
Clasifici mesajele pacienților (în română sau rusă). Câmpuri:
- intent: urgenta (durere, umflătură, sângerare, traumă) | implant | ortodontie | estetica | igienizare | copii | consultatie | spam
- urgent: true doar pentru durere acută, umflătură, sângerare, traumă
- language: limba mesajului (ro / ru)
- price_only: întreabă doar de preț, fără să ceară programare
- wants_time: cere o oră anume sau „azi/mâine”
- summary: o propoziție în română pentru recepție
Nu pune diagnostice și nu promite prețuri."""


class ClaudeQualifier:
    name = "claude"

    def __init__(self, model: str = "claude-haiku-5-5", client: Any = None, fallback: Qualifier | None = None) -> None:
        if client is None:  # pragma: no cover - doar live
            import anthropic
            client = anthropic.Anthropic(max_retries=2, timeout=20.0)
        self.client = client
        self.model = model
        self.fallback = fallback or RuleQualifier()
        self.fallbacks_used = 0

    def qualify(self, name: str, message: str) -> Qualification:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": _schema()}, "effort": "low"},
                messages=[{"role": "user", "content": f"Pacient: {name}\nMesaj: {message}"}],
            )
            if response.stop_reason != "end_turn":
                raise ValueError(f"stop_reason={response.stop_reason}")
            text = next(b.text for b in response.content if b.type == "text")
            return Qualification.model_validate(json.loads(text))
        except (ValidationError, ValueError, StopIteration, json.JSONDecodeError) as err:
            log.warning("răspuns AI invalid, folosesc regulile: %s", err)
        except Exception as err:  # rețea, 5xx, rate limit după retry-urile SDK-ului
            log.warning("AI indisponibil, folosesc regulile: %s", err)
        self.fallbacks_used += 1
        return self.fallback.qualify(name, message)


def _schema() -> dict:
    schema = Qualification.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"])
    return schema


def score(q: Qualification, *, has_phone: bool, has_email: bool, name: str, qualifier: str) -> Scored:
    if q.intent == "spam":
        return Scored(qualification=q, score=0, priority="spam", estimated_value=0, suggested_reply="",
                      qualifier=qualifier)
    s = BASE_SCORE[q.intent] + (20 if has_phone else 0) + (5 if has_email else 0)
    s += 15 if q.urgent else 0
    s += 10 if q.wants_time else 0
    s -= 10 if q.price_only else 0
    s = max(0, min(100, s))
    priority = "hot" if q.urgent or s >= 60 else "warm" if s >= 35 else "cold"
    first = name.split()[0] if name else ""
    templates = REPLIES[q.language]
    key = "urgenta" if q.urgent else "pret" if q.price_only else "default"
    return Scored(qualification=q, score=s, priority=priority, estimated_value=AVG_TICKET[q.intent],
                  suggested_reply=templates[key].format(name=first), qualifier=qualifier)
