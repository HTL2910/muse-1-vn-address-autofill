/**
 * AddressDiffConfirm — approach_14.
 *
 * Diff + confirm UI BẮT BUỘC khi địa chỉ khách gõ và địa chỉ hệ thống
 * chuẩn hóa khác nhau ở bất kỳ cấp nào.
 *
 * Quy tắc:
 *  - Hiển thị song song: trái "Bạn đã nhập", phải "Hệ thống đề xuất".
 *  - Tô đỏ cấp bị đổi, ĐẶC BIỆT là tỉnh (gốc lỗi Cuôr Đăng -> Hải Dương).
 *  - Khách phải chủ động tick chọn 1 trong 2 hoặc sửa lại. Không có nút
 *    mặc định bỏ qua, không tự đóng, không tự chọn hộ.
 *  - Có map pin preview, nút "Địa chỉ này chưa đúng", tóm tắt địa chỉ
 *    chuẩn hóa trước nút Đặt hàng, lưu consent timestamp làm bằng chứng.
 *  - Đơn qua Pancake/chat: dùng buildPancakeConfirmPayload() để gửi link/
 *    tin nhắn xác nhận rút gọn, khách bấm Xác nhận hoặc Sửa trước khi
 *    đẩy sang vận chuyển, kèm timeout + trạng thái chờ trên dashboard.
 */
import React, { useMemo, useState } from "react";

export type AddressParts = {
  houseNumber?: string;
  street?: string;
  ward?: string;
  district?: string;
  province?: string;
  /** Tọa độ pin preview (nếu có từ geocode đã validate). */
  lat?: number;
  lng?: number;
};

export type ConfirmChoice = "proposal" | "raw" | "edited";
export type ConfirmResult = {
  choice: ConfirmChoice;
  address: AddressParts;
  /** Bằng chứng consent: thời điểm khách bấm xác nhận (ISO 8601). */
  consentTimestamp: string;
};

export type AddressDiffConfirmProps = {
  /** Chuỗi thô khách đã gõ — nguồn sự thật, không được sửa ngầm. */
  rawInput: string;
  rawParts: AddressParts;
  /** Địa chỉ hệ thống chuẩn hóa (đã qua Validate phân cấp + Confidence Gate). */
  proposal: AddressParts;
  orderId?: string;
  /** Hiển thị dạng modal (mặc định) hoặc inline trong form checkout. */
  variant?: "modal" | "inline";
  onConfirm: (result: ConfirmResult) => void;
  onReportWrong: (reason: string) => void;
  onEdit?: (current: AddressParts) => void;
};

type LevelKey = "houseNumber" | "street" | "ward" | "district" | "province";
const LEVELS: { key: LevelKey; label: string }[] = [
  { key: "houseNumber", label: "Số nhà" },
  { key: "street", label: "Đường" },
  { key: "ward", label: "Phường / Xã" },
  { key: "district", label: "Quận / Huyện" },
  { key: "province", label: "Tỉnh / Thành phố" },
];

export function formatAddress(a: AddressParts): string {
  return [a.houseNumber, a.street, a.ward, a.district, a.province]
    .filter(Boolean)
    .join(", ");
}

function diffLevels(raw: AddressParts, proposal: AddressParts): Set<LevelKey> {
  const changed = new Set<LevelKey>();
  for (const { key } of LEVELS) {
    const r = (raw[key] ?? "").trim().toLowerCase();
    const p = (proposal[key] ?? "").trim().toLowerCase();
    if (r !== p && (r || p)) changed.add(key);
  }
  return changed;
}

export default function AddressDiffConfirm({
  rawInput, rawParts, proposal, orderId, variant = "modal",
  onConfirm, onReportWrong, onEdit,
}: AddressDiffConfirmProps) {
  const [choice, setChoice] = useState<ConfirmChoice | null>(null);
  const [edited, setEdited] = useState<AddressParts>(proposal);
  const changed = useMemo(() => diffLevels(rawParts, proposal), [rawParts, proposal]);
  const provinceChanged = changed.has("province");

  // Không có khác biệt thì component này không được render — caller phải
  // kiểm tra diff trước, không dùng confirm để hợp thức hóa autofill.
  if (changed.size === 0) return null;

  const canConfirm = choice !== null;
  const chosenAddress = choice === "raw" ? rawParts : choice === "edited" ? edited : proposal;

  const handleConfirm = () => {
    if (!canConfirm || !choice) return;
    onConfirm({
      choice,
      address: chosenAddress,
      consentTimestamp: new Date().toISOString(),
    });
  };

  const cell = (value: string | undefined, isChanged: boolean, isProvince: boolean) => (
    <span
      style={{
        background: isChanged ? "#fde8e8" : "transparent",
        color: isChanged ? "#b42318" : "inherit",
        fontWeight: isChanged && isProvince ? 700 : isChanged ? 600 : 400,
        padding: "2px 4px", borderRadius: 4,
      }}
    >
      {value || "—"}
    </span>
  );

  const body = (
    <div role="dialog" aria-modal={variant === "modal"} aria-label="Xác nhận địa chỉ giao hàng">
      <h2>Địa chỉ hệ thống hiểu khác với địa chỉ bạn đã nhập</h2>
      {provinceChanged && (
        <p role="alert" style={{ color: "#b42318", fontWeight: 700 }}>
          ⚠ Tỉnh/Thành phố đã bị đổi — vui lòng kiểm tra kỹ trước khi xác nhận.
          Đây là lỗi hay gây hoàn đơn nhất.
        </p>
      )}
      <p>Bạn đã nhập: <em>{rawInput}</em></p>

      <table>
        <thead><tr><th>Cấp</th><th>Bạn đã nhập</th><th>Hệ thống đề xuất</th></tr></thead>
        <tbody>
          {LEVELS.map(({ key, label }) => (
            <tr key={key}>
              <td>{label}</td>
              <td>{cell(rawParts[key], changed.has(key), key === "province")}</td>
              <td>{cell(proposal[key], changed.has(key), key === "province")}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {proposal.lat != null && proposal.lng != null && (
        <div>
          <p>Vị trí trên bản đồ (pin preview):</p>
          {/* Chỉ hiển thị pin của proposal đã validate; không dùng pin để chốt địa chỉ. */}
          <iframe
            title="Bản đồ vị trí giao hàng"
            src={`https://www.openstreetmap.org/export/embed.html?bbox=${proposal.lng - 0.02}%2C${proposal.lat - 0.02}%2C${proposal.lng + 0.02}%2C${proposal.lat + 0.02}&layer=mapnik&marker=${proposal.lat}%2C${proposal.lng}`}
            style={{ width: "100%", height: 220, border: "1px solid #ddd" }}
          />
        </div>
      )}

      <fieldset>
        <legend>Vui lòng chọn một phương án (bắt buộc):</legend>
        <label>
          <input type="radio" name="addr-choice" onChange={() => setChoice("proposal")} />
          Dùng địa chỉ hệ thống đề xuất
        </label>
        <label>
          <input type="radio" name="addr-choice" onChange={() => setChoice("raw")} />
          Giữ nguyên địa chỉ tôi đã nhập
        </label>
        <label>
          <input type="radio" name="addr-choice" onChange={() => setChoice("edited")} />
          Tôi muốn sửa lại
        </label>
      </fieldset>

      {choice === "edited" && (
        <div>
          {LEVELS.map(({ key, label }) => (
            <label key={key}>{label}
              <input
                value={edited[key] ?? ""}
                onChange={(e) => setEdited({ ...edited, [key]: e.target.value })}
              />
            </label>
          ))}
          <button type="button" onClick={() => onEdit?.(edited)}>Lưu bản sửa</button>
        </div>
      )}

      <p><strong>Tóm tắt trước khi đặt hàng:</strong> {formatAddress(chosenAddress) || "—"}</p>

      {/* Không có nút "Bỏ qua" mặc định, không tự đóng sau timeout. */}
      <button type="button" disabled={!canConfirm} onClick={handleConfirm}>
        Xác nhận địa chỉ này và đặt hàng
      </button>
      <button type="button" onClick={() => onReportWrong("Địa chỉ này chưa đúng")}>
        Địa chỉ này chưa đúng
      </button>
    </div>
  );

  return variant === "modal" ? (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)" }}>{body}</div>
  ) : body;
}

/** Pancake / chat: payload xác nhận rút gọn, gửi trước khi đẩy sang vận chuyển. */
export type PancakeConfirmPayload = {
  orderId: string;
  rawInput: string;
  proposalText: string;
  confirmUrl: string; // link khách bấm Xác nhận / Sửa
  status: "PENDING_CONFIRMATION";
  expiresAt: string; // timeout, vd: +30 phút; quá hạn -> giữ đơn, không tự đẩy
};

export function buildPancakeConfirmPayload(
  orderId: string, rawInput: string, proposal: AddressParts,
  baseUrl: string, timeoutMinutes = 30,
): PancakeConfirmPayload {
  return {
    orderId,
    rawInput,
    proposalText: formatAddress(proposal),
    confirmUrl: `${baseUrl}/confirm-address?order=${encodeURIComponent(orderId)}`,
    status: "PENDING_CONFIRMATION",
    expiresAt: new Date(Date.now() + timeoutMinutes * 60_000).toISOString(),
  };
}

export function buildPancakeConfirmMessage(p: PancakeConfirmPayload): string {
  return [
    "Shop xin xác nhận lại địa chỉ giao hàng:",
    `Bạn nhập: ${p.rawInput}`,
    `Hệ thống hiểu: ${p.proposalText}`,
    "Nếu đúng, bấm Xác nhận; nếu sai, bấm Sửa. Shop chỉ gửi hàng sau khi bạn xác nhận.",
    p.confirmUrl,
  ].join("\n");
}
