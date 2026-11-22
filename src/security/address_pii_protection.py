"""
Address PII protection - support module for fix_adapter_idempotency_audit_pii

- encrypt_pii / decrypt_pii: authenticated encryption for raw address
  text and phone-bearing fields before they are persisted in audit.
  Uses AES-GCM from `cryptography` when available; otherwise falls back
  to a stdlib HMAC-SHA256 counter-mode stream + HMAC tag construction
  (encrypt-then-MAC) so the module never stores plaintext just because
  an optional dependency is missing. Token format: enc:v1:<base64>.
  Key: argument > env ADDRESS_PII_KEY > process default derived key.
  Production MUST set ADDRESS_PII_KEY (32+ random bytes, base64/hex/text)
  in the secret manager; the default exists only so tests/dev never
  crash into plaintext storage.
- scrub_pii(obj): recursive copy masking PII-bearing keys
  (phone/tel/name/note/detail/street/address/raw_input/full_address...).
  Administrative codes (province_code/district_code/ward_code) and
  status/reason fields are NOT PII and are kept for dispute tracing.
- mask_phone / mask_text: display-safe previews.
- sha256_hex(value): stable fingerprint for lookup/dedup without
  exposing the value (used for raw_input_sha256 in audit).

Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

_TOKEN_PREFIX = "enc:v1:"
_PII_KEYS = {
    "phone", "customer_phone", "phone_number", "tel", "mobile",
    "name", "customer_name", "recipient_name", "note", "notes",
    "detail", "address_detail", "street", "address", "full_address",
    "raw_input", "raw_address", "email",
}


def _derive_key(key: Any = None) -> bytes:
    if key is None:
        key = os.environ.get("ADDRESS_PII_KEY", "")
    if isinstance(key, bytes):
        raw = key
    else:
        text = str(key or "")
        raw = b""
        for decoder in (lambda s: base64.b64decode(s, validate=True),
                        lambda s: bytes.fromhex(s)):
            try:
                raw = decoder(text)
                if len(raw) >= 16:
                    break
                raw = b""
            except Exception:
                raw = b""
        if not raw:
            raw = text.encode("utf-8") or b"dev-only-address-pii-key-change-me"
    return hashlib.sha256(raw).digest()


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def encrypt_pii(plaintext: Any, key: Any = None) -> str:
    """Encrypt text -> 'enc:v1:<base64(nonce|ct|tag)>'. '' stays ''."""
    text = "" if plaintext is None else str(plaintext)
    if not text:
        return ""
    raw_key = _derive_key(key)
    data = text.encode("utf-8")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        nonce = secrets.token_bytes(12)
        ct = AESGCM(raw_key).encrypt(nonce, data, None)  # ct includes tag
        return _TOKEN_PREFIX + base64.b64encode(b"A" + nonce + ct).decode("ascii")
    except Exception:
        nonce = secrets.token_bytes(16)
        stream = _keystream(raw_key, nonce, len(data))
        ct = bytes(a ^ b for a, b in zip(data, stream))
        tag = hmac.new(raw_key, b"tag" + nonce + ct, hashlib.sha256).digest()
        return _TOKEN_PREFIX + base64.b64encode(b"H" + nonce + ct + tag).decode("ascii")


def decrypt_pii(token: Any, key: Any = None) -> str:
    """Reverse encrypt_pii. Raises ValueError on tamper/wrong key.
    Non-token input is returned unchanged (legacy/plaintext tolerance
    for read paths only - new writes always encrypt)."""
    if not token:
        return ""
    text = str(token)
    if not text.startswith(_TOKEN_PREFIX):
        return text
    raw_key = _derive_key(key)
    blob = base64.b64decode(text[len(_TOKEN_PREFIX):])
    mode, rest = blob[:1], blob[1:]
    if mode == b"A":
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        return AESGCM(raw_key).decrypt(rest[:12], rest[12:], None).decode("utf-8")
    if mode == b"H":
        nonce, ct, tag = rest[:16], rest[16:-32], rest[-32:]
        expected = hmac.new(raw_key, b"tag" + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("PII ciphertext integrity check failed")
        stream = _keystream(raw_key, nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")
    raise ValueError("unknown PII token format")


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_TOKEN_PREFIX)


class PIIProtector:
    """Key-holding convenience wrapper used by stores/adapters."""

    def __init__(self, key: Any = None):
        self._key = key

    def encrypt(self, plaintext: Any) -> str:
        return encrypt_pii(plaintext, self._key)

    def decrypt(self, token: Any) -> str:
        return decrypt_pii(token, self._key)

    def scrub(self, obj: Any) -> Any:
        return scrub_pii(obj)


def mask_phone(phone: Any) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if len(digits) < 4:
        return "***" if digits else ""
    return "***" + digits[-3:]


def mask_text(text: Any) -> str:
    return "***" if str(text or "").strip() else ""


def scrub_pii(obj: Any) -> Any:
    """Recursive masked copy. Dict keys in _PII_KEYS are masked; lists,
    tuples and nested dicts are copied; everything else is kept."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _PII_KEYS:
                out[k] = mask_phone(v) if "phone" in str(k).lower() or str(k).lower() in ("tel", "mobile") else "***"
            else:
                out[k] = scrub_pii(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [scrub_pii(v) for v in obj]
    return obj
