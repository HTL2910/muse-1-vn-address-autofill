/**
 * Structured Address Picker 3 cấp — approach_13, nhánh expert/systems-arch-m1.
 *
 * Thay ô địa chỉ tự do duy nhất bằng flow:
 *   Tỉnh/Thành -> Quận/Huyện -> Phường/Xã -> số nhà / đường / thôn-xóm
 * Mỗi bước lọc theo mã hành chính cha (không tìm toàn quốc).
 * Ô tìm kiếm nhanh (Goong) chỉ gợi ý sau >= 3 ký tự có nghĩa,
 * debounce 300ms, tối đa 5 gợi ý kèm đầy đủ tỉnh/huyện/xã.
 * Backend chỉ nhận khi có đủ province_code, district_code, ward_code.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface AdminUnit {
  code: string;
  name: string;
  level: "province" | "district" | "ward";
  parent_code?: string | null;
}

export interface AddressValue {
  raw_input: string;
  province: string;
  province_code: string;
  district: string;
  district_code: string;
  ward: string;
  ward_code: string;
  street: string;
  house_number: string;
  hamlet: string;
}

export type GateStatus = "VERIFIED" | "NEED_CONFIRMATION" | "REJECTED_INSUFFICIENT";

export interface Suggestion {
  display: string;
  province: string;
  province_code: string;
  district: string;
  district_code: string;
  ward: string;
  ward_code: string;
  street?: string;
  house_number?: string;
  confidence: number;
  source: string;
}

interface Props {
  customerId?: string;
  value?: Partial<AddressValue>;
  onChange: (value: AddressValue, status: GateStatus) => void;
  onStatusChange?: (status: GateStatus, reason?: string) => void;
  disabled?: boolean;
}

const EMPTY: AddressValue = {
  raw_input: "",
  province: "",
  province_code: "",
  district: "",
  district_code: "",
  ward: "",
  ward_code: "",
  street: "",
  house_number: "",
  hamlet: "",
};

function meaningfulLength(text: string): number {
  // Đếm ký tự có nghĩa, bỏ khoảng trắng và từ chung 1-2 ký tự.
  return text.replace(/\s+/g, "").replace(/^(xom|bi)$/i, "").length;
}

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`Request failed: ${res.status} ${url}`);
  return res.json() as Promise<T>;
}

export default function StructuredAddressPicker({
  customerId,
  value,
  onChange,
  onStatusChange,
  disabled = false,
}: Props) {
  const [form, setForm] = useState<AddressValue>({ ...EMPTY, ...value });
  const [provinces, setProvinces] = useState<AdminUnit[]>([]);
  const [districts, setDistricts] = useState<AdminUnit[]>([]);
  const [wards, setWards] = useState<AdminUnit[]>([]);
  const [search, setSearch] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [history, setHistory] = useState<AddressValue[]>([]);
  const [status, setStatus] = useState<GateStatus>("NEED_CONFIRMATION");
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  const debouncedSearch = useDebounced(search, 300);

  const isComplete = useMemo(
    () =>
      Boolean(form.province_code && form.district_code && form.ward_code),
    [form]
  );

  const emit = useCallback(
    (next: AddressValue, nextStatus: GateStatus, reason?: string) => {
      setForm(next);
      setStatus(nextStatus);
      onChange(next, nextStatus);
      onStatusChange?.(nextStatus, reason);
    },
    [onChange, onStatusChange]
  );

  // 1) Nạp master data tỉnh — nguồn sự thật nội bộ, không gọi Goong ở đây.
  useEffect(() => {
    fetchJson<AdminUnit[]>("/api/address/admin/provinces")
      .then(setProvinces)
      .catch(() => setError("Không tải được danh sách tỉnh/thành."));
  }, []);

  // 2) Lịch sử địa chỉ đã xác minh của khách — ưu tiên gợi ý.
  useEffect(() => {
    if (!customerId) return;
    fetchJson<AddressValue[]>(
      `/api/address/history?customer_id=${encodeURIComponent(customerId)}`
    )
      .then(setHistory)
      .catch(() => undefined);
  }, [customerId]);

  // 3) Chọn tỉnh -> nạp huyện con của tỉnh đó.
  useEffect(() => {
    if (!form.province_code) {
      setDistricts([]);
      setWards([]);
      return;
    }
    fetchJson<AdminUnit[]>(
      `/api/address/admin/districts?province_code=${form.province_code}`
    )
      .then(setDistricts)
      .catch(() => setError("Không tải được danh sách quận/huyện."));
    setWards([]);
  }, [form.province_code]);

  // 4) Chọn huyện -> nạp xã con của huyện đó.
  useEffect(() => {
    if (!form.district_code) {
      setWards([]);
      return;
    }
    fetchJson<AdminUnit[]>(
      `/api/address/admin/wards?district_code=${form.district_code}`
    )
      .then(setWards)
      .catch(() => setError("Không tải được danh sách phường/xã."));
  }, [form.district_code]);

  // 5) Tìm kiếm nhanh Goong: >= 3 ký tự có nghĩa, debounce 300ms, tối đa 5.
  useEffect(() => {
    if (meaningfulLength(debouncedSearch) < 3) {
      setSuggestions([]);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    fetch(
      `/api/address/suggest?q=${encodeURIComponent(debouncedSearch)}&limit=5`,
      { signal: controller.signal }
    )
      .then((r) => r.json())
      .then((data: { suggestions: Suggestion[] }) => {
        // Ưu tiên lịch sử đã xác minh của khách lên đầu.
        const mine = history
          .filter((h) => h.raw_input.toLowerCase().includes(debouncedSearch.toLowerCase()))
          .slice(0, 2)
          .map((h) => ({
            display: h.raw_input,
            province: h.province,
            province_code: h.province_code,
            district: h.district,
            district_code: h.district_code,
            ward: h.ward,
            ward_code: h.ward_code,
            street: h.street,
            house_number: h.house_number,
            confidence: 1,
            source: "customer_confirmed",
          }));
        setSuggestions([...mine, ...(data.suggestions ?? [])].slice(0, 5));
      })
      .catch((e: unknown) => {
        if ((e as Error).name !== "AbortError") setSuggestions([]);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [debouncedSearch, history]);

  const pickSuggestion = (s: Suggestion) => {
    const next: AddressValue = {
      ...form,
      raw_input: s.display,
      province: s.province,
      province_code: s.province_code,
      district: s.district,
      district_code: s.district_code,
      ward: s.ward,
      ward_code: s.ward_code,
      street: s.street ?? form.street,
      house_number: s.house_number ?? form.house_number,
    };
    // Highlight từng cấp để khách đối chiếu trước khi chốt.
    setHighlight(new Set(["province", "district", "ward"]));
    setSuggestions([]);
    setSearch("");
    emit(next, s.confidence >= 0.85 ? "VERIFIED" : "NEED_CONFIRMATION");
  };

  const selectProvince = (code: string) => {
    const unit = provinces.find((p) => p.code === code);
    if (!unit) return;
    emit(
      { ...form, province: unit.name, province_code: code, district: "", district_code: "", ward: "", ward_code: "" },
      "NEED_CONFIRMATION",
      "province_changed"
    );
  };

  const selectDistrict = (code: string) => {
    const unit = districts.find((d) => d.code === code);
    if (!unit) return;
    emit(
      { ...form, district: unit.name, district_code: code, ward: "", ward_code: "" },
      "NEED_CONFIRMATION",
      "district_changed"
    );
  };

  const selectWard = (code: string) => {
    const unit = wards.find((w) => w.code === code);
    if (!unit) return;
    const next = { ...form, ward: unit.name, ward_code: code };
    emit(next, "NEED_CONFIRMATION", "ward_selected");
  };

  const updateDetail = (patch: Partial<AddressValue>) => {
    const next = { ...form, ...patch, raw_input: "" };
    const raw = [next.house_number, next.street, next.hamlet, next.ward, next.district, next.province]
      .filter(Boolean)
      .join(", ");
    next.raw_input = raw;
    // Chưa đủ 3 mã thì không bao giờ VERIFIED.
    const nextStatus: GateStatus =
      next.province_code && next.district_code && next.ward_code
        ? status
        : "NEED_CONFIRMATION";
    emit(next, nextStatus);
  };

  const canSubmit = isComplete && form.street.trim().length >= 2;

  return (
    <div aria-label="Chọn địa chỉ giao hàng">
      <label>
        Tìm nhanh địa chỉ (tối thiểu 3 ký tự)
        <input
          value={search}
          disabled={disabled}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="VD: 127 Nguyễn Trãi, Cuôr Đăng, Đắk Lắk"
          autoComplete="off"
        />
      </label>
      {loading && <p>Đang gợi ý…</p>}
      {suggestions.length > 0 && (
        <ul>
          {suggestions.map((s, i) => (
            <li key={`${s.ward_code}-${i}`}>
              <button type="button" onClick={() => pickSuggestion(s)}>
                {s.display}
                <small>
                  {" "}
                  — {s.ward}, {s.district}, {s.province} ({s.source})
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}

      <label>
        Tỉnh/Thành *
        <select
          value={form.province_code}
          disabled={disabled}
          onChange={(e) => selectProvince(e.target.value)}
        >
          <option value="">— Chọn tỉnh/thành —</option>
          {provinces.map((p) => (
            <option key={p.code} value={p.code}>{p.name}</option>
          ))}
        </select>
      </label>

      <label>
        Quận/Huyện *
        <select
          value={form.district_code}
          disabled={disabled || !form.province_code}
          onChange={(e) => selectDistrict(e.target.value)}
        >
          <option value="">— Chọn quận/huyện —</option>
          {districts.map((d) => (
            <option key={d.code} value={d.code}>{d.name}</option>
          ))}
        </select>
      </label>

      <label>
        Phường/Xã *
        <select
          value={form.ward_code}
          disabled={disabled || !form.district_code}
          onChange={(e) => selectWard(e.target.value)}
        >
          <option value="">— Chọn phường/xã —</option>
          {wards.map((w) => (
            <option key={w.code} value={w.code}>{w.name}</option>
          ))}
        </select>
      </label>

      <label>
        Số nhà
        <input
          value={form.house_number}
          disabled={disabled}
          onChange={(e) => updateDetail({ house_number: e.target.value })}
          placeholder="VD: 73, 127"
        />
      </label>
      <label>
        Đường *
        <input
          value={form.street}
          disabled={disabled}
          onChange={(e) => updateDetail({ street: e.target.value })}
          placeholder="VD: đường Lam Sơn"
        />
      </label>
      <label>
        Thôn/Xóm
        <input
          value={form.hamlet}
          disabled={disabled}
          onChange={(e) => updateDetail({ hamlet: e.target.value })}
          placeholder="VD: Xóm 5 (không bắt buộc)"
        />
      </label>

      {highlight.size > 0 && (
        <p>
          Đã bóc tách: Tỉnh <strong>{form.province}</strong> — Huyện{" "}
          <strong>{form.district}</strong> — Xã <strong>{form.ward}</strong>.
          Vui lòng đối chiếu trước khi xác nhận.
        </p>
      )}
      {!canSubmit && (
        <p>Chưa đủ thông tin: cần chọn đủ Tỉnh — Huyện — Xã và nhập tên đường.</p>
      )}
      {error && <p role="alert">{error}</p>}
      <p>Trạng thái: {status}</p>
    </div>
  );
}
