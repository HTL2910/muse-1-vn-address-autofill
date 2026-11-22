"""
approach_20 — Triển khai an toàn: shadow mode, A/B, canary, feature flag.

Giai đoạn:
  SHADOW  (1-2 tuần): gateway mới chỉ chạy ngầm, ghi log chênh lệch so với
          Pancake/Goong hiện tại, KHÔNG chặn đơn, KHÔNG đổi địa chỉ khách.
  CANARY  : 5% -> 10% -> 50% -> 100% đơn thật đi qua gateway mới, theo
          từng shop và khu vực, có thể dừng ở bất kỳ bậc nào.
  FULL    : 100%, vẫn giữ kill-switch.

Feature flag theo shop + khu vực, rollback một nút: `rollback(shop_id)` đưa
shop về hệ thống cũ ngay lập tức và ghi audit log. Mọi quyết định định tuyến
đều deterministic theo hash(order_id) để cùng một đơn không nhảy qua lại
giữa hai hệ thống khi retry.

KPI nghiệm thu (cứng, kiểm tra bằng `evaluate_kpi`):
- sai_tỉnh với đơn đã khai báo tỉnh == 0
- đơn bị ép điền khi thiếu thông tin == 0
- tỉ lệ giao thành công đích: > 90-92% (từ nền 60-70%)
- tỉ lệ hoàn do sai địa chỉ giảm theo tuần (so sánh tuần hiện tại < tuần trước)
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum


class RolloutStage(str, Enum):
    SHADOW = "shadow"
    CANARY = "canary"
    FULL = "full"
    DISABLED = "disabled"  # đã rollback / tắt khẩn cấp


CANARY_STEPS: tuple[int, ...] = (5, 10, 50, 100)


@dataclass
class ShopRollout:
    shop_id: str
    stage: RolloutStage = RolloutStage.SHADOW
    canary_percent: int = 0              # chỉ có nghĩa khi stage == CANARY
    regions: set[str] = field(default_factory=set)  # rỗng = mọi khu vực
    updated_at: float = field(default_factory=time.time)
    updated_by: str = "system"


@dataclass
class ShadowDiff:
    order_id: str
    shop_id: str
    legacy_province_code: str | None     # Pancake/Goong hiện tại chốt
    new_province_code: str | None        # gateway mới sẽ chốt
    declared_province_code: str | None   # tỉnh khách đã ghi rõ (nếu có)
    new_decision: str                    # VERIFIED | NEEDS_REVIEW | REJECTED
    would_block: bool                    # gateway mới có chặn đơn này không
    province_mismatch: bool              # hai hệ thống lệch tỉnh nhau
    recorded_at: float = field(default_factory=time.time)


@dataclass
class KpiSnapshot:
    declared_province_orders: int
    wrong_province_orders: int
    insufficient_info_orders: int
    force_filled_orders: int             # bị ép điền khi thiếu thông tin
    total_orders: int
    delivered_orders: int
    returned_wrong_address_orders: int
    previous_week_return_rate: float | None = None

    @property
    def delivery_success_rate(self) -> float:
        return self.delivered_orders / self.total_orders if self.total_orders else 0.0

    @property
    def wrong_address_return_rate(self) -> float:
        return (self.returned_wrong_address_orders / self.total_orders
                if self.total_orders else 0.0)


@dataclass
class KpiVerdict:
    passed: bool
    failures: list[str]
    delivery_success_rate: float
    target_success_rate: float = 0.90


def evaluate_kpi(kpi: KpiSnapshot, target_success_rate: float = 0.90) -> KpiVerdict:
    failures: list[str] = []
    if kpi.wrong_province_orders != 0:
        failures.append(
            f"Sai tỉnh {kpi.wrong_province_orders} đơn đã khai báo tỉnh (yêu cầu = 0)")
    if kpi.force_filled_orders != 0:
        failures.append(
            f"Ép điền {kpi.force_filled_orders} đơn thiếu thông tin (yêu cầu = 0)")
    if kpi.total_orders > 0 and kpi.delivery_success_rate < target_success_rate:
        failures.append(
            f"Tỉ lệ giao thành công {kpi.delivery_success_rate:.1%} < mục tiêu "
            f"{target_success_rate:.0%}")
    if (kpi.previous_week_return_rate is not None
            and kpi.wrong_address_return_rate >= kpi.previous_week_return_rate):
        failures.append("Tỉ lệ hoàn do sai địa chỉ chưa giảm so với tuần trước")
    return KpiVerdict(not failures, failures, kpi.delivery_success_rate,
                      target_success_rate)


class RolloutController:
    def __init__(self) -> None:
        self._shops: dict[str, ShopRollout] = {}
        self._shadow_diffs: list[ShadowDiff] = []
        self._audit_log: list[dict] = []

    # -- Cấu hình / feature flag -------------------------------------------- #
    def configure(self, shop_id: str, stage: RolloutStage,
                  canary_percent: int = 0, regions: set[str] | None = None,
                  updated_by: str = "ops") -> ShopRollout:
        if stage == RolloutStage.CANARY and canary_percent not in CANARY_STEPS:
            raise ValueError(f"canary_percent phải thuộc {CANARY_STEPS}")
        cfg = ShopRollout(shop_id, stage, canary_percent,
                          regions or set(), time.time(), updated_by)
        self._shops[shop_id] = cfg
        self._audit_log.append({"action": "configure", "shop_id": shop_id,
                                "stage": stage.value, "canary_percent": canary_percent,
                                "by": updated_by, "at": cfg.updated_at})
        return cfg

    def advance_canary(self, shop_id: str, updated_by: str = "ops") -> ShopRollout:
        cfg = self._shops.get(shop_id) or self.configure(shop_id, RolloutStage.SHADOW)
        if cfg.stage == RolloutStage.SHADOW:
            return self.configure(shop_id, RolloutStage.CANARY, CANARY_STEPS[0],
                                  cfg.regions, updated_by)
        if cfg.stage == RolloutStage.CANARY:
            idx = CANARY_STEPS.index(cfg.canary_percent)
            if idx + 1 >= len(CANARY_STEPS):
                return self.configure(shop_id, RolloutStage.FULL, 100,
                                      cfg.regions, updated_by)
            return self.configure(shop_id, RolloutStage.CANARY,
                                  CANARY_STEPS[idx + 1], cfg.regions, updated_by)
        return cfg

    def rollback(self, shop_id: str, updated_by: str = "ops") -> ShopRollout:
        """Rollback một nút: shop quay về hệ thống cũ ngay lập tức."""
        cfg = self.configure(shop_id, RolloutStage.DISABLED, 0, set(), updated_by)
        self._audit_log.append({"action": "rollback", "shop_id": shop_id,
                                "by": updated_by, "at": cfg.updated_at})
        return cfg

    # -- Định tuyến từng đơn -------------------------------------------------- #
    def route_for_order(self, shop_id: str, order_id: str,
                        province_code: str | None = None) -> str:
        """Trả 'new' | 'legacy' | 'shadow'. Deterministic theo hash(order_id)."""
        cfg = self._shops.get(shop_id)
        if cfg is None or cfg.stage == RolloutStage.DISABLED:
            return "legacy"
        if cfg.regions and province_code and province_code not in cfg.regions:
            return "legacy"
        if cfg.stage == RolloutStage.SHADOW:
            return "shadow"
        if cfg.stage == RolloutStage.FULL:
            return "new"
        bucket = int(hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        return "new" if bucket < cfg.canary_percent else "legacy"

    # -- Shadow mode: ghi chênh lệch, không can thiệp đơn --------------------- #
    def record_shadow_diff(self, diff: ShadowDiff) -> None:
        self._shadow_diffs.append(diff)

    def shadow_report(self, shop_id: str | None = None) -> dict:
        diffs = [d for d in self._shadow_diffs
                 if shop_id is None or d.shop_id == shop_id]
        total = len(diffs)
        return {
            "total_orders_observed": total,
            "would_block": sum(1 for d in diffs if d.would_block),
            "would_block_rate": (sum(1 for d in diffs if d.would_block) / total) if total else 0.0,
            "province_mismatch_vs_legacy": sum(1 for d in diffs if d.province_mismatch),
            "legacy_wrong_vs_declared": sum(
                1 for d in diffs
                if d.declared_province_code
                and d.legacy_province_code != d.declared_province_code),
            "new_wrong_vs_declared": sum(
                1 for d in diffs
                if d.declared_province_code
                and d.new_province_code != d.declared_province_code),
        }

    def audit_log(self) -> list[dict]:
        return list(self._audit_log)
