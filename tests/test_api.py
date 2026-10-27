import json

import pytest
from fastapi.testclient import TestClient

from leadflow import store
from leadflow.api import create_app
from leadflow.integrations import MemoryCRM, MemoryNotifier
from leadflow.pipeline import LeadPipeline
from leadflow.security import sign

SECRET, KEY = "whsec_test", "key_test"


@pytest.fixture
def client():
    pipe = LeadPipeline(store.connect(), MemoryCRM(), MemoryNotifier())
    c = TestClient(create_app(pipe, webhook_secret=SECRET, api_key=KEY))
    c.pipe = pipe
    return c


def post_lead(client, payload, secret=SECRET, ts=None):
    body = json.dumps(payload).encode()
    t, sig = sign(secret, body, ts)
    return client.post("/webhooks/lead", content=body, headers={
        "Content-Type": "application/json", "X-Leadflow-Timestamp": t, "X-Leadflow-Signature": sig})


LEAD = {"delivery_id": "fb-100001", "name": "Олег Иванов", "phone": "079 555 111",
        "message": "Срочно, сильно болит зуб", "source": "facebook", "utm_campaign": "urgente-oct"}


def test_signed_webhook_creates_lead_and_alerts(client):
    r = post_lead(client, LEAD)
    assert r.status_code == 202 and r.json()["priority"] == "hot"
    assert "URGENȚĂ" in client.pipe.notifier.messages[0]


def test_unsigned_or_forged_webhook_is_rejected(client):
    assert client.post("/webhooks/lead", json=LEAD).status_code == 401
    assert post_lead(client, LEAD, secret="ghicit").status_code == 401
    assert post_lead(client, LEAD, ts=1_000_000).status_code == 401  # replay vechi
    assert client.pipe.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0


def test_invalid_payload_is_422_after_signature(client):
    assert post_lead(client, {"delivery_id": "x", "name": ""}).status_code == 422


def test_webhook_retry_is_idempotent(client):
    post_lead(client, LEAD)
    r = post_lead(client, LEAD)
    assert r.json()["status"] == "replayed"


def test_reception_endpoints_need_api_key(client):
    assert client.get("/leads").status_code == 401
    assert client.get("/leads", headers={"X-Api-Key": "gresit"}).status_code == 401


def test_reception_flow_and_report(client):
    post_lead(client, LEAD)
    post_lead(client, {**LEAD, "delivery_id": "web-000001", "phone": "068 000 111", "name": "Ana",
                       "message": "Cât costă detartrajul?", "source": "website"})
    h = {"X-Api-Key": KEY}
    leads = client.get("/leads", headers=h).json()
    assert leads[0]["urgent"] == 1  # urgențele primele
    assert client.post(f"/leads/{leads[0]['id']}/status", json={"status": "booked"}, headers=h).status_code == 200
    assert client.post("/leads/999/status", json={"status": "booked"}, headers=h).status_code == 404
    assert client.post(f"/leads/{leads[0]['id']}/status", json={"status": "vindecat"}, headers=h).status_code == 422
    rep = client.get("/report", headers=h).json()
    assert rep["leads"] == 2 and rep["funnel"]["booked"] == 1
    md = client.get("/report?format=md", headers=h).text
    assert md.startswith("# Raport lead-uri")


def test_health(client):
    assert client.get("/health").json() == {"ok": True}
