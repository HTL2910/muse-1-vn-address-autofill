"""Validator — parent_code, không prefix, không reject default của khách."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from .province_anchor import AdminIndex
from .schemas import AddressContract
class Decision(str, Enum):
    OK="OK"; NEEDS_REVIEW="NEEDS_REVIEW"; BLOCKED="BLOCKED"
@dataclass
class ValidationError:
    code: str; field: str; message: str
@dataclass
class ValidationResult:
    decision: Decision; errors: list[ValidationError]=field(default_factory=list); warnings: list[ValidationError]=field(default_factory=list); confidence_penalty: float=0.0
def _haversine_km(lat1, lng1, lat2, lng2):
    r=math.radians; dlat=r(lat2-lat1); dlng=r(lng2-lng1)
    a=math.sin(dlat/2)**2+math.cos(r(lat1))*math.cos(r(lat2))*math.sin(dlng/2)**2
    return 6371.0*2*math.asin(math.sqrt(a))
class HierarchicalValidator:
    def __init__(self, index: Optional[AdminIndex]=None, carrier_servable_wards: Optional[set[str]]=None) -> None:
        self.index=index; self.carrier_servable_wards=carrier_servable_wards
    def validate(self, addr: AddressContract, preferred_carrier: str="SPX") -> ValidationResult:
        errors=[]; warnings=[]; penalty=0.0
        if not addr.province_code: errors.append(ValidationError("MISSING_PROVINCE","province_code","Missing province code."))
        if not addr.district_code: errors.append(ValidationError("MISSING_DISTRICT","district_code","Missing district code."))
        if not addr.ward_code: errors.append(ValidationError("MISSING_WARD","ward_code","Missing ward code."))
        if self.index is not None:
            d=self.index.units.get(addr.district_code or "")
            if d is not None and addr.province_code and d.parent_code!=addr.province_code: errors.append(ValidationError("DISTRICT_MISMATCH","district_code","District not in province."))
            w=self.index.units.get(addr.ward_code or "")
            if w is not None and addr.district_code and w.parent_code!=addr.district_code: errors.append(ValidationError("WARD_MISMATCH","ward_code","Ward not in district."))
            if addr.ward_code and addr.ward_code not in self.index.units: errors.append(ValidationError("WARD_NOT_IN_ADMIN_DB","ward_code","Ward code not in Canonical Admin DB."))
        if not addr.has_house_or_street:
            warnings.append(ValidationError("MISSING_HOUSE_OR_STREET","street","Missing house/street.")); penalty+=0.15
        if self.carrier_servable_wards is not None and addr.ward_code and addr.ward_code not in self.carrier_servable_wards:
            errors.append(ValidationError("CARRIER_NOT_SERVABLE","ward_code",f"Carrier {preferred_carrier} does not serve this ward."))
        if addr.lat is not None and addr.lng is not None and self.index is not None and addr.province_code:
            prov=self.index.units.get(addr.province_code)
            if prov and prov.centroid_lat is not None and prov.centroid_lng is not None:
                dist=_haversine_km(addr.lat,addr.lng,prov.centroid_lat,prov.centroid_lng)
                if dist>200: errors.append(ValidationError("GPS_PROVINCE_MISMATCH","lat_lng",f"GPS {dist:.0f}km from anchored province centroid.")); penalty+=0.35
        decision=Decision.BLOCKED if errors else (Decision.NEEDS_REVIEW if warnings or penalty>0 else Decision.OK)
        return ValidationResult(decision=decision,errors=errors,warnings=warnings,confidence_penalty=penalty)
