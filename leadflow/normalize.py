"""Normalizarea lead-urilor care vin din surse diferite (site, Facebook Lead Ads, Instagram, telefon).

Telefoanele din Moldova vin în zeci de forme: „069 123 456”, „+373 69123456”,
„0037369123456”, „69-12-34-56”. Ca deduplicarea să meargă, toate devin E.164: +37369123456.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field, field_validator

SOURCES = {"website", "facebook", "instagram", "google_ads", "phone", "referral"}


class LeadIn(BaseModel):
    """Payload-ul webhook-ului, așa cum îl trimite formularul / n8n / Zapier."""

    delivery_id: str = Field(min_length=6, max_length=80)  # unic per livrare; retry-urile îl păstrează
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    message: str = Field(default="", max_length=2000)
    source: str = "website"
    utm_campaign: str | None = Field(default=None, max_length=120)
    created_at: datetime | None = None

    @field_validator("source")
    @classmethod
    def known_source(cls, v: str) -> str:
        v = v.lower().strip()
        return v if v in SOURCES else "website"


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("373") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 9:      # 069123456
        return "+373" + digits[1:]
    if len(digits) == 8 and digits[0] in "2345678":     # 69123456
        return "+373" + digits
    if len(digits) >= 10 and not digits.startswith("373"):  # număr străin, deja cu prefix de țară
        return "+" + digits
    raise InvalidPhone(f"număr de telefon nerecunoscut: {raw!r}")


def normalize_name(raw: str) -> str:
    parts = [p for p in re.split(r"\s+", raw.strip()) if p]
    return " ".join(p[:1].upper() + p[1:].lower() if p.isupper() or p.islower() else p for p in parts)


def utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
