"""Gateway facade mỏng — chỉ gọi AddressService, mapping chuẩn."""
from __future__ import annotations
from typing import Any, Optional
from ..domain.schemas import AddressStatus
from ..services.address_service import AddressService
class AddressGateway:
    def __init__(self, service: Optional[AddressService]=None) -> None:
        self._service=service or AddressService()
    def resolve_address(self, raw_input: str, carrier: str="SPX") -> dict[str, Any]:
        result=self._service.resolve(raw_input,carrier=carrier); contract=result.contract
        status="VERIFIED" if contract.status==AddressStatus.VERIFIED else ("NEED_CONFIRMATION" if contract.status==AddressStatus.NEEDS_REVIEW else "REJECTED_INSUFFICIENT")
        return {"status":status,"confidence_score":round(contract.confidence,4),"address":contract.to_dict(),"reason_code":contract.reason_code.value,"stage_reached":result.stage_reached.value,"is_shippable":contract.is_shippable}
    def autofill(self, raw_input: str, carrier: str="SPX") -> dict[str, Any]: return self.resolve_address(raw_input,carrier=carrier)
