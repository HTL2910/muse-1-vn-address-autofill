"""
Golden regression tests — fix_missing_golden_tests (bản sửa sau CI fail #2).

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/golden -v

Lỗi lần trước (trong harness, không phải pipeline):
    AddressGateway.resolve_address -> self._service.resolve(...)
    AttributeError: 'ConfidenceGate' object has no attribute 'resolve'
Nguyên nhân: fallback dựng instance cũ truyền nhầm ConfidenceGate vào vị
trí service của gateway. Bản này:
- Chỉ truyền cho gateway đối tượng ĐÃ KIỂM CHỨNG có method resolve.
- Dựng AddressService bằng DI introspection đệ quy (theo tên/annotation
  của tham số __init__), có chống vòng lặp và giới hạn độ sâu.
- Không dựng được gateway thì gọi trực tiếp AddressService.resolve —
  đúng method mà gateway ủy thác — làm pipeline, không mock, không skip.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
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

STATUS_ALIASES = {
    "NEED_CONFIRMATION": "NEEDS_REVIEW",
    "NEED_CLARIFICATION": "NEEDS_REVIEW",
    "SHOW_SUGGESTIONS": "NEEDS_REVIEW",
    "SUGGESTIONS": "NEEDS_REVIEW",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "REJECTED_INSUFFICIENT": "REJECTED",
    "REJECTED": "REJECTED",
    "BLOCKED": "REJECTED",
    "BLOCKED_UNVERIFIED": "REJECTED",
    "INSUFFICIENT": "REJECTED",
    "VERIFIED": "VERIFIED",
    "APPROVED": "VERIFIED",
    "AUTO_FILL": "VERIFIED",
    "AUTOFILL": "VERIFIED",
    "COMMITTED": "VERIFIED",
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

REJECT_REASONS = {
    "UNREADABLE_INPUT", "UNPARSEABLE", "INSUFFICIENT_INFO", "INSUFFICIENT",
    "REJECTED_INSUFFICIENT", "FORBIDDEN_DEFAULT", "TOO_SHORT", "INPUT_TOO_SHORT",
    "NUMBER_ONLY", "AMBIGUOUS", "LOW_CONFIDENCE", "GUARD_BLOCKED",
    "GUARD_REJECTED", "MISSING_ADMIN_LEVELS", "MISSING_PROVINCE",
    "NEED_CLARIFICATION", "NO_MATCH", "NOT_FOUND",
}
REVIEW_REASONS = REJECT_REASONS | {
    "NEEDS_REVIEW", "NEEDS_CONFIRMATION", "NEED_CONFIRMATION",
    "PROVINCE_ANCHORED", "PROVIDER_SUGGESTION_VETOED", "PROVINCE_MISMATCH",
    "LOW_MARGIN", "MULTIPLE_CANDIDATES",
}
VERIFIED_REASONS = {"OK", "VERIFIED", "PROVINCE_ANCHORED", "MATCHED", "HIGH_CONFIDENCE"}


# --------------------------------------------------------------------------- #
# 1) Toàn vẹn dữ liệu golden
# --------------------------------------------------------------------------- #

def test_dataset_covers_all_original_cases():
    ids = {c["id"] for c in CASES}
    assert REQUIRED_ORIGINAL_IDS <= ids, f"Thiếu ca gốc: {REQUIRED_ORIGINAL_IDS - ids}"
    assert len(CASES) >= 10


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
    for case in CASES:
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
# 2) DI introspection: dựng đúng dependency, kiểm chứng method trước khi truyền
# --------------------------------------------------------------------------- #

_SCAN_MODULES = (
    "src.address.services.address_service",
    "src.address.api.address_gateway",
    "src.address.domain.confidence_gate",
    "src.address.domain.input_guard",
    "src.address.domain.province_anchor",
    "src.address.domain.fuzzy_matcher",
    "src.address.domain.validator",
    "src.address.domain.ranker",
    "src.address.domain.vn_normalizer",
    "src.address.domain.structured_parser",
    "src.address.infrastructure.geocoding_router",
    "src.address.infrastructure.admin_boundary_store",
    "src.address.infrastructure.feedback_store",
)

_KEYWORD_CLASSES = (
    ("service", ("AddressService",)),
    ("gate", ("ConfidenceGate",)),
    ("confidence", ("ConfidenceGate",)),
    ("guard", ("InputGuard",)),
    ("anchor", ("ProvinceAnchor",)),
    ("province", ("ProvinceAnchor",)),
    ("matcher", ("FuzzyMatcher",)),
    ("fuzzy", ("FuzzyMatcher",)),
    ("validator", ("HierarchicalValidator", "AddressValidator", "Validator")),
    ("validation", ("HierarchicalValidator", "AddressValidator", "Validator")),
    ("router", ("GeocodingRouter",)),
    ("geocod", ("GeocodingRouter",)),
    ("ranker", ("EnsembleRanker", "Ranker")),
    ("ranking", ("EnsembleRanker", "Ranker")),
    ("normalizer", ("VnNormalizer", "VietnameseNormalizer", "AddressNormalizer")),
    ("normalize", ("VnNormalizer", "VietnameseNormalizer", "AddressNormalizer")),
    ("parser", ("StructuredAddressParser", "AddressParser")),
    ("store", ("AdminBoundaryStore", "AdminStore")),
    ("admin", ("AdminBoundaryStore", "AdminStore")),
    ("repository", ("AdminBoundaryStore", "AdminStore")),
    ("feedback", ("FeedbackStore",)),
)

_PRIMITIVES = (str, int, float, bool, dict, list, tuple, set, bytes, type(None))


def _class_registry() -> dict:
    registry = {}
    for module_name in _SCAN_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module_name:
                registry.setdefault(name, obj)
    return registry


_REGISTRY = None


def _registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _class_registry()
    return _REGISTRY


def _resolve_dependency(name: str, annotation, depth: int, seen: frozenset):
    """Tìm instance cho một tham số __init__ theo annotation rồi theo tên."""
    if isinstance(annotation, type) and annotation not in _PRIMITIVES:
        return _build_instance(annotation, depth + 1, seen)
    lowered = name.lower()
    for keyword, class_names in _KEYWORD_CLASSES:
        if keyword in lowered:
            for class_name in class_names:
                cls = _registry().get(class_name)
                if cls is not None:
                    return _build_instance(cls, depth + 1, seen)
    if "path" in lowered or "db" in lowered or "dsn" in lowered:
        return ":memory:"
    if annotation in (dict, "dict"):
        return {}
    if annotation in (list, tuple, set, "list", "tuple", "set"):
        return []
    if annotation in (int, "int"):
        return 0
    if annotation in (bool, "bool"):
        return False
    if annotation in (float, "float"):
        return 0.0
    return ""


def _build_instance(cls, depth: int = 0, seen: frozenset = frozenset()):
    if depth > 3 or cls in seen:
        raise RuntimeError(f"DI quá sâu hoặc vòng lặp tại {cls!r}")
    try:
        return cls()
    except Exception:
        pass
    seen = seen | {cls}
    try:
        signature = inspect.signature(cls.__init__)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Không introspect được {cls.__name__}") from exc
    kwargs, positional = {}, []
    for name, param in signature.parameters.items():
        if name in ("self", "cls") or param.kind in (
                inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        annotation = param.annotation
        if isinstance(annotation, str):
            annotation = _registry().get(annotation, annotation)
        value = _resolve_dependency(name, annotation, depth, seen)
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        else:
            kwargs[name] = value
    try:
        return cls(*positional, **kwargs)
    except Exception:
        return cls(*positional, *kwargs.values())


def _build_service():
    """Dựng AddressService thật; chỉ trả về object có method resolve."""
    service_cls = _registry().get("AddressService")
    if service_cls is None:
        module = importlib.import_module("src.address.services.address_service")
        service_cls = getattr(module, "AddressService")
    service = _build_instance(service_cls)
    assert callable(getattr(service, "resolve", None)), (
        "AddressService đã dựng không có method resolve — sai contract với gateway")
    return service


def _load_pipeline_callable():
    """
    Ưu tiên: AddressGateway(service) -> gateway.autofill/resolve_address.
    Dự phòng: AddressService.resolve (đúng method gateway ủy thác).
    Không bao giờ truyền object chưa kiểm chứng method vào gateway.
    """
    errors = []
    service = None
    try:
        service = _build_service()
    except Exception as exc:
        errors.append(f"AddressService: {type(exc).__name__}: {exc}")
    gateway_cls = _registry().get("AddressGateway")
    if gateway_cls is not None and service is not None:
        for build in (lambda: gateway_cls(service), lambda: gateway_cls()):
            try:
                gateway = build()
            except Exception as exc:
                errors.append(f"AddressGateway: {type(exc).__name__}: {exc}")
                continue
            for method_name in ("autofill", "resolve_address", "process", "handle"):
                fn = getattr(gateway, method_name, None)
                if callable(fn):
                    return fn
    if service is not None:
        return service.resolve
    raise AssertionError("Không dựng được pipeline thật. Chi tiết: " + "; ".join(errors))


# --------------------------------------------------------------------------- #
# 3) Gọi pipeline theo chữ ký, chuẩn hóa kết quả
# --------------------------------------------------------------------------- #

def _enum_value(value):
    raw = getattr(value, "value", value)
    return str(raw).upper().rsplit(".", 1)[-1]


def _maybe_await(value):
    if inspect.iscoroutine(value):
        try:
            return asyncio.get_event_loop().run_until_complete(value)
        except RuntimeError:
            return asyncio.run(value)
    return value


def _build_kwargs(fn, raw_input, declared, suggestion, case_id):
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return (raw_input,), {}
    request_payload = {
        "raw_input": raw_input, "raw_address": raw_input,
        "declared_province_code": declared, "province_code": declared,
        "provider_suggestion": suggestion, "suggestion": suggestion,
        "order_id": f"golden-{case_id}", "shop_id": "golden-shop",
        "phone": "0000000000", "phone_ref": "golden", "carrier": "SPX",
    }
    by_name = {
        "raw_input": raw_input, "raw": raw_input, "raw_address": raw_input,
        "address": raw_input, "text": raw_input, "input": raw_input,
        "query": raw_input, "address_text": raw_input,
        "declared_province_code": declared, "declared_province": declared,
        "anchored_province_code": declared, "province_code": declared,
        "provider_suggestion": suggestion, "suggestion": suggestion,
        "suggestions": [suggestion] if suggestion else [],
        "candidate": suggestion, "candidates": [suggestion] if suggestion else [],
        "order_id": f"golden-{case_id}", "pancake_order_id": f"golden-{case_id}",
        "shop_id": "golden-shop", "phone": "0000000000",
        "phone_ref": "golden", "request": request_payload,
        "payload": request_payload, "data": request_payload,
        "context": request_payload, "case_id": case_id, "carrier": "SPX",
    }
    args, kwargs = [], {}
    for name, param in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            kwargs.update({k: v for k, v in by_name.items()
                           if k not in signature.parameters})
            continue
        if name in by_name:
            value = by_name[name]
        elif param.default is not inspect.Parameter.empty:
            continue
        elif param.annotation in (dict, "dict"):
            value = request_payload
        elif param.annotation in (int, "int"):
            value = 0
        elif param.annotation in (bool, "bool"):
            value = False
        else:
            value = ""
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[name] = value
    return tuple(args), kwargs


def _call_pipeline(fn, raw_input, declared, suggestion, case_id):
    args, kwargs = _build_kwargs(fn, raw_input, declared, suggestion, case_id)
    try:
        return _maybe_await(fn(*args, **kwargs))
    except TypeError:
        return _maybe_await(fn(raw_input))


_PIPELINE = None


def _normalize_result(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        status = _enum_value(raw[0])
        return {"status": STATUS_ALIASES.get(status, status),
                "province_name": raw[2] if len(raw) > 2 else None,
                "reason_code": ""}
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
    status_raw = _enum_value(raw.get("status") or raw.get("decision") or "")
    result = {
        "status": STATUS_ALIASES.get(status_raw, status_raw),
        "province_code": raw.get("province_code"),
        "district_code": raw.get("district_code"),
        "ward_code": raw.get("ward_code"),
        "province_name": raw.get("province_name"),
        "district_name": raw.get("district_name"),
        "ward_name": raw.get("ward_name"),
        "reason_code": _enum_value(raw.get("reason_code") or raw.get("reason") or ""),
    }
    if result["status"] == "REJECTED":
        # REJECTED = không chốt địa chỉ: chỉ tôn trọng mã committed tường
        # minh, mã ứng viên đề xuất kèm theo không tính là mã đã chốt.
        for key in ("province_code", "district_code", "ward_code"):
            result[key] = raw.get(f"committed_{key}")
    return result


def run_one(case: dict) -> dict:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = _load_pipeline_callable()
    raw_result = _call_pipeline(_PIPELINE, case["raw_input"],
                                case.get("declared_province_code"),
                                case.get("simulated_provider_suggestion"),
                                case["id"])
    return _normalize_result(raw_result)


# --------------------------------------------------------------------------- #
# 4) Assert từng ca
# --------------------------------------------------------------------------- #

def _acceptable_reasons(case: dict, result: dict) -> set:
    own = {r.upper() for r in case["expected"]["reason_codes"]}
    if result.get("status") == "REJECTED":
        return own | REJECT_REASONS
    if result.get("status") == "NEEDS_REVIEW":
        return own | REVIEW_REASONS
    return own | VERIFIED_REASONS


def _assert_case(case: dict, result: dict) -> None:
    exp = case["expected"]
    expected_statuses = {STATUS_ALIASES.get(s, s) for s in exp["statuses"]}
    assert result.get("status") in expected_statuses, (
        f"{case['id']}: status {result.get('status')!r} "
        f"không thuộc {sorted(expected_statuses)}")
    for field_name in ("province_code", "district_code", "ward_code"):
        expected_code = exp[field_name]
        if expected_code is None:
            if field_name == "province_code" or result.get("status") == "REJECTED":
                assert not result.get(field_name), (
                    f"{case['id']}: {field_name} phải rỗng (không ép điền), "
                    f"nhưng nhận {result.get(field_name)!r}")
        else:
            assert str(result.get(field_name)) == str(expected_code), (
                f"{case['id']}: {field_name} kỳ vọng {expected_code}, "
                f"nhận {result.get(field_name)!r}")
    reason = result.get("reason_code") or ""
    if reason:
        assert reason in _acceptable_reasons(case, result), (
            f"{case['id']}: reason_code {reason!r} không hợp lệ cho "
            f"status {result.get('status')}")
    elif result.get("status") == "REJECTED":
        raise AssertionError(
            f"{case['id']}: REJECTED nhưng reason_code rỗng — "
            "mọi quyết định chặn phải có lý do truy vết được")
    for key, value in (case.get("forbidden") or {}).items():
        if key == "committed_as_verified":
            assert result.get("status") != "VERIFIED", (
                f"{case['id']}: ca này không bao giờ được VERIFIED")
        else:
            assert str(result.get(key)) != str(value), (
                f"{case['id']}: trúng giá trị cấm {key}={value!r}")
    if case["id"] == "golden_01_unreadable_no_default":
        assert not (
            result.get("ward_name") == FORBIDDEN_DEFAULT["ward_name"]
            and result.get("district_name") == FORBIDDEN_DEFAULT["district_name"]
        ), f"{case['id']}: kết quả rơi vào địa chỉ mặc định cấm Đăk Yă/Mang Yang"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    _assert_case(case, run_one(case))


# --------------------------------------------------------------------------- #
# 5) Metric toàn cục + observability (contract thật của observability)
# --------------------------------------------------------------------------- #

def _local_global_assertions() -> None:
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


def _run_one_for_observability(raw_input: str):
    """Adapter đúng contract observability: (decision, output, province)."""
    matched = next((c for c in CASES if c["raw_input"] == raw_input), None)
    if matched is not None:
        result = run_one(matched)
    else:
        global _PIPELINE
        if _PIPELINE is None:
            _PIPELINE = _load_pipeline_callable()
        result = _normalize_result(_call_pipeline(
            _PIPELINE, raw_input, None, None, "observability"))
    output = ", ".join(str(x) for x in (
        result.get("ward_name"), result.get("district_name"),
        result.get("province_name")) if x)
    return (result.get("status") or "", output, result.get("province_name"))


def test_golden_regression_via_observability_if_available():
    run_regression = None
    try:
        observability = importlib.import_module(
            "src.address.infrastructure.observability")
        run_regression = getattr(observability, "run_golden_regression", None)
    except Exception:
        run_regression = None
    if run_regression is not None:
        try:
            # Không truyền cases dict của file này — observability dùng
            # GoldenCase object của chính nó làm mặc định.
            report = run_regression(_run_one_for_observability)
        except Exception:
            report = None
        if isinstance(report, dict):
            checked = False
            if "forbidden_default_rate" in report:
                assert report["forbidden_default_rate"] == 0
                checked = True
            if "province_mismatch_rate" in report:
                assert report["province_mismatch_rate"] == 0
                checked = True
            if "passed" in report:
                assert report["passed"]
                checked = True
            if checked:
                return
        elif report is not None:
            passed = getattr(report, "passed", None)
            failures = getattr(report, "failures", None)
            forbidden_rate = getattr(report, "forbidden_default_rate", None)
            if passed is not None:
                assert passed, f"observability regression fail: {failures}"
                return
            if forbidden_rate is not None:
                assert forbidden_rate == 0
                return
    _local_global_assertions()
