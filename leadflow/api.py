"""API-ul HTTP.

    POST /webhooks/lead          webhook semnat (formular site, n8n, Zapier, Facebook Lead Ads prin n8n)
    GET  /leads?status=new       lista pentru recepție (cere X-Api-Key)
    POST /leads/{id}/status      recepția marchează: contacted / booked / attended / no_show / lost
    GET  /report?start=&end=     raportul pentru manager (JSON sau ?format=md)
    GET  /health

Configurare prin variabile de mediu: LEADFLOW_WEBHOOK_SECRET, LEADFLOW_API_KEY, LEADFLOW_DB,
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, HUBSPOT_TOKEN, ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import hmac
import os
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ValidationError

from leadflow import report, store
from leadflow.integrations import HubSpotCRM, MemoryCRM, MemoryNotifier, TelegramNotifier
from leadflow.normalize import LeadIn
from leadflow.pipeline import LeadPipeline
from leadflow.qualify import ClaudeQualifier, RuleQualifier
from leadflow.security import SignatureError, verify


class StatusIn(BaseModel):
    status: Literal["contacted", "booked", "attended", "no_show", "lost", "spam"]


def default_pipeline() -> LeadPipeline:  # pragma: no cover - configurația de producție
    conn = store.connect(os.environ.get("LEADFLOW_DB", "leadflow.db"))
    crm = HubSpotCRM() if os.environ.get("HUBSPOT_TOKEN") else MemoryCRM()
    notifier = TelegramNotifier() if os.environ.get("TELEGRAM_BOT_TOKEN") else MemoryNotifier()
    qualifier = ClaudeQualifier() if os.environ.get("ANTHROPIC_API_KEY") else RuleQualifier()
    return LeadPipeline(conn, crm, notifier, qualifier)


def create_app(pipeline: LeadPipeline | None = None, *, webhook_secret: str | None = None,
               api_key: str | None = None) -> FastAPI:
    pipe = pipeline or default_pipeline()
    secret = webhook_secret if webhook_secret is not None else os.environ.get("LEADFLOW_WEBHOOK_SECRET", "")
    key = api_key if api_key is not None else os.environ.get("LEADFLOW_API_KEY", "")
    app = FastAPI(title="Leadflow — clinica Zâmbet Sănătos", version="0.1.0")

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if not key or not x_api_key or not hmac.compare_digest(key, x_api_key):
            raise HTTPException(401, "X-Api-Key invalid")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/webhooks/lead", status_code=202)
    async def webhook(request: Request,
                      x_leadflow_timestamp: str | None = Header(default=None),
                      x_leadflow_signature: str | None = Header(default=None)) -> dict:
        body = await request.body()  # semnătura se verifică pe bytes-ii exacți, înainte de parsare
        try:
            verify(secret, body, x_leadflow_timestamp, x_leadflow_signature)
        except SignatureError as err:
            raise HTTPException(401, str(err)) from None
        try:
            payload = LeadIn.model_validate_json(body)
        except ValidationError as err:
            raise HTTPException(422, err.errors(include_url=False, include_context=False)) from None
        res = pipe.process(payload)
        return {"lead_id": res.lead_id, "status": res.status, "priority": res.priority}

    @app.get("/leads", dependencies=[Depends(require_key)])
    def leads(status: str | None = None, limit: int = 50) -> list[dict]:
        sql, params = ("SELECT id, name, phone, email, intent, urgent, priority, score, status, suggested_reply, "
                       "created_at FROM leads WHERE duplicate_of IS NULL"), []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY urgent DESC, score DESC, id LIMIT ?"
        params.append(min(limit, 200))
        return [dict(r) for r in pipe.conn.execute(sql, params)]

    @app.post("/leads/{lead_id}/status", dependencies=[Depends(require_key)])
    def set_status(lead_id: int, body: StatusIn) -> dict:
        try:
            row = store.set_status(pipe.conn, lead_id, body.status, datetime.now(timezone.utc))
        except KeyError:
            raise HTTPException(404, "lead inexistent") from None
        return {"id": row["id"], "status": row["status"]}

    @app.get("/report", dependencies=[Depends(require_key)])
    def get_report(start: date | None = None, end: date | None = None, format: str = "json"):
        end = end or date.today()
        start = start or end - timedelta(days=6)
        rep = report.build(pipe.conn, start, end)
        return PlainTextResponse(report.to_markdown(rep)) if format == "md" else rep

    return app
