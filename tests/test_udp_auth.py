import base64
import json

import pytest

import udp_auth
from udp_auth import UdpAuthenticationError, UdpAuthenticator

SECRET = "local-demo-hmac-secret-change-before-deploy-0123456789"


def test_authenticated_payload_round_trip() -> None:
    sender = UdpAuthenticator(SECRET)
    receiver = UdpAuthenticator(SECRET)
    payload = b'{"alert_id":"test"}'

    assert receiver.open(sender.seal(payload)) == payload


def test_rejects_modified_payload() -> None:
    sender = UdpAuthenticator(SECRET)
    receiver = UdpAuthenticator(SECRET)
    envelope = json.loads(sender.seal(b'{"quantity":1}'))
    envelope["payload"] = base64.b64encode(b'{"quantity":9}').decode("ascii")

    with pytest.raises(UdpAuthenticationError, match="signature"):
        receiver.open(json.dumps(envelope).encode("utf-8"))


def test_rejects_expired_message(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000]
    monkeypatch.setattr(udp_auth.time, "time", lambda: now[0])
    sender = UdpAuthenticator(SECRET)
    receiver = UdpAuthenticator(SECRET, max_age_seconds=1)
    datagram = sender.seal(b"order")
    now[0] += 2

    with pytest.raises(UdpAuthenticationError, match="Expired"):
        receiver.open(datagram)


def test_rejects_replayed_message() -> None:
    sender = UdpAuthenticator(SECRET)
    receiver = UdpAuthenticator(SECRET)
    datagram = sender.seal(b"order")

    assert receiver.open(datagram) == b"order"
    with pytest.raises(UdpAuthenticationError, match="Replayed"):
        receiver.open(datagram)


def test_requires_a_32_byte_secret() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        UdpAuthenticator("too-short")
