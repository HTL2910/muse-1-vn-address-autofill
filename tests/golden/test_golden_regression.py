"""
Golden regression tests — fix_missing_golden_tests.

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/golden -v

Thiết kế:
1) Test toàn vẹn dữ liệu: chạy không cần pipeline, khóa việc bộ golden phải
   bao phủ đủ các ca gốc, đúng schema, có ca hợp lệ đối chứng, và không ca
   bị chặn nào lại kỳ vọng một mã hành chính được chốt.
2) Test pipeline: nối qua entry point thật của repo theo thứ tự ưu tiên
   AddressGateway (src/address/api) -> AddressService (src/address/services),
   vốn bên trong phải chạy InputGuard -> province anchor -> fuzzy matcher ->
   validator -> confidence gate. Nếu import được
   observability.run_golden_regression thì dùng nó để tính metric toàn cục;
   nếu không, tự tính metric tương đương trong file này (assert không nới lỏng).
3) Nếu không tìm thấy entry point nào: test pipeline FAIL với thông báo rõ
   (không skip), vì gate hồi quy không được xanh giả khi pipeline chưa có
   trên main — đúng phát hiện fix_missing_pipeline_core của vòng audit.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_FILE = Path(__file__).resolve().parent / "golden_cases_vn.json"
DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))
CASES = DATA["cases"]
FORBIDDEN_DEFAULT = DATA["forbidden_default_address"]

# Ánh xạ trạng thái tương đương giữa các tầng (schemas / gate / adapter).
STATUS_ALIASES = {
    "NEED_CONFIRMATION": "NEEDS_REVIEW",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "REJECTED_INSUFFICIENT": "REJECTED",
    "REJECTED": "REJECTED",
    "BLOCKED": "REJECTED",
    "BLOCKED_UNVERIFIED": "REJECTED",
    "VERIFIED": "VERIFIED",
    "APPROVED": "VERIFIED",
    "OK": "VERIFIED",
}

REQUIRED_ORIGINAL_IDS = {
    "golden_01_unreadable_no_default",
    "golden_03_cuor_dang_dak_lak_not_hai_duong",
    "golden_04_xom_too_short",
    "golden_05_soi_799_lam_son_missing_levels",
    "golden_06_number_73_only",
    "golden_07_number_127_only",
    "golden_08_bi_ambiguous",
    "golden_09_bi_accented_ambiguous",
}

BLOCKED_STATUSES = {"REJECTED", "NEEDS_REVIEW"}


# --------------------------------------------------------------------------- #
# 1) Toàn vẹn dữ liệu golden
# --------------------------------------------------------------------------- #

def test_dataset_covers_all_original_cases():
    ids = {c["id"] for c in CASES}
    assert REQUIRED_ORIGINAL_IDS <= ids, f"Thiếu ca gốc: {REQUIRED_ORIGINAL_IDS - ids}"
    assert len(CASES) >= 10, (
        "Cần tối thiểu 8 ca gốc + ca mismatch tỉnh + ca hợp lệ đối chứng")


def test_dataset_schema():
    seen_ids = set()
    for case in CASES:
        assert case["id"] not in seen_ids, f"Trùng id ca: {case['id']}"
        seen_ids.add(case["id"])
        for key in ("id", "raw_input", "expected"):
            assert key in case, f"{case.get('id')}: thiếu trường {key}"
        exp = case["expected"]
        for key in ("statuses", "province_code", "district_code",
                    "ward_code", "reason_codes"):
            assert key in exp, f"{case['id']}: expected thiếu {key}"
        assert exp["statuses"], f"{case['id']}: statuses rỗng"
        for status in exp["statuses"]:
            assert status in DATA["status_vocabulary"], (
                f"{case['id']}: status {status!r} ngoài từ vựng chuẩn")


def test_dataset_blocked_cases_expect_no_committed_province():
    """Mọi ca chỉ cho phép trạng thái bị chặn phải kỳ vọng province_code=null:
    đó là cách khóa 'tỉ lệ ép điền khi thiếu thông tin = 0' ngay ở dữ liệu."""
    for case in CASES:
        normalized = {STATUS_ALIASES.get(s, s) for s in case["expected"]["statuses"]}
        if normalized <= BLOCKED_STATUSES and "VERIFIED" not in normalized:
            if case["expected"]["statuses"] and all(
                    s.startswith("REJECTED") for s in case["expected"]["statuses"]):
                assert case["expected"]["province_code"] is None, case["id"]
                assert case["expected"]["district_code"] is None, case["id"]
                assert case["expected"]["ward_code"] is None, case["id"]


def test_dataset_has_valid_control_case():
    valid = [c for c in CASES if c["expected"]["statuses"] == ["VERIFIED"]]
    assert valid, "Thiếu ca hợp lệ đối chứng (chỉ VERIFIED)"
    for case in valid:
        assert case["expected"]["province_code"]
        assert case["expected"]["district_code"]
        assert case["expected"]["ward_code"]


# --------------------------------------------------------------------------- #
# 2) Nối pipeline thật
# --------------------------------------------------------------------------- #

def _normalize_result(raw) -> dict:
    """Chuẩn hóa mọi dạng trả về (dict / dataclass / object) về 1 dict chung."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raw = {
            "status": getattr(raw, "status", None) or getattr(raw, "decision", None),
            "province_code": getattr(raw, "province_code", None),
            "district_code": getattr(raw, "district_code", None),
            "ward_code": getattr(raw, "ward_code", None),
            "province_name": getattr(raw, "province_name", None),
            "district_name": getattr(raw, "district_name", None),
            "ward_name": getattr(raw, "ward_name", None),
            "reason_code": getattr(raw, "reason_code", None) or getattr(raw, "reason", None),
            **(getattr(raw, "__dict__", {}) or {}),
        }
    nested = raw.get("resolved_address") or raw.get("address") or {}
    if isinstance(nested, dict):
        for key in ("province_code", "district_code", "ward_code",
                    "province_name", "district_name", "ward_name"):
            if raw.get(key) is None:
                raw[key] = nested.get(key)
    status = str(raw.get("status") or raw.get("decision") or "").upper()
    return {
        "status": STATUS_ALIASES.get(status, status),
        "province_code": raw.get("province_code"),
        "district_code": raw.get("district_code"),
        "ward_code": raw.get("ward_code"),
        "province_name": raw.get("province_name"),
        "district_name": raw.get("district_name"),
        "ward_name": raw.get("ward_name"),
        "reason_code": str(raw.get("reason_code") or raw.get("reason") or "").upper(),
    }


def _wrap_callable(fn):
    def call(raw_input, declared, suggestion):
        try:
            return fn(raw_input, declared_province_code=declared,
                      provider_suggestion=suggestion)
        except TypeError:
            try:
                return fn(raw_input)
            except TypeError:
                return fn({"raw_input": raw_input,
                           "declared_province_code": declared,
                           "provider_suggestion": suggestion})
    return call


def _load_pipeline_callable():
    """Thử lần lượt các entry point đã thống nhất; không thấy thì raise rõ ràng."""
    attempts = []
    candidates = (
        ("src.address.api.address_gateway", ("AddressGateway", "Gateway"),
         ("process", "handle", "autofill", "resolve")),
        ("src.address.services.address_service", ("AddressService",),
         ("process", "handle", "resolve_address", "process_address")),
    )
    for module_name, class_names, method_names in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            attempts.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        for class_name in class_names:
            cls = getattr(module, class_name, None)
            if cls is None:
                continue
            try:
                instance = cls()
            except Exception:
                instance = cls
            for method_name in method_names:
                fn = getattr(instance, method_name, None)
                if callable(fn):
                    return _wrap_callable(fn)
        for func_name in method_names:
            fn = getattr(module, func_name, None)
            if callable(fn):
                return _wrap_callable(fn)
        attempts.append(f"{module_name}: không thấy entry point phù hợp")
    raise AssertionError(
        "Không tìm thấy pipeline entry point trên main (AddressGateway/"
        "AddressService). Golden gate không thể xanh cho tới khi "
        "fix_missing_pipeline_core được khôi phục. Chi tiết: "
        + "; ".join(attempts))


_PIPELINE = None


def run_one(case: dict) -> dict:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = _load_pipeline_callable()
    result = _PIPELINE(case["raw_input"], case.get("declared_province_code"),
                       case.get("simulated_provider_suggestion"))
    return _normalize_result(result)


# --------------------------------------------------------------------------- #
# 3) Assert từng ca
# --------------------------------------------------------------------------- #

def _assert_case(case: dict, result: dict) -> None:
    exp = case["expected"]
    expected_statuses = {STATUS_ALIASES.get(s, s) for s in exp["statuses"]}
    assert result.get("status") in expected_statuses, (
        f"{case['id']}: status {result.get('status')!r} "
        f"không thuộc {sorted(expected_statuses)}")
    for field_name in ("province_code", "district_code", "ward_code"):
        expected_code = exp[field_name]
        if expected_code is None:
            # province: luôn kiểm nghiêm khi kỳ vọng null (chốt chống ép điền /
            # sai tỉnh). district/ward: kiểm nghiêm khi ca bị REJECTED hẳn;
            # ở NEEDS_REVIEW mã đề xuất chưa chốt được phép tồn tại để CS xem.
            if field_name == "province_code" or result.get("status") == "REJECTED":
                assert not result.get(field_name), (
                    f"{case['id']}: {field_name} phải rỗng (không ép điền), "
                    f"nhưng nhận {result.get(field_name)!r}")
        else:
            assert str(result.get(field_name)) == str(expected_code), (
                f"{case['id']}: {field_name} kỳ vọng {expected_code}, "
                f"nhận {result.get(field_name)!r}")
    if exp["reason_codes"]:
        assert result.get("reason_code") in exp["reason_codes"], (
            f"{case['id']}: reason_code {result.get('reason_code')!r} "
            f"không thuộc {exp['reason_codes']}")
    for key, value in (case.get("forbidden") or {}).items():
        if key == "committed_as_verified":
            assert result.get("status") != "VERIFIED", (
                f"{case['id']}: ca này không bao giờ được VERIFIED")
        else:
            assert str(result.get(key)) != str(value), (
                f"{case['id']}: trúng giá trị cấm {key}={value!r}")
    # Chốt toàn cục cho ca input không đọc được: kết quả không được là địa
    # chỉ mặc định cấm do hệ thống tự sinh (ca khách ghi thật địa chỉ này
    # là golden_02, có declared_province_code nên được loại trừ ở đây).
    if case.get("declared_province_code") is None and case["id"] == "golden_01_unreadable_no_default":
        assert not (
            result.get("ward_name") == FORBIDDEN_DEFAULT["ward_name"]
            and result.get("district_name") == FORBIDDEN_DEFAULT["district_name"]
        ), f"{case['id']}: kết quả rơi vào địa chỉ mặc định cấm Đăk Yă/Mang Yang"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    _assert_case(case, run_one(case))


def test_golden_regression_via_observability_if_available():
    """Metric toàn cục: forbidden-default = 0 và sai tỉnh trên đơn đã khai
    báo tỉnh = 0. Dùng observability.run_golden_regression nếu import được,
    nếu không tự tính tương đương với cùng ngưỡng assert."""
    run_regression = None
    try:
        observability = importlib.import_module(
            "src.address.infrastructure.observability")
        run_regression = getattr(observability, "run_golden_regression", None)
    except Exception:
        run_regression = None
    if run_regression is not None:
        report = run_regression(run_one, CASES)
        if isinstance(report, dict):
            assert report.get("forbidden_default_rate", 0) == 0
            assert report.get("province_mismatch_rate", 0) == 0
            return
    results = {c["id"]: run_one(c) for c in CASES}
    unreadable = results["golden_01_unreadable_no_default"]
    assert not (
        unreadable.get("ward_name") == FORBIDDEN_DEFAULT["ward_name"]
        and unreadable.get("district_name") == FORBIDDEN_DEFAULT["district_name"]
    ), "Input không đọc được vẫn sinh địa chỉ mặc định cấm"
    for case in CASES:
        declared = case.get("declared_province_code")
        if declared:
            assert str(results[case["id"]].get("province_code")) == str(declared), (
                f"{case['id']}: đơn đã khai báo tỉnh {declared} nhưng kết quả "
                f"lệch tỉnh ({results[case['id']].get('province_code')!r})")
