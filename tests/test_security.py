import pytest

from leadflow.security import SignatureError, sign, verify

BODY = b'{"name": "Ana"}'


def test_valid_signature_passes():
    ts, sig = sign("s3cret", BODY, 1_000_000)
    verify("s3cret", BODY, ts, sig, now=1_000_010)


@pytest.mark.parametrize("secret,body,now", [
    ("alt-secret", BODY, 1_000_010),          # secret greșit
    ("s3cret", b'{"name": "Eva"}', 1_000_010),  # body modificat
    ("s3cret", BODY, 1_000_000 + 301),        # replay după 5 minute
    ("s3cret", BODY, 1_000_000 - 301),        # timestamp din viitor
])
def test_tampering_and_replay_are_rejected(secret, body, now):
    ts, sig = sign("s3cret", BODY, 1_000_000)
    with pytest.raises(SignatureError):
        verify(secret, body, ts, sig, now=now)


@pytest.mark.parametrize("ts,sig", [(None, "sha256=x"), ("123", None), ("abc", "sha256=x")])
def test_missing_or_malformed_headers(ts, sig):
    with pytest.raises(SignatureError):
        verify("s3cret", BODY, ts, sig, now=123)


def test_empty_secret_is_a_configuration_error():
    with pytest.raises(SignatureError, match="nu e configurat"):
        verify("", BODY, "1", "sha256=x", now=1)
