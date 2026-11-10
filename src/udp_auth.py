import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any


class UdpAuthenticationError(ValueError):
    pass


class UdpAuthenticator:
    def __init__(self, secret: str, max_age_seconds: int = 30) -> None:
        if max_age_seconds < 1:
            raise ValueError("max_age_seconds must be positive")
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("UDP_HMAC_SECRET must contain at least 32 bytes")
        self._secret = secret.encode("utf-8")
        self._max_age_seconds = max_age_seconds
        self._seen_nonces: dict[str, int] = {}
        self._lock = threading.Lock()

    def seal(self, payload: bytes) -> bytes:
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(16)
        signature = self._sign(payload, timestamp, nonce)
        envelope = {
            "payload": base64.b64encode(payload).decode("ascii"),
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
        }
        return json.dumps(envelope, separators=(",", ":")).encode("utf-8")

    def open(self, datagram: bytes) -> bytes:
        try:
            envelope: Any = json.loads(datagram)
            encoded_payload = envelope["payload"]
            timestamp = envelope["timestamp"]
            nonce = envelope["nonce"]
            signature = envelope["signature"]
            if (
                not isinstance(encoded_payload, str)
                or not isinstance(timestamp, int)
                or isinstance(timestamp, bool)
                or not isinstance(nonce, str)
                or not nonce
                or not nonce.isascii()
                or not isinstance(signature, str)
                or not signature.isascii()
                or len(signature) != hashlib.sha256().digest_size * 2
            ):
                raise ValueError("Invalid envelope fields")
            payload = base64.b64decode(encoded_payload, validate=True)
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise UdpAuthenticationError(
                "Malformed authenticated UDP message"
            ) from error

        expected_signature = self._sign(payload, timestamp, nonce)
        if not hmac.compare_digest(signature, expected_signature):
            raise UdpAuthenticationError("Invalid UDP message signature")

        now = int(time.time())
        if abs(now - timestamp) > self._max_age_seconds:
            raise UdpAuthenticationError("Expired UDP message")

        with self._lock:
            cutoff = now - self._max_age_seconds
            self._seen_nonces = {
                seen_nonce: seen_at
                for seen_nonce, seen_at in self._seen_nonces.items()
                if seen_at >= cutoff
            }
            if nonce in self._seen_nonces:
                raise UdpAuthenticationError("Replayed UDP message")
            self._seen_nonces[nonce] = timestamp

        return payload

    def _sign(self, payload: bytes, timestamp: int, nonce: str) -> str:
        signed_data = (
            str(timestamp).encode("ascii")
            + b"."
            + nonce.encode("ascii")
            + b"."
            + payload
        )
        return hmac.new(self._secret, signed_data, hashlib.sha256).hexdigest()
