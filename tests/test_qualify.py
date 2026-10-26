import json
from types import SimpleNamespace as NS

import pytest

from leadflow.qualify import ClaudeQualifier, Qualification, RuleQualifier, detect_language, score

RQ = RuleQualifier()


@pytest.mark.parametrize("message,intent,urgent", [
    ("Mă doare măseaua de 2 zile, nu pot dormi", "urgenta", True),
    ("У меня сильно болит зуб и опухла щека", "urgenta", True),
    ("Aș vrea un implant, îmi lipsește un dinte din spate", "implant", False),
    ("Сколько стоят брекеты для взрослого?", "ortodontie", False),
    ("Vreau albire profesională înainte de nuntă", "estetica", False),
    ("Fiica mea are 6 ani, vrem un control", "copii", False),
    ("Detartraj și periaj profesional, vă rog", "igienizare", False),
    ("Bună ziua, aș vrea o programare", "consultatie", False),
    ("Best SEO services for your clinic http://spam.example", "spam", False),
])
def test_rule_intents_ro_and_ru(message, intent, urgent):
    q = RQ.qualify("Ana", message)
    assert (q.intent, q.urgent) == (intent, urgent)


def test_urgency_wins_over_other_intents():
    assert RQ.qualify("x", "Implantul pus luna trecută mă doare și e umflat").intent == "urgenta"


def test_language_detection():
    assert detect_language("Здравствуйте, болит зуб") == "ru"
    assert detect_language("Bună ziua") == "ro"
    assert detect_language("123") == "ro"


def test_scoring_and_priority():
    implant = score(RQ.qualify("Ion Rusu", "Vreau un implant mâine dacă se poate"),
                    has_phone=True, has_email=True, name="Ion Rusu", qualifier="rules")
    assert implant.score == 40 + 20 + 5 + 10 and implant.priority == "hot"
    assert implant.estimated_value == 15000
    assert implant.suggested_reply.startswith("Bună, Ion!")

    price = score(RQ.qualify("Ana", "Cât costă detartrajul?"), has_phone=False, has_email=True, name="Ana",
                  qualifier="rules")
    assert price.qualification.price_only and price.priority == "cold"
    assert "300 lei" in price.suggested_reply


def test_urgent_is_always_hot_and_reply_in_russian():
    s = score(RQ.qualify("Олег", "Срочно, болит зуб"), has_phone=False, has_email=False, name="Олег",
              qualifier="rules")
    assert s.priority == "hot" and s.suggested_reply.startswith("Здравствуйте, Олег!")


def test_spam_scores_zero():
    s = score(RQ.qualify("x", "crypto investment http://x"), has_phone=True, has_email=True, name="x",
              qualifier="rules")
    assert (s.score, s.priority, s.suggested_reply) == (0, "spam", "")


class FakeClient:
    def __init__(self, payload=None, error=None, stop="end_turn"):
        self.payload, self.error, self.stop, self.calls = payload, error, stop, 0
        self.messages = self

    def create(self, **params):
        self.calls += 1
        self.params = params
        if self.error:
            raise self.error
        return NS(stop_reason=self.stop, content=[NS(type="text", text=json.dumps(self.payload))])


def test_claude_understands_ambiguous_message():
    payload = {"intent": "urgenta", "urgent": True, "language": "ro", "price_only": False, "wants_time": True,
               "summary": "durere după coroană"}
    cq = ClaudeQualifier(client=FakeClient(payload))
    q = cq.qualify("Ana", "de când mi-au pus coroana nu pot mesteca pe partea aia")
    assert q.intent == "urgenta" and cq.fallbacks_used == 0
    schema = cq.client.params["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("client", [
    FakeClient(error=ConnectionError("rețea")),
    FakeClient(payload={"intent": "diagnostic", "urgent": True}),   # schemă încălcată
    FakeClient(payload={}, stop="refusal"),
])
def test_claude_failure_falls_back_to_rules(client):
    cq = ClaudeQualifier(client=client)
    q = cq.qualify("Ana", "Mă doare dintele")
    assert q.intent == "urgenta" and cq.fallbacks_used == 1


def test_qualification_model_rejects_unknown_intent():
    with pytest.raises(Exception):
        Qualification(intent="diagnostic", urgent=False, language="ro")
