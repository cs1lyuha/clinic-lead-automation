"""Semnătura webhook-urilor: HMAC-SHA256 peste `timestamp.body`.

Antete așteptate:
    X-Leadflow-Timestamp: 1761350400
    X-Leadflow-Signature: sha256=<hex>

De ce timestamp-ul intră în semnătură: altfel cineva care a interceptat o
cerere validă o poate retrimite oricând (replay). Cu timestamp, cererile mai
vechi de `tolerance_s` sunt respinse. Același mecanism îl folosesc Stripe și Slack.
"""

from __future__ import annotations

import hashlib
import hmac
import time


class SignatureError(Exception):
    pass


def sign(secret: str, body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return ts, f"sha256={mac}"


def verify(secret: str, body: bytes, timestamp: str | None, signature: str | None, *,
           tolerance_s: int = 300, now: float | None = None) -> None:
    if not secret:
        raise SignatureError("secretul webhook-ului nu e configurat")
    if not timestamp or not signature:
        raise SignatureError("lipsesc antetele de semnătură")
    try:
        ts = int(timestamp)
    except ValueError:
        raise SignatureError("timestamp invalid") from None
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_s:
        raise SignatureError("cerere prea veche sau din viitor (posibil replay)")
    _, expected = sign(secret, body, ts)
    if not hmac.compare_digest(expected, signature.strip()):
        raise SignatureError("semnătură invalidă")
