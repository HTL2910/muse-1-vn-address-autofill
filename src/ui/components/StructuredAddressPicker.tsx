/**
 * StructuredAddressPicker — fix_picker_suggestion_bypasses_hierarchy.
 *
 * Quy tắc bắt buộc:
 * 1) Mọi gợi ý (Goong / provider bất kỳ) chỉ là ĐỀ XUẤT. Trước khi nhận phải
 *    qua validateSuggestionHierarchy + hasValidCodes:
 *    - đủ 3 mã tỉnh/huyện/xã,
 *    - huyện thuộc tỉnh, xã thuộc huyện (theo parent_code tường minh nếu
 *      provider trả kèm, nếu không theo quy ước tiền tố mã phân cấp),
 *    - nếu tỉnh đã được neo/khóa (anchoredProvinceCode hoặc tỉnh khách đã
 *      chọn tay) thì gợi ý lệch tỉnh bị loại 100%.
 *    Gợi ý xung đột -> KHÔNG gán mã nào vào form, hiển thị lỗi, giữ nguyên
 *    địa chỉ khách đang nhập.
 * 2) Trạng thái luôn được tính lại từ form MỚI bằng deriveStatus trong mọi
 *    lần thay đổi (kể cả sửa ô chi tiết sau khi đã chọn gợi ý). Không bao
 *    giờ mang status cũ (đặc biệt VERIFIED) sang form đã sửa.
 * 3) Chỉ cho submit khi hasValidCodes(form) == true.
 *
 * Lưu ý: kiểm tra ở UI là phòng thủ phụ — backend (province anchor +
 * validator) vẫn là chốt chặn chính, picker không tự coi gợi ý là đúng.
 */
import React, { useMemo, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AdminOption {
  code: string;
  name: string;
  /** Mã cấp cha tường minh, nếu dữ liệu/provider có. */
  parent_code?: string | null;
}

export interface Suggestion {
  label: string;
  province_code: string;
  district_code: string;
  ward_code: string;
  province_name?: string;
  district_name?: string;
  ward_name?: string;
  /** parent_code tường minh do provider trả kèm (nếu có). */
  district_province_code?: string | null;
  ward_district_code?: string | null;
}

export type PickerStatus =
  | "INCOMPLETE"
  | "NEEDS_CONFIRMATION"
  | "VERIFIED";

export interface AddressForm {
  province_code: string;
  district_code: string;
  ward_code: string;
  province_name: string;
  district_name: string;
  ward_name: string;
  house_number: string;
  street: string;
  detail: string;
  status: PickerStatus;
}

export interface HierarchyCheck {
  ok: boolean;
  reason:
    | "OK"
    | "MISSING_CODES"
    | "DISTRICT_NOT_IN_PROVINCE"
    | "WARD_NOT_IN_DISTRICT"
    | "PROVINCE_ANCHOR_MISMATCH";
  message: string;
}

// ---------------------------------------------------------------------------
// Kiểm tra phân cấp thuần (export để test và tái sử dụng)
// ---------------------------------------------------------------------------

function belongsTo(childCode: string, parentCode: string,
                   explicitParentCode?: string | null): boolean {
  if (!childCode || !parentCode) return false;
  if (explicitParentCode != null && explicitParentCode !== "") {
    return String(explicitParentCode) === String(parentCode);
  }
  // Quy ước mã phân cấp dự phòng: mã cấp dưới bắt đầu bằng mã cấp trên.
  return String(childCode).startsWith(String(parentCode));
}

export function hasValidCodes(input: {
  province_code?: string | null;
  district_code?: string | null;
  ward_code?: string | null;
  district_province_code?: string | null;
  ward_district_code?: string | null;
}): boolean {
  const { province_code, district_code, ward_code } = input;
  if (!province_code || !district_code || !ward_code) return false;
  if (!belongsTo(district_code, province_code, input.district_province_code)) {
    return false;
  }
  if (!belongsTo(ward_code, district_code, input.ward_district_code)) {
    return false;
  }
  return true;
}

export function validateSuggestionHierarchy(
  suggestion: Suggestion,
  opts: { anchoredProvinceCode?: string | null } = {},
): HierarchyCheck {
  if (!suggestion.province_code || !suggestion.district_code || !suggestion.ward_code) {
    return {
      ok: false, reason: "MISSING_CODES",
      message: "Gợi ý thiếu mã tỉnh/huyện/xã nên không được áp dụng.",
    };
  }
  if (opts.anchoredProvinceCode &&
      String(suggestion.province_code) !== String(opts.anchoredProvinceCode)) {
    return {
      ok: false, reason: "PROVINCE_ANCHOR_MISMATCH",
      message: "Gợi ý nằm ngoài tỉnh bạn đã chọn nên đã bị loại.",
    };
  }
  if (!belongsTo(suggestion.district_code, suggestion.province_code,
                 suggestion.district_province_code)) {
    return {
      ok: false, reason: "DISTRICT_NOT_IN_PROVINCE",
      message: "Gợi ý có huyện không thuộc tỉnh tương ứng nên đã bị loại.",
    };
  }
  if (!belongsTo(suggestion.ward_code, suggestion.district_code,
                 suggestion.ward_district_code)) {
    return {
      ok: false, reason: "WARD_NOT_IN_DISTRICT",
      message: "Gợi ý có xã/phường không thuộc huyện tương ứng nên đã bị loại.",
    };
  }
  return { ok: true, reason: "OK", message: "" };
}

/**
 * Trạng thái LUÔN suy ra từ form được truyền vào — không đọc state cũ.
 * VERIFIED không bao giờ tự sinh ở UI sau khi form thay đổi: sau mọi lần
 * sửa, form hoàn chỉnh chỉ ở NEEDS_CONFIRMATION cho tới khi backend xác
 * nhận (bản ghi VERIFIED thuộc về kết quả của Address Gateway).
 */
export function deriveStatus(form: Omit<AddressForm, "status">): PickerStatus {
  if (!hasValidCodes(form)) return "INCOMPLETE";
  return "NEEDS_CONFIRMATION";
}

const EMPTY_FORM: AddressForm = {
  province_code: "", district_code: "", ward_code: "",
  province_name: "", district_name: "", ward_name: "",
  house_number: "", street: "", detail: "",
  status: "INCOMPLETE",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface StructuredAddressPickerProps {
  provinces: AdminOption[];
  districts: AdminOption[];
  wards: AdminOption[];
  initialValue?: Partial<AddressForm>;
  /** Tỉnh đã neo từ bước trước (vd khách đã khai báo tỉnh): khóa gợi ý. */
  anchoredProvinceCode?: string | null;
  fetchSuggestions?: (query: string) => Promise<Suggestion[]>;
  onChange?: (form: AddressForm) => void;
  onSubmit?: (form: AddressForm) => void;
}

export function StructuredAddressPicker(props: StructuredAddressPickerProps) {
  const { provinces, districts, wards, onChange, onSubmit } = props;
  const [form, setFormState] = useState<AddressForm>({
    ...EMPTY_FORM, ...(props.initialValue ?? {}),
  });
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [error, setError] = useState<string>("");

  const emit = (next: AddressForm) => {
    setFormState(next);
    onChange?.(next);
  };

  const districtOptions = useMemo(
    () => districts.filter(
      (d) => !form.province_code || belongsTo(d.code, form.province_code, d.parent_code)),
    [districts, form.province_code]);
  const wardOptions = useMemo(
    () => wards.filter(
      (w) => !form.district_code || belongsTo(w.code, form.district_code, w.parent_code)),
    [wards, form.district_code]);

  const search = async (text: string) => {
    setQuery(text);
    if (!props.fetchSuggestions || text.trim().length < 3) {
      setSuggestions([]);
      return;
    }
    const results = await props.fetchSuggestions(text);
    // Lọc ngay từ danh sách hiển thị: gợi ý vi phạm phân cấp / lệch tỉnh
    // neo không được hiện cho khách bấm vào.
    setSuggestions(results.filter(
      (s) => validateSuggestionHierarchy(s, {
        anchoredProvinceCode: props.anchoredProvinceCode ?? form.province_code || null,
      }).ok));
  };

  const pickSuggestion = (suggestion: Suggestion): boolean => {
    const check = validateSuggestionHierarchy(suggestion, {
      anchoredProvinceCode: props.anchoredProvinceCode ?? form.province_code || null,
    });
    if (!check.ok || !hasValidCodes(suggestion)) {
      // Loại gợi ý: không gán bất kỳ mã nào, giữ nguyên form hiện tại.
      setError(check.message || "Gợi ý không hợp lệ về phân cấp nên đã bị loại.");
      return false;
    }
    setError("");
    const base = {
      ...form,
      province_code: suggestion.province_code,
      district_code: suggestion.district_code,
      ward_code: suggestion.ward_code,
      province_name: suggestion.province_name ?? form.province_name,
      district_name: suggestion.district_name ?? form.district_name,
      ward_name: suggestion.ward_name ?? form.ward_name,
    };
    const nextForm: AddressForm = { ...base, status: deriveStatus(base) };
    emit(nextForm);
    setSuggestions([]);
    return true;
  };

  const updateDetail = (patch: Partial<AddressForm>) => {
    const base = { ...form, ...patch };
    // Tính trạng thái từ form MỚI (base), tuyệt đối không giữ status cũ:
    // sửa bất kỳ trường nào sau khi đã hoàn chỉnh đều hạ về
    // NEEDS_CONFIRMATION/INCOMPLETE để backend xác nhận lại.
    const nextForm: AddressForm = { ...base, status: deriveStatus(base) };
    emit(nextForm);
  };

  const pickProvince = (code: string) => {
    const option = provinces.find((p) => p.code === code);
    updateDetail({
      province_code: code, province_name: option?.name ?? "",
      district_code: "", district_name: "", ward_code: "", ward_name: "",
    });
  };

  const pickDistrict = (code: string) => {
    const option = districtOptions.find((d) => d.code === code);
    if (option && form.province_code &&
        !belongsTo(option.code, form.province_code, option.parent_code)) {
      setError("Huyện đã chọn không thuộc tỉnh hiện tại.");
      return;
    }
    setError("");
    updateDetail({
      district_code: code, district_name: option?.name ?? "",
      ward_code: "", ward_name: "",
    });
  };

  const pickWard = (code: string) => {
    const option = wardOptions.find((w) => w.code === code);
    if (option && form.district_code &&
        !belongsTo(option.code, form.district_code, option.parent_code)) {
      setError("Xã/phường đã chọn không thuộc huyện hiện tại.");
      return;
    }
    setError("");
    updateDetail({ ward_code: code, ward_name: option?.name ?? "" });
  };

  const canSubmit = hasValidCodes(form);

  return (
    <div>
      <input
        aria-label="Tìm địa chỉ"
        value={query}
        onChange={(e) => void search(e.target.value)}
        placeholder="Nhập địa chỉ để xem gợi ý"
      />
      {suggestions.length > 0 && (
        <ul>
          {suggestions.map((s) => (
            <li key={`${s.ward_code}-${s.label}`}>
              <button type="button" onClick={() => pickSuggestion(s)}>
                {s.label}
              </button>
            </li>
          ))}
        </ul>
      )}

      <select aria-label="Tỉnh/Thành phố" value={form.province_code}
              onChange={(e) => pickProvince(e.target.value)}>
        <option value="">Chọn tỉnh/thành phố</option>
        {provinces.map((p) => (
          <option key={p.code} value={p.code}>{p.name}</option>
        ))}
      </select>

      <select aria-label="Quận/Huyện" value={form.district_code}
              onChange={(e) => pickDistrict(e.target.value)}>
        <option value="">Chọn quận/huyện</option>
        {districtOptions.map((d) => (
          <option key={d.code} value={d.code}>{d.name}</option>
        ))}
      </select>

      <select aria-label="Phường/Xã" value={form.ward_code}
              onChange={(e) => pickWard(e.target.value)}>
        <option value="">Chọn phường/xã</option>
        {wardOptions.map((w) => (
          <option key={w.code} value={w.code}>{w.name}</option>
        ))}
      </select>

      <input
        aria-label="Số nhà"
        value={form.house_number}
        onChange={(e) => updateDetail({ house_number: e.target.value })}
        placeholder="Số nhà"
      />
      <input
        aria-label="Tên đường / thôn xóm"
        value={form.street}
        onChange={(e) => updateDetail({ street: e.target.value })}
        placeholder="Tên đường, thôn/xóm"
      />
      <input
        aria-label="Chi tiết bổ sung"
        value={form.detail}
        onChange={(e) => updateDetail({ detail: e.target.value })}
        placeholder="Chi tiết bổ sung (không bắt buộc)"
      />

      {error && <p role="alert">{error}</p>}
      <p data-testid="address-status">{form.status}</p>

      <button type="button" disabled={!canSubmit}
              onClick={() => canSubmit && onSubmit?.(form)}>
        Xác nhận địa chỉ
      </button>
    </div>
  );
}

export default StructuredAddressPicker;
