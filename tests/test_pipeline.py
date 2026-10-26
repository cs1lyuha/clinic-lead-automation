from datetime import timedelta

from conftest import T0

from leadflow import store


def test_new_lead_goes_to_crm_and_telegram(pipe, lead):
    res = pipe.process(lead(message="Vreau un implant, îmi lipsește un dinte"))
    assert res.status == "created" and res.crm_synced and res.notified
    row = pipe.conn.execute("SELECT * FROM leads").fetchone()
    assert row["phone"] == "+37369123456" and row["name"] == "Ana Popescu"
    assert row["crm_id"] == "c1"
    assert pipe.crm.deals[0] == {"contact": "c1", "stage": "appointmentscheduled", "intent": "implant",
                                 "amount": 15000}
    assert "🔥 Lead nou #1 · implant" in pipe.notifier.messages[0]


def test_retried_delivery_is_not_processed_twice(pipe, lead):
    payload = lead()
    first, second = pipe.process(payload), pipe.process(payload)
    assert second.status == "replayed" and second.lead_id == first.lead_id
    assert pipe.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    assert len(pipe.notifier.messages) == 1 and len(pipe.crm.deals) == 1


def test_same_person_within_30_days_is_a_duplicate(pipe, lead):
    first = pipe.process(lead())
    again = pipe.process(lead(phone="+37369123456", email=None, created_at=T0 + timedelta(days=3)))
    assert again.status == "duplicate"
    dup = pipe.conn.execute("SELECT duplicate_of FROM leads WHERE id = ?", (again.lead_id,)).fetchone()[0]
    assert dup == first.lead_id
    events = [r["event"] for r in pipe.conn.execute("SELECT event FROM lead_events WHERE lead_id = ?",
                                                     (first.lead_id,))]
    assert "repeat_contact" in events
    assert len(pipe.crm.deals) == 1  # fără deal dublu


def test_after_30_days_it_is_a_new_lead(pipe, lead):
    pipe.process(lead())
    assert pipe.process(lead(created_at=T0 + timedelta(days=31))).status == "created"


def test_returning_patient_in_pain_still_triggers_alert(pipe, lead):
    pipe.process(lead())
    res = pipe.process(lead(message="Mă doare foarte tare, e umflat", created_at=T0 + timedelta(days=1)))
    assert res.status == "duplicate" and res.notified
    assert "revine, vezi #1" in pipe.notifier.messages[-1] and "URGENȚĂ" in pipe.notifier.messages[-1]


def test_spam_is_stored_but_not_sent_anywhere(pipe, lead):
    res = pipe.process(lead(message="SEO backlinks for your clinic http://x.example"))
    assert res.priority == "spam"
    assert pipe.crm.deals == [] and pipe.notifier.messages == []


def test_crm_down_lead_is_kept_and_retried(pipe, lead):
    pipe.crm.fail = True
    res = pipe.process(lead())
    assert res.status == "created" and not res.crm_synced and res.notified  # recepția tot află
    events = [r["event"] for r in pipe.conn.execute("SELECT event FROM lead_events")]
    assert "crm_failed" in events
    pipe.crm.fail = False
    assert pipe.retry_crm() == 1
    assert pipe.conn.execute("SELECT crm_id FROM leads").fetchone()[0] == "c1"


def test_telegram_down_does_not_lose_lead(pipe, lead):
    class Broken:
        def send(self, text):
            raise TimeoutError("telegram")
    pipe.notifier = Broken()
    res = pipe.process(lead())
    assert res.status == "created" and not res.notified and res.crm_synced


def test_invalid_phone_keeps_lead_without_phone(pipe, lead):
    res = pipe.process(lead(phone="123", email="x@y.md"))
    row = pipe.conn.execute("SELECT phone, score FROM leads WHERE id = ?", (res.lead_id,)).fetchone()
    assert row["phone"] is None


def test_status_changes_are_logged(pipe, lead):
    res = pipe.process(lead())
    row = store.set_status(pipe.conn, res.lead_id, "booked", T0 + timedelta(minutes=12))
    assert row["status"] == "booked" and row["contacted_at"].startswith("2026-10-19T09:12")
