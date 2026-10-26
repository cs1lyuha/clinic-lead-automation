import pytest
from pydantic import ValidationError

from leadflow.normalize import InvalidPhone, LeadIn, normalize_name, normalize_phone


@pytest.mark.parametrize("raw", ["069 123 456", "+373 69123456", "0037369123456", "69-12-34-56", "(069) 12-34-56"])
def test_moldovan_phone_formats(raw):
    assert normalize_phone(raw) == "+37369123456"


def test_landline_and_foreign_numbers():
    assert normalize_phone("022 21 21 21") == "+37322212121"
    assert normalize_phone("+40 721 234 567") == "+40721234567"
    assert normalize_phone(None) is None


@pytest.mark.parametrize("raw", ["123", "abc", "0691234"])
def test_garbage_phone_is_rejected(raw):
    with pytest.raises(InvalidPhone):
        normalize_phone(raw)


def test_names():
    assert normalize_name("  ANA   popescu ") == "Ana Popescu"
    assert normalize_name("Ion McDonald") == "Ion McDonald"  # nu stricăm majusculele intenționate


def test_lead_payload_validation():
    lead = LeadIn(delivery_id="fb-000123", name="Ana", email="Ana@Mail.MD", source="FACEBOOK")
    assert lead.source == "facebook"
    assert LeadIn(delivery_id="x-000001", name="A", source="tiktok").source == "website"
    with pytest.raises(ValidationError):
        LeadIn(delivery_id="x-000001", name="A", email="nu-e-email")
    with pytest.raises(ValidationError):
        LeadIn(delivery_id="x", name="A")
