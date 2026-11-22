"""Address Gateway — facade mỏng, canonical cho fix_missing_pipeline_core.

Đầu mối duy nhất cho app và Pancake. Không chứa logic pipeline,
không gọi provider, chỉ gọi AddressService và mapping trạng thái
chuẩn VERIFIED / NEED_CONFIRMATION / REJECTED_INSUFFICIENT.
"""
from __future__ import annotations

from typing import Any

from ..domain.schemas import AddressStatus
from ..services.address_service import AddressService


class AddressGateway:
    def __init__(self, service: AddressService) -> None:
        self._service = service

    def resolve_address(self, raw_input: str, carrier: str = "SPX") -> dict[str, Any]:
        result = self._service.resolve(raw_input, carrier=carrier)
        contract = result.contract
        if contract.status == AddressStatus.VERIFIED:
            status = "VERIFIED"
        elif contract.status == AddressStatus.NEEDS_REVIEW:
            status = "NEED_CONFIRMATION"
        else:
            status = "REJECTED_INSUFFICIENT"
        return {
            "status": status,
            "confidence_score": round(contract.confidence, 4),
            "address": contract.to_dict(),
            "reason_code": contract.reason_code.value,
            "stage_reached": result.stage_reached.value,
            "is_shippable": contract.is_shippable,
        }

    def autofill(self, raw_input: str, carrier: str = "SPX") -> dict[str, Any]:
        return self.resolve_address(raw_input, carrier=carrier)
