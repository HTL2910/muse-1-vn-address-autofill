"""Observability — run_golden_regression tính theo CASE, không theo lượt trúng.
Fix dead code: không còn tên chưa định nghĩa, không ghi đè lock.
"""
from __future__ import annotations
import json, threading
from pathlib import Path
FORBIDDEN_DEFAULT={"ward_name":"Đăk Yă","district_name":"Mang Yang","province_name":"Gia Lai"}
class Observability:
    def __init__(self, region_lock=None):
        self.region_lock=region_lock if region_lock is not None else threading.Lock()
    def run_golden_regression(self, run_one, cases=None):
        return run_golden_regression(run_one, cases)
def run_golden_regression(run_one, cases=None):
    if cases is None:
        p=Path("tests/golden/golden_cases_vn.json")
        cases=json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
        if isinstance(cases, dict): cases=cases.get("cases", cases.get("CASES", []))
    total=len(cases or []); failures=[]; forbidden_cases=0; mismatch_cases=0
    for case in cases or []:
        try: result=run_one(case) or {}
        except Exception as exc: failures.append({"id":case.get("id"),"error":str(exc)}); continue
        if result.get("ward_name")==FORBIDDEN_DEFAULT["ward_name"] and result.get("district_name")==FORBIDDEN_DEFAULT["district_name"]:
            forbidden_cases+=1; failures.append({"id":case.get("id"),"error":"forbidden_default"})
        declared=case.get("declared_province_code")
        if declared and str(result.get("province_code"))!=str(declared):
            mismatch_cases+=1; failures.append({"id":case.get("id"),"error":"province_mismatch"})
    forbidden_rate=(forbidden_cases/total) if total else 0.0
    mismatch_rate=(mismatch_cases/total) if total else 0.0
    passed=not failures
    return {"passed":passed,"failures":failures,"forbidden_default_rate":forbidden_rate,"province_mismatch_rate":mismatch_rate,"total":total}
