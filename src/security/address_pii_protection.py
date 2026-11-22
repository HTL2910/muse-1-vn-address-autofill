"""
Bảo mật PII địa chỉ và SĐT, quản trị secret và audit log — approach_17.

- Địa chỉ chi tiết + SĐT là PII: mã hóa cột bằng AES-GCM (AESGCM),
  khóa lấy từ Secret Manager / Vault, KHÔNG hard-code, rotate định kỳ.
- Masking theo vai trò RBAC: CSKH chỉ thấy đủ để xác nhận,
  kho chỉ thấy địa chỉ đã chuẩn hóa, admin mới thấy đầy đủ (đã giải mã).
- Tuyệt đối không log API key Goong/Pancake/SPX; mọi secret chỉ đi
  qua Secret Manager, log phải qua RedactingFormatter.
- Webhook Pancake/SPX ký HMAC-SHA256 + verify timestamp (chống replay
  và giả mạo trạng thái hoàn đơn).
- Rate limiting theo shop/IP/token ở API Gateway.
- Audit log bất biến dạng hash-chain (JSONL), ghi raw_input đã mã hóa,
  provider_response, parsed_result, final_address, người override.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - CI cài cryptography
    AESGCM = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Redaction — không bao giờ để secret lọt vào log
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    re.compile(r"(goong[_-]?api[_-]?key\s*[:=]\s*)([^\s\"',}]+)", re.IGNORECASE),
    re.compile(r"(pancake[_-]?token\s*[:=]\s*)([^\s\"',}]+)", re.IGNORECASE),
    re.compile(r"(spx[_-]?api[_-]?key\s*[:=]\s*)([^\s\"',}]+)", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*['\"]?)([A-Za-z0-9_\-]{8,})", re.IGNORECASE),
    re.compile(r"(Authorization:\s*Bearer\s+)([^\s]+)", re.IGNORECASE),
    re.compile(r"(phone\s*[:=]\s*['\"]?)(0\d{9})", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Thay mọi secret / SĐT trong chuỗi log bằng ***. Dùng trước mọi logger."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1***", redacted)
    return redacted


class SecretManager(Protocol):
    def get_secret(self, name: str) -> str: ...


class EnvSecretManager:
    """Adapter tối thiểu cho Secret Manager/Vault; KHÔNG log giá trị trả về."""

    def __init__(self, prefix: str = "SECRET_") -> None:
        self._prefix = prefix

    def get_secret(self, name: str) -> str:
        value = os.environ.get(f"{self._prefix}{name}")
        if not value:
            raise RuntimeError(f"Secret not configured: {name} (value never logged)")
        return value


# ---------------------------------------------------------------------------
# Mã hóa cột AES-GCM với key rotation (key_id + envelope)
# ---------------------------------------------------------------------------
@dataclass
class EncryptedField:
    ciphertext_b64: str
    nonce_b64: str
    key_id: str
    aad: str = ""


def _b64encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _b64decode(data: str) -> bytes:
    import base64

    return base64.b64decode(data.encode("ascii"))


class PIIProtector:
    """
    Mã hóa phone và address_detail ở tầng cột.
    Khóa: 32 byte, lưu trong Secret Manager dưới tên PII_ENCRYPTION_KEY_<key_id>,
    rotate bằng cách thêm key_id mới và giải mã bằng key_id cũ.
    """

    def __init__(self, secrets: SecretManager, current_key_id: str = "k1") -> None:
        if AESGCM is None:
            raise RuntimeError("cryptography package is required for AES-GCM")
        self._secrets = secrets
        self.current_key_id = current_key_id

    def _key(self, key_id: str) -> bytes:
        raw = self._secrets.get_secret(f"PII_ENCRYPTION_KEY_{key_id}")
        # Khóa lưu dạng base64 32 byte; không log raw.
        key = _b64decode(raw) if len(raw) >= 40 else hashlib.sha256(raw.encode()).digest()
        if len(key) != 32:
            raise ValueError("PII encryption key must be 32 bytes")
        return key

    def encrypt(self, plaintext: str, aad: str = "") -> EncryptedField:
        nonce = secrets.token_bytes(12)
        aes = AESGCM(self._key(self.current_key_id))
        ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return EncryptedField(
            ciphertext_b64=_b64encode(ct),
            nonce_b64=_b64encode(nonce),
            key_id=self.current_key_id,
            aad=aad,
        )

    def decrypt(self, field: EncryptedField) -> str:
        aes = AESGCM(self._key(field.key_id))
        pt = aes.decrypt(
            _b64decode(field.nonce_b64),
            _b64decode(field.ciphertext_b64),
            field.aad.encode("utf-8"),
        )
        return pt.decode("utf-8")

    def rotate_key(self, new_key_id: str) -> None:
        # Kiểm tra key mới tồn tại trong Secret Manager trước khi chuyển.
        _ = self._key(new_key_id)
        self.current_key_id = new_key_id


# ---------------------------------------------------------------------------
# Masking theo vai trò RBAC
# ---------------------------------------------------------------------------
class Role(str, Enum):
    CUSTOMER_SERVICE = "cskh"
    WAREHOUSE = "kho"
    ADMIN = "admin"
    COURIER = "courier"


def mask_phone(phone: str, role: Role = Role.CUSTOMER_SERVICE) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return "***"
    if role == Role.ADMIN:
        return phone
    # CSKH/kho chỉ thấy 3 số đầu + 2 số cuối để xác nhận.
    return f"{digits[:3]}***{digits[-2:]}"


def mask_address_detail(detail: str, role: Role) -> str:
    if role == Role.ADMIN:
        return detail
    if role == Role.WAREHOUSE:
        # Kho chỉ thấy địa chỉ đã chuẩn hóa, không thấy ghi chú/SĐT trong detail.
        return re.sub(r"\d{10,11}", "***", detail or "")
    # CSKH: ẩn số nhà chi tiết, chỉ giữ đường + xã/huyện/tỉnh.
    parts = [p.strip() for p in (detail or "").split(",")]
    return ", ".join(parts[1:]) if len(parts) > 1 else "***"


def visible_address_for_role(address: dict[str, Any], role: Role) -> dict[str, Any]:
    return {
        **address,
        "phone": mask_phone(str(address.get("phone", "")), role),
        "address_detail": mask_address_detail(str(address.get("address_detail", "")), role),
    }


# ---------------------------------------------------------------------------
# Verify webhook HMAC + timestamp (Pancake / SPX)
# ---------------------------------------------------------------------------
def verify_webhook_signature(
    *,
    secret: str,
    payload: bytes,
    signature_header: str,
    timestamp_header: str,
    tolerance_seconds: int = 300,
) -> bool:
    """Chữ ký = HMAC-SHA256(secret, f"{timestamp}.{payload}"), hex."""
    try:
        ts = int(timestamp_header)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False  # chống replay
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    # So sánh thời gian hằng số để chống timing attack.
    return hmac.compare_digest(expected, signature_header.strip().lower())


# ---------------------------------------------------------------------------
# Rate limiting — token bucket theo shop / IP / token
# ---------------------------------------------------------------------------
@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Token bucket trong bộ nhớ; production thay bằng Redis với cùng API."""

    def __init__(self, rate_per_second: float = 10.0, burst: int = 50) -> None:
        self.rate = rate_per_second
        self.burst = burst
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), updated_at=now)
            self._buckets[key] = bucket
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.rate)
        bucket.updated_at = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True


# ---------------------------------------------------------------------------
# Audit log bất biến — hash chain, JSONL append-only
# ---------------------------------------------------------------------------
@dataclass
class AuditLog:
    path: Path
    _prev_hash: str = field(default="GENESIS", init=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > 0:
            last = self.path.read_text(encoding="utf-8").strip().splitlines()[-1]
            self._prev_hash = json.loads(last).get("hash", "GENESIS")

    def append(
        self,
        *,
        order_id: str,
        raw_input_encrypted: EncryptedField,
        provider_response: dict[str, Any],
        parsed_result: dict[str, Any],
        final_address: dict[str, Any],
        overridden_by: Optional[str] = None,
    ) -> dict[str, Any]:
        # Không ghi raw_input dạng plain text; chỉ ghi bản đã mã hóa + hash.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "order_id": order_id,
            "raw_input": {
                "ciphertext_b64": raw_input_encrypted.ciphertext_b64,
                "key_id": raw_input_encrypted.key_id,
                "sha256": hashlib.sha256(
                    raw_input_encrypted.ciphertext_b64.encode()
                ).hexdigest(),
            },
            "provider_response": provider_response,
            "parsed_result": parsed_result,
            "final_address": final_address,
            "overridden_by": overridden_by,
            "prev_hash": self._prev_hash,
        }
        payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
        record_hash = hashlib.sha256(
            (self._prev_hash + payload).encode("utf-8")
        ).hexdigest()
        record["hash"] = record_hash
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        self._prev_hash = record_hash
        return record

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for line in self.path.read_text(encoding="utf-8").strip().splitlines():
            record = json.loads(line)
            if record.get("prev_hash") != prev:
                return False
            stored = record.pop("hash")
            payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
            if hashlib.sha256((prev + payload).encode()).hexdigest() != stored:
                return False
            prev = stored
        return True
