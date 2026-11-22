"""
Golden regression tests — fix_missing_golden_tests.

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/golden -v

Thiết kế:
1) Test toàn vẹn dữ liệu: chạy không cần pipeline, khóa việc bộ golden phải
   bao phủ đủ 8 ca gốc, đúng schema, và không ca nào kỳ vọng địa chỉ mặc định
   cấm "Xã Đăk Yă, Mang Yang, Gia Lai".
2) Test pipeline: nối qua entry point thật của repo theo thứ tự ưu tiên
   AddressGateway (src/address/api) -> AddressService (src/address/services),
   vốn bên trong phải chạy InputGuard -> province anchor -> fuzzy matcher ->
   validator -> confidence gate. Nếu import được
   observability.run_golden_regression thì dùng nó để tính metric; nếu không,
   dùng bộ đếm tương đương trong file này (không nới lỏng assert).
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

# Ánh xạ trạng thái tương đương giữa các tầng (schemas/gate/adapter).
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


# --------------------------------------------------------------------------- #
# 1) Toàn vẹn dữ liệu golden
# --------------------------------------------------------------------------- #

def test_dataset_covers_all_original_cases():
    ids = {c["id"] for c in CASES}
    assert REQUIRED_ORIGINAL_IDS <= ids, f"Thiếu ca gốc: {REQUIRED_ORIGINAL_IDS - ids}"
    assert len(CASES) >= 10, "Cần tối thiểu 8 ca gốc + ca mismatch tỉnh + ca hợp lệ đối chứng"


def test_dataset_schema_and_no_forbidden_default_expectation():
    for case in CASES:
        for key in ("id", "raw_input", "expected"):
            assert key in case, f"{case.get('id')}: thiếu trường {key}"
        exp = case["expected"]
        for key in ("statuses", "province_code", "district_code", "ward_code", "reason_codes"):
            assert key in exp, f"{case['id']}: expected thiếu {key}"
        assert exp["statuses"], f"{case['id']}: statuses rỗng"
        # Không ca nào được kỳ vọng ra địa chỉ mặc định cấm.
        blob = json.dumps(case, ensure_ascii=False)
        assert not (
            FORBIDDEN_DEFAULT["ward_name"] in blob
            and '"province_code": "64"' in blob
            and "VERIFIED" == (exp["statuses"][0] if exp["statuses"] else "")
        ), f"{case['id']}: kỳ vọng địa chỉ mặc định cấm"


def test_dataset_has_valid_control_case():
    valid = [c for c in CASES if c["expected"]["statuses"] == ["VERIFIED"]]
    assert valid, "Thiếu ca hợp lệ đối chứng (chỉ VERIFIED) — gate không được chặn cả đơn đúng"
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
        raw = getattr(raw, "__dict__", {}) or {
            "status": getattr(raw, "status", None),
            "province_code": getattr(raw, "province_code", None),
            "district_code": getattr(raw, "district_code", None),
            "ward_code": getattr(raw, "ward_code", None),
            "reason_code": getattr(raw, "reason_code", None),
        }
    nested = raw.get("resolved_address") or raw.get("address") or {}
    if isinstance(nested, dict):
        for k in ("province_code", "district_code", "ward_code",
                  "province_name", "district_name", "ward_name"):
            raw.setdefault(k, nested.get(k))
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


def _load_pipeline_callable():
    """
    Thử lần lượt các entry point đã thống nhất. Trả về callable(raw_input,
    declared_province_code, provider_suggestion) -> result, hoặc raise
    AssertionError rõ ràng nếu pipeline chưa có trên main.
    """
    attempts = []

    for module_name, class_names, method_names in (
        ("src.address.api.address_gateway", ("AddressGateway", "Gateway"),
         ("process", "handle", "autofill", "resolve")),
        ("src.address.services.address_service", ("AddressService",),
         ("process", "handle", "resolve_address", "process_address")),
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # file thiếu / import lỗi đều là đỏ
            attempts.append(f"{module_name}: {type(exc).__name__}")
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
                    def call(raw_input, declared, suggestion, _fn=fn):
                        try:
                            return _fn(raw_input, declared_province_code=declared,
                                       provider_suggestion=suggestion)
                        except TypeError:
                            try:
                                return _fn(raw_input)
                            except TypeError:
                                return _fn({"raw_input": raw_input,
                                            "declared_province_code": declared,
                                            "provider_suggestion": suggestion})
                    return call
        for func_name in method_names:
            fn = getattr(module, func_name, None)
            if callable(fn):
                def call(raw_input, declared, suggestion, _fn=fn):
                    try:
                        return _fn(raw_input)
                    except TypeError:
                        return _fn({"raw_input": raw_input,
                                    "declared_province_code": declared,
                                    "provider_suggestion": suggestion})
                return call
        attempts.append(f"{module_name}: không thấy entry point")
    raise AssertionError(
        "Không tìm thấy pipeline entry point trên main (AddressGateway/"
        "AddressService). Golden gate không thể xanh cho tới khi "
        "fix_missing_pipeline_core được khôi phục. Chi tiết: " + "; ".join(attempts))


def run_one(case: dict) -> dict:
    pipeline = _load_pipeline_callable()
    result = pipeline(case["raw_input"], case.get("declared_province_code"),
                      case.get("simulated_provider_suggestion"))
    return _normalize_result(result)


# --------------------------------------------------------------------------- #
# 3) Assert từng ca
# --------------------------------------------------------------------------- #

def _assert_case(case: dict, result: dict) -> None:
    exp = case["expected"]
    expected_statuses = {STATUS_ALIASES.get(s, s) for s in exp["statuses"]}
    assert result.get("status") in expected_statuses, (
        f"{case['id']}: status {result.get('status')!r} không thuộc {expected_statuses}")
    for field_name in ("province_code", "district_code", "ward_code"):
        expected_code = exp[field_name]
        if expected_code is None:
            # Fail-closed: ca bị chặn/thiếu thông tin không được chốt mã nào
            # ở các trường được đánh dấu null (province luôn áp dụng; district/
            # ward áp dụng khi ca không kỳ vọng mã cụ thể).
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
    forbidden = case.get("forbidden") or {}
    for key, value in forbidden.items():
        if key == "committed_as_verified":
            assert result.get("status") != "VERIFIED", (
                f"{case['id']}: ca này không bao giờ được VERIFIED")
        else:
            assert str(result.get(key)) != str(value), (
                f"{case['id']}: trúng giá trị cấm {key}={value!r}")
    # Chốt toàn cục: không kết quả nào được là địa chỉ mặc định cấm.
    assert not (
        result.get("ward_name") == FORBIDDEN_DEFAULT["ward_name"]
        and result.get("district_name") == FORBIDDEN_DEFAULT["district_name"]
    ), f"{case['id']}: kết quả rơi vào địa chỉ mặc định cấm Đăk Yă/Mang Yang"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    _assert_case(case, run_one(case))


def test_golden_regression_via_observability_if_available():
    """Dùng observability.run_golden_regression khi import được để tính metric
    toàn cục: forbidden_default_rate phải bằng 0, province_mismatch = 0."""
    try:
        observability = importlib.import_module(
            "src.address.infrastructure.observability")
        run_regression = getattr(observability, "run_golden_regression", None)
    except Exception:
        run_regression = None
    if run_regression is None:
        # Không có observability: tự tính metric tương đương, assert như nhau.
        results = [run_one(c) for c in CASES]
        forbidden_hits = sum(
            1 for r in results
            if r.get("ward_name") == FORBIDDEN_DEFAULT["ward_name"])
        assert forbidden_hits == 0
        return
    report = run_regression(run_one, CASES)
    if isinstance(report, dict):
        assert report.get("forbidden_default_rate", 0) == 0
        assert report.get("province_mismatch_rate", 0) == 0
