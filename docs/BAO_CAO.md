# Báo Cáo Nghiên Cứu & Giải Pháp Hội Đồng Chuyên Gia (MuseAssistantTeam)

- **GitHub Repository:** https://github.com/HTL2910/muse-1-vn-address-autofill
- **Trưởng nhóm điều phối:** muse (CEO)
- **Số chuyên gia tham gia:** 5 thành viên
- **Số nhánh sửa lỗi (`fix/...`) đã kiểm thử & gộp:** 16 nhánh
- **Trạng thái nghiệm thu:** 100% Đồng thuận

---

# Hệ thống Autofill Địa chỉ Việt Nam — Fail-Closed Address Intelligence

> **Trạng thái: 100% ĐỒNG THUẬN sau 2 vòng kiểm định trên `main`**
> **Repository:** https://github.com/HTL2910/muse-1-vn-address-autofill
> **Hội đồng:** MuseAssistantTeam — muse (CEO / Team Lead), systems-arch, algo-ml, backend-sec, fullstack-ux

---

## Mục lục

1. [Tổng quan đề bài](#1-tổng-quan-đề-bài)
2. [Kiến trúc & cấu trúc thư mục](#2-kiến-trúc--cấu-trúc-thư-mục)
3. [Các hướng giải quyết đã triển khai](#3-các-hướng-giải-quyết-đã-triển-khai)
4. [Biên bản phản biện, kiểm thử & các nhánh fix](#4-biên-bản-phản-biện-kiểm-thử--các-nhánh-fix)
5. [Cài đặt, sử dụng & kiểm chứng](#5-cài-đặt-sử-dụng--kiểm-chứng)

---

## 1. Tổng quan đề bài

### 1.1. Bối cảnh

Một cửa hàng bán đồ tiêu dùng giao hàng toàn quốc qua app, khối lượng lên tới **hàng nghìn đơn/ngày**. Hệ thống autofill địa chỉ đang dùng **Goong.io và Pancake** chỉ đạt tỉ lệ giao thành công **60–70%**. Sai địa chỉ gây hoàn đơn hàng loạt, giảm doanh thu và uy tín shop.

### 1.2. Các ca lỗi thực tế do người dùng phản ánh

| # | Input của khách | Máy điền sai thành | Hậu quả |
|---|---|---|---|
| 1 | Không đọc được địa chỉ | Mặc định `Xã Đăk Yă, Mang Yang, Gia Lai` | Điền bịa, giao sai tỉnh |
| 2 | Khách ghi rõ tỉnh | Máy điền sang tỉnh khác | Sai tỉnh hệ thống |
| 3 | `xã Cuôr Đăng, Đắk Lắk` | `Phường Phả Lại, Chí Linh, Hải Dương` | Sai tỉnh, sai xã |
| 4 | `Xom` | `Thanh Mỹ, Nghệ An` | Input 1 từ vẫn cho đi |
| 5 | `Sói 799, đường Lam Sơn` | `P2 Tân Bình` | Thiếu ngữ cảnh vẫn chốt đơn |
| 6 | `73` | `Hợp Giang, Cao Bằng` | Chỉ có số vẫn điền |
| 7 | `127` | `Hiến Nam, Hưng Yên` | Chỉ có số vẫn điền |
| 8 | `bi` / `Bị` | `Phú Cường, Thủ Dầu Một` | 2 ký tự vẫn điền |

SPX báo sai địa chỉ và hoàn đơn ở nhiều ca trong số này.

### 1.3. Kết luận chẩn đoán của Hội đồng

Bản chất **không phải Goong yếu**, mà là pipeline thiếu kiểm soát:

- Autofill best-effort **không ngưỡng tin cậy**, lấy top-1 dù độ tin cậy thấp.
- Có **fallback mặc định nguy hiểm** (`Xã Đăk Yă`) khi không đọc được.
- **Không khóa tỉnh/huyện** mà khách đã ghi rõ; không kiểm tra xã có thuộc huyện/tỉnh đó không.
- Không chuẩn hóa tiếng Việt và **địa giới hành chính sau sáp nhập 2025**.
- Không có vòng phản hồi từ **SPX / hoàn đơn**, lỗi lặp lại mãi.

Hệ thống cũ **thà điền sai còn hơn để trống**. Giải pháp chuyển sang nguyên tắc ngược lại: **fail-closed — không đủ bằng chứng thì không điền, không tạo vận đơn**.

### 1.4. Mục tiêu nghiệm thu (KPI)

| Chỉ tiêu | Trước | Mục tiêu |
|---|---|---|
| Tỉ lệ giao thành công | 60–70% | **> 90–92%** |
| Sai tỉnh với đơn đã khai báo tỉnh | Thường xuyên | **= 0** |
| Ép điền khi thiếu thông tin | Thường xuyên | **= 0** |
| Tỉ lệ hoàn do sai địa chỉ | Cao | Giảm theo tuần, đo được theo tỉnh / pattern |

---

## 2. Kiến trúc & cấu trúc thư mục

### 2.1. Nguyên tắc kiến trúc (do Trưởng nhóm thiết kế)

1. **Một Address Contract duy nhất** — mọi tầng giao tiếp qua cùng một schema.
2. **Một entry point duy nhất** — `AddressService` điều phối toàn bộ pipeline; adapter, API, UI không được gọi thẳng provider.
3. **Canonical Admin DB là nguồn sự thật duy nhất** cho tỉnh/huyện/xã sau sáp nhập 2025.
4. **Goong / Pancake chỉ là provider gợi ý**, không được chốt địa chỉ.
5. **Fail-closed ở mọi tầng** — mỗi tầng đều có quyền từ chối thay vì đoán.
6. **Tỉnh do khách ghi là ràng buộc cứng** — mọi ứng viên khác tỉnh bị loại 100%.
7. **Không bao giờ có địa chỉ mặc định** — không đọc được thì trả `REJECTED`, không điền bịa.
8. **Mọi quyết định đều truy vết được** — log `raw_input`, `confidence`, `source`, `reason_code`.

### 2.2. Pipeline 5 tầng

```
raw_address
  → [1] Normalize (chuẩn hóa tiếng Việt, alias, viết tắt)
  → [2] Parse (tách số nhà / đường / thôn / xã / huyện / tỉnh)
  → [3] Anchor (khóa neo tỉnh, lan truyền ràng buộc phân cấp)
  → [4] Geocode / Validate (multi-provider + đối chiếu hãng vận chuyển)
  → [5] Gate / Commit (confidence gate fail-closed)
  → VERIFIED | NEEDS_REVIEW | REJECTED_INSUFFICIENT
```

Trạng thái chuẩn duy nhất, định nghĩa một lần trong `schemas.py` và dùng chung ở mọi tầng:

| Trạng thái | Ý nghĩa |
|---|---|
| `VERIFIED` | Đủ tin cậy, đủ 3 mã hành chính, cho tạo vận đơn |
| `NEEDS_REVIEW` / `NEED_CONFIRMATION` | Chưa đủ chắc, cần khách hoặc CS xác nhận |
| `REJECTED_INSUFFICIENT` | Thiếu thông tin, chặn tạo vận đơn |
| `BLOCKED_UNVERIFIED` | Mâu thuẫn hoặc không xác minh được, chặn tạo vận đơn |

### 2.3. Cấu trúc thư mục trên `main`

```
.
├── .editorconfig
├── .github/workflows/address-pipeline.yml   # CI: manifest, encoding, golden tests
├── README.md
├── data/
│   └── admin/                              # Dữ liệu hành chính chuẩn có version
│       ├── provinces.csv
│       ├── districts.csv
│       ├── wards.csv
│       ├── admin_units.csv
│       ├── admin_units_seed.csv
│       └── merger_2025.csv                 # Mapping sáp nhập 2025 từ nguồn chính thức
├── docs/
│   ├── ARCHITECTURE.md                     # Contract kiến trúc duy nhất
│   └── hoi-dong-nghien-cuu/                # Tài liệu làm việc của Hội đồng
├── infra/
├── scripts/
│   ├── manifest_check.py                   # Kiểm tra cây file + hash
│   └── encoding_check.py                   # Kiểm tra UTF-8 / mojibake
├── src/
│   ├── __init__.py
│   ├── registry.py
│   ├── address/
│   │   ├── api/
│   │   │   └── address_gateway.py          # Facade mỏng gọi AddressService
│   │   ├── domain/                         # Logic thuần, không phụ thuộc provider
│   │   │   ├── schemas.py                  # Address Contract + enum trạng thái
│   │   │   ├── vn_normalizer.py            # Chuẩn hóa tiếng Việt + alias
│   │   │   ├── structured_parser.py        # Parser Rule + NER nhẹ
│   │   │   ├── province_anchor.py          # Khóa neo tỉnh + Aho-Corasick
│   │   │   ├── fuzzy_matcher.py            # Khớp mờ đa tín hiệu
│   │   │   ├── ranker.py                   # Ensemble scoring + Learning-to-Rank
│   │   │   ├── confidence_gate.py          # Cổng tin cậy fail-closed
│   │   │   ├── input_guard.py              # Chặn input quá ngắn / mơ hồ
│   │   │   └── validator.py                # Validator phân cấp + hãng vận chuyển
│   │   ├── infrastructure/                 # Adapter: DB, provider, feedback, metric
│   │   │   ├── admin_boundary_store.py     # Canonical Admin DB + ETL
│   │   │   ├── geocoding_router.py         # Multi-provider router + circuit breaker
│   │   │   ├── feedback_store.py           # Vòng lặp học từ hoàn đơn SPX
│   │   │   ├── observability.py            # Metrics + golden regression + drift
│   │   │   └── rollout.py                  # Shadow mode, A/B, canary, feature flag
│   │   └── services/
│   │       └── address_service.py          # Orchestrator duy nhất của pipeline
│   ├── integrations/
│   │   └── pancake_goong_shipping_adapter.py
│   ├── ops/
│   │   └── review_queue.py                 # Hàng đợi rà soát phân tầng rủi ro
│   ├── orders/
│   ├── security/
│   │   └── address_pii_protection.py       # Mã hóa PII, audit, RBAC
│   └── ui/components/
│       ├── StructuredAddressPicker.tsx     # Picker 3 cấp Tỉnh > Huyện > Xã
│       └── AddressDiffConfirm.tsx          # UI xác nhận chênh lệch
└── tests/
    ├── golden/
    │   ├── golden_cases_vn.json            # 8 ca lỗi gốc bắt buộc
    │   └── test_golden_regression.py
    └── test_*.py                           # 20 file test đơn vị + tích hợp
```

Quy ước: `domain` chứa logic thuần không phụ thuộc provider; `infrastructure` chứa adapter; `api` chỉ là facade; `ui` chỉ là phòng thủ phụ, backend luôn là chốt chặn cuối.

---

## 3. Các hướng giải quyết đã triển khai

Hội đồng đã khử trùng lặp từ 43 ý tưởng ban đầu thành **20 hướng độc lập**, chia đều cho 4 chuyên gia và merge vào `main`.

| # | Hướng giải quyết | File mục tiêu |
|---|---|---|
| 1 | Schema địa chỉ chuẩn và pipeline 5 tầng thống nhất | `src/address/domain/schemas.py` |
| 2 | Canonical Admin DB có version và ETL sáp nhập 2025 | `src/address/infrastructure/admin_boundary_store.py` |
| 3 | Bộ chuẩn hóa tiếng Việt và từ điển alias địa danh | `src/address/domain/vn_normalizer.py` |
| 4 | Parser địa chỉ có cấu trúc Rule-based kết hợp NER nhẹ | `src/address/domain/structured_parser.py` |
| 5 | Khóa neo tỉnh và lan truyền ràng buộc phân cấp | `src/address/domain/province_anchor.py` |
| 6 | Khớp mờ đa tín hiệu trong phạm vi đã khóa | `src/address/domain/fuzzy_matcher.py` |
| 7 | Multi-provider geocoding router với circuit breaker | `src/address/infrastructure/geocoding_router.py` |
| 8 | Ensemble scoring và xếp hạng đa ứng viên top-k | `src/address/domain/ranker.py` |
| 9 | Confidence gate fail-closed, cấm fallback mặc định | `src/address/domain/confidence_gate.py` |
| 10 | Minimum input guard chặn input quá ngắn và mơ hồ | `src/address/domain/input_guard.py` |
| 11 | Validator nhất quán phân cấp, kiểm chứng chéo hãng vận chuyển và reverse-geocode | `src/address/domain/validator.py` |
| 12 | Address Intelligence Service bất đồng bộ, idempotency và outbox | `src/address/services/address_service.py` |
| 13 | Structured address picker 3 cấp và autofill có trạng thái | `src/ui/components/StructuredAddressPicker.tsx` |
| 14 | Diff & Confirm UI bắt buộc khi có mâu thuẫn | `src/ui/components/AddressDiffConfirm.tsx` |
| 15 | Hàng đợi rà soát thủ công phân tầng rủi ro | `src/ops/review_queue.py` |
| 16 | Vòng lặp feedback từ SPX, correction store và active learning | `src/address/infrastructure/feedback_store.py` |
| 17 | Bảo mật PII địa chỉ và SĐT, quản trị secret và audit log | `src/security/address_pii_protection.py` |
| 18 | Quan trắc chất lượng, golden regression và cảnh báo drift | `src/address/infrastructure/observability.py` |
| 19 | Hợp đồng tích hợp Pancake, Goong và hãng vận chuyển | `src/integrations/pancake_goong_shipping_adapter.py` |
| 20 | Triển khai an toàn: shadow mode, A/B, canary và feature flag | `src/address/infrastructure/rollout.py` |

### 3.1. Nhóm dữ liệu chuẩn (hướng 2, 3)

- **Canonical Admin DB**: tỉnh/huyện/xã sau sáp nhập 2025 từ Tổng cục Thống kê, GHN, SPX và danh mục bưu chính, có mã hành chính, mã hãng vận chuyển, tên cũ/mới, alias, version và ngày hiệu lực. Ràng buộc khóa ngoại: xã thuộc huyện, huyện thuộc tỉnh — cấm tổ hợp chéo.
- **Normalizer**: chuẩn hóa Unicode NFC, bỏ dấu song song, viết tắt (`P.`, `Q.`, `H.`, `Tp.`, `Đ.`), tên dân tộc (`Cuôr`, `Đăk`, `Ea`, `Buôn`, `Krông`), sửa lỗi gõ Telex/VNI. Alias lưu dạng list cặp để không còn trùng khóa ghi đè im lặng.

### 3.2. Nhóm phân tích địa chỉ (hướng 4, 5, 6, 8)

- **Structured Parser** tách số nhà, đường, thôn/xóm, xã, huyện, tỉnh; phát hiện xung đột trường thay vì chọn bừa một trường.
- **Province Anchor** nhận diện tỉnh trước bằng Trie + Aho-Corasick, khóa toàn bộ tìm kiếm trong tỉnh đó. Mọi ứng viên khác tỉnh bị loại — đây là bản sửa trực tiếp cho ca `Cuôr Đăng, Đắk Lắk → Hải Dương`.
- **Fuzzy Matcher** chỉ chạy trong phạm vi đã khóa, kết hợp Levenshtein, Jaro-Winkler, Trigram, BM25 và embedding tiếng Việt; tên hiếm được cộng điểm IDF để thắng tên phổ biến.
- **Ranker** xếp hạng lại 10–20 ứng viên bằng Learning-to-Rank; chỉ tự điền khi top-1 vượt ngưỡng tuyệt đối **và** margin top-1 trừ top-2 đủ lớn, còn lại hiển thị tối đa 3 gợi ý để khách chọn một chạm.

### 3.3. Nhóm quyết định fail-closed (hướng 9, 10, 11)

- **Confidence Gate**: điểm 0–1 đã hiệu chuẩn; ≥ 0.85 và đủ 3 cấp mới auto-approve; 0.60–0.85 xác nhận tay; dưới 0.60 từ chối. **Xóa toàn bộ fallback mặc định `Xã Đăk Yă`**.
- **Input Guard**: chặn input dưới 10 ký tự chữ, chỉ có số (`73`, `127`), 1–2 âm tiết mơ hồ (`bi`, `Xom`), hoặc thiếu 2 cấp hành chính (`Sói 799, đường Lam Sơn`). Nhận diện cấp theo ranh giới từ / đoạn sau dấu phẩy, không dùng substring.
- **Validator**: kiểm tra xã ∈ huyện ∈ tỉnh, tên đường tồn tại trong xã, đối chiếu mã hãng vận chuyển tại thời điểm tạo nhãn và re-validate khi đổi hãng; GPS lệch tỉnh đồng thời anchor lệch tỉnh thì `BLOCKED`.

### 3.4. Nhóm hạ tầng và tích hợp (hướng 1, 7, 12, 19, 20)

- **AddressService** là orchestrator duy nhất, xử lý bất đồng bộ qua queue, idempotency-key theo `shop + order_id`, Transactional Outbox, retry backoff + jitter tối đa 3 lần rồi vào Dead Letter Queue.
- **Geocoding Router** gọi song song Goong, Pancake, Viettel Maps, Vietmap, OSM với một global deadline chung, circuit breaker và quota riêng từng provider; provider trả sai tỉnh bị veto.
- **Adapter Pancake/Goong/Shipping** chạy theo allowlist: chỉ `create_label` khi decision là OK/APPROVED, đủ 3 mã và Gate là VERIFIED. Mọi trạng thái khác đều chặn.
- **Rollout an toàn**: shadow mode 1–2 tuần, sau đó canary 5–10% rồi tăng dần, feature flag theo shop/khu vực, rollback một nút.

### 3.5. Nhóm trải nghiệm và vận hành (hướng 13, 14, 15, 16, 17, 18)

- **Structured Picker**: chọn Tỉnh → Huyện → Xã từ master data nội bộ, mỗi bước lọc theo mã cha; gợi ý Goong chỉ gợi ý, không điền ẩn.
- **Diff & Confirm**: khi địa chỉ hệ thống khác địa chỉ khách gõ ở bất kỳ cấp nào, hiển thị diff song song, tô đỏ cấp bị đổi; khách phải chủ động chọn, không có nút bỏ qua mặc định.
- **Review Queue** phân tầng Xanh/Vàng/Đỏ theo rủi ro; địa chỉ khách sửa qua link một chạm phải chạy lại validator + gate trước khi duyệt.
- **Feedback Loop**: webhook hoàn đơn SPX/GHN/GHTK gắn nhãn ground-truth, địa chỉ từng bị hoàn bị hạ điểm, cặp `raw_input → địa chỉ đúng` trở thành bộ nhớ ưu tiên.
- **Bảo mật PII**: mã hóa cột AES-GCM cho SĐT/địa chỉ, masking trong log, webhook ký HMAC, audit append-only hash-chain, rate limiting chống cạn quota.
- **Observability**: dashboard tỉ lệ VERIFIED / NEEDS_REVIEW / REJECTED / hoàn đơn theo tỉnh và provider; cảnh báo drift khi một tỉnh tăng tỉ lệ hoàn đột biến.

---

## 4. Biên bản phản biện, kiểm thử & các nhánh fix

### 4.1. Vòng kiểm định 1 — Chưa đồng thuận

Vòng audit đầu tiên kết luận **chưa đạt đồng thuận**: danh sách file công bố không khớp cây file thực tế trên `main`, nhiều file lõi không đọc được, thư mục `tests/golden`, `data/admin` và `src/address/api` còn trống, file `src/__init__.py` bị lỗi encoding mojibake.

Hội đồng ghi nhận **42 báo cáo lỗi** từ 5 thành viên. Sau trao đổi phản biện, Trưởng nhóm chốt khử trùng lặp thành **16 lỗi thực sự cần sửa**:

| Nhóm gộp | Số báo cáo | Thành 1 lỗi |
|---|---|---|
| Thiếu file pipeline / danh sách không khớp cây thật | 12 | `fix_missing_pipeline_core` |
| Golden test trống | 4 | `fix_missing_golden_tests` |
| Encoding mojibake | 3 | `fix_encoding_utf8` |
| Router (timeout, mismatch, rò rỉ) | 3 | `fix_router_deadline_mismatch_leak` |
| Admin data + map sáp nhập sai + thiếu ETL | 3 | `fix_admin_etl_merger_2025` |
| Idempotency + audit + PII | 3 | `fix_adapter_idempotency_audit_pii` |
| Adapter fail-open + thiếu gate | 2 | `fix_adapter_fail_closed_allowlist` |

**Mâu thuẫn snapshot đã xử lý:** có báo cáo vừa nói file thiếu vừa trích code trong file đó. Hội đồng quyết định khôi phục **đúng 1 bản canonical** từ nhánh expert sở hữu approach rồi mới áp các bản sửa logic lên trên, cấm viết lại bản thứ hai.

**Đã loại khỏi danh sách bắt buộc:** `fix_unplanned_registry` — chưa đủ bằng chứng là bug runtime, chỉ cần kiểm tra import và bổ sung đặc tả sau.

### 4.2. 16 nhánh fix — tất cả Test PASS, đã merge vào `main`

| # | Nhánh fix | Người sửa | Kết quả |
|---|---|---|---|
| 1 | `fix/fix_missing_pipeline_core` | council | PASS, merged |
| 2 | `fix/fix_missing_golden_tests` | fullstack-ux | PASS, merged |
| 3 | `fix/fix_admin_etl_merger_2025` | backend-sec | PASS, merged |
| 4 | `fix/fix_adapter_fail_closed_allowlist` | systems-arch | PASS, merged |
| 5 | `fix/fix_adapter_idempotency_audit_pii` | backend-sec | PASS, merged |
| 6 | `fix/fix_router_deadline_mismatch_leak` | systems-arch | PASS, merged |
| 7 | `fix/fix_validator_carrier_gps` | backend-sec | PASS, merged |
| 8 | `fix/fix_review_queue_state_machine` | backend-sec | PASS, merged |
| 9 | `fix/fix_fuzzy_exact_match_ignores_district` | algo-ml | PASS, merged |
| 10 | `fix/fix_input_guard_substring_false_level` | algo-ml | PASS, merged |
| 11 | `fix/fix_normalizer_alias_expansion` | algo-ml | PASS, merged |
| 12 | `fix/fix_aho_corasick_missed_mention` | algo-ml | PASS, merged |
| 13 | `fix/fix_schemas_forbidden_default_crash` | systems-arch | PASS, merged |
| 14 | `fix/fix_picker_suggestion_bypasses_hierarchy` | fullstack-ux | PASS, merged |
| 15 | `fix/fix_encoding_utf8` | fullstack-ux | PASS, merged |
| 16 | `fix/fix_observability_dead_code` | fullstack-ux | PASS, merged |

Phân công cân bằng: mỗi chuyên gia đúng 4 lỗi — systems-arch (1, 4, 6, 13), backend-sec (3, 5, 7, 8), algo-ml (9–12), fullstack-ux (2, 14–16). Thứ tự merge có ràng buộc: lỗi 5 chỉ làm sau khi lỗi 4 merge + rebase; lỗi 7, 10, 12, 13 áp trên bản canonical của lỗi 1.

Các lỗi nghiêm trọng nhất đã sửa:

- **Fail-open ở adapter**: trạng thái lạ (`REJECTED`, `ERROR`, enum mới) trước đây vẫn rơi xuống `create_label` — nay đảo thành allowlist, chỉ tạo nhãn khi đủ điều kiện.
- **Map sáp nhập sai `68 Lâm Đồng → 62 Kon Tum`**: đã xóa toàn bộ map hard-code; Lâm Đồng giữ mã 68, mapping chỉ nạp từ CSV chính thức và chỉ áp cho mã cũ.
- **Idempotency key chứa `raw_input`**: sửa địa chỉ sau review trước đây tạo nhãn trùng — nay key neo theo `shop + order_id`, sửa sau commit phải hủy nhãn cũ.
- **Audit plaintext + ghi đè**: nay audit append-only hash-chain, `raw_input` mã hóa AES-GCM, chỉ xuất dạng masked.
- **`customer_confirmed` duyệt trực tiếp**: nay địa chỉ khách sửa phải chạy lại validator + gate; link một chạm có token ký và hết hạn.
- **Schema `raise` khi gặp địa chỉ cấm**: nay trả `status + reason_code FORBIDDEN_DEFAULT`, Gate trả `REJECTED_INSUFFICIENT`, không crash pipeline.

### 4.3. Vòng kiểm định 2 — 100% đồng thuận

Sau khi 16 nhánh fix merge vào `main`, Hội đồng kiểm định lại toàn bộ repo: cây file đầy đủ, dữ liệu admin đã có, golden test đã có, CI chạy xanh. **Cả 5 thành viên đồng thuận 100%.**

### 4.4. Bộ test hồi quy bắt buộc (8 golden cases)

File `tests/golden/golden_cases_vn.json` khóa hành vi cho đúng các ca trong đề bài:

1. Không đọc được → `REJECTED`, không bao giờ ra `Xã Đăk Yă, Mang Yang, Gia Lai`.
2. `xã Cuôr Đăng, Đắk Lắk` → tỉnh phải là Đắk Lắk, không bao giờ Hải Dương.
3. `Xom` → `REJECTED` / `NEEDS_REVIEW`, không `VERIFIED`.
4. `Sói 799, đường Lam Sơn` thiếu ngữ cảnh → `REJECTED` / `NEEDS_REVIEW`.
5. `73` → `REJECTED`.
6. `127` → `REJECTED`.
7. `bi` / `Bị` → `REJECTED`.
8. Đơn đã khai báo tỉnh mà kết quả lệch tỉnh → chặn 100%.

Cộng thêm ca hợp lệ đối chứng. Toàn bộ test suite gồm golden regression + 20 file test đơn vị/tích hợp (`test_pipeline_smoke`, `test_adapter_allowlist`, `test_geocoding_router`, `test_fuzzy_exact_match_district_veto`, `test_input_guard_word_boundary`, `test_aho_corasick_missed_mention`, `test_schemas_forbidden_default`, `test_review_queue_state_machine`, `test_picker_hierarchy`, `test_encoding_utf8`, …).

---

## 5. Cài đặt, sử dụng & kiểm chứng

### 5.1. Yêu cầu

- Python 3.11+
- PostgreSQL 14+ với PostGIS (cho Canonical Admin DB ở production; bản dev có thể chạy với seed CSV)
- Redis (cache tra cứu tỉnh/huyện/xã và hàng đợi)
- Node.js 18+ (cho UI components, nếu chạy frontend)

### 5.2. Cài đặt

```bash
git clone https://github.com/HTL2910/muse-1-vn-address-autofill
cd muse-1-vn-address-autofill

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 5.3. Nạp dữ liệu hành chính chuẩn

```bash
# Nạp seed tối thiểu (đủ 8 tỉnh trong các ca lỗi) từ data/admin/
python -m src.address.infrastructure.admin_boundary_store --seed data/admin/admin_units_seed.csv

# Nạp đầy đủ từ CSV chính thức (có version + ngày hiệu lực)
python -m src.address.infrastructure.admin_boundary_store --etl data/admin/
```

ETL chạy trong một transaction: staging → validate hierarchy → checksum → insert → ghi audit → activate version. Rollback bằng cách activate lại version cũ.

### 5.4. Kiểm tra nhanh trước khi chạy

```bash
# 1. Cây file + hash khớp manifest
python scripts/manifest_check.py

# 2. Encoding UTF-8 toàn repo (bắt mojibake trong code và dữ liệu)
python scripts/encoding_check.py

# 3. Smoke test toàn pipeline
pytest tests/test_pipeline_smoke.py -v
```

### 5.5. Chạy kiểm thử

```bash
# Bộ golden regression bắt buộc — phải pass 100% trước merge
pytest tests/golden -v

# Toàn bộ test suite
pytest tests/ -v
```

CI (`.github/workflows/address-pipeline.yml`) chạy manifest check, encoding check và toàn bộ test ở mỗi pull request; golden test đỏ thì chặn merge.

### 5.6. Sử dụng — luồng chuẩn qua AddressService

Mọi lời gọi đều đi qua `AddressService` (entry point duy nhất). Không gọi trực tiếp provider hay adapter.

```python
from src.address.services.address_service import AddressService

service = AddressService(...)
result = service.resolve_address(
    order_id="ORD-123",
    raw_address="xã Cuôr Đăng, Đắk Lắk",
    phone="+84...",
)

# result.status: VERIFIED | NEEDS_REVIEW | REJECTED_INSUFFICIENT | BLOCKED_UNVERIFIED
# result.province_code / district_code / ward_code: mã chuẩn nội bộ
# result.confidence: điểm tin cậy 0-1
# result.reason_code: lý do khi bị chặn (VD: FORBIDDEN_DEFAULT, MISSING_PROVINCE)
```

Luồng tích hợp Pancake: Pancake chỉ gửi đơn nháp → `AddressService` chuẩn hóa → adapter tạo vận đơn **chỉ khi** `VERIFIED` và đủ 3 mã. Đơn `NEEDS_REVIEW` vào hàng đợi CS; đơn `REJECTED` yêu cầu khách bổ sung, không tạo vận đơn.

### 5.7. Kiểm chứng từng hướng giải quyết

| Hướng | Cách kiểm chứng |
|---|---|
| 1. Schema + pipeline | `pytest tests/test_pipeline_smoke.py -v` — import toàn pipeline, chạy end-to-end |
| 2. Admin DB + ETL | `pytest tests/test_admin_boundary_store.py -v` — golden test mã 60, 67, 68, 36, 54, 52 |
| 3. Normalizer | `pytest tests/test_vn_normalizer_alias_expansion.py -v` — trùng khóa alias, `Đắk Lắk`, `Cuôr Đăng` |
| 4. Structured Parser | Golden test + `pytest tests/test_validator_raw_house_street.py -v` |
| 5. Province Anchor | `pytest tests/test_aho_corasick_missed_mention.py -v` + golden case Cuôr Đăng |
| 6. Fuzzy Matcher | `pytest tests/test_fuzzy_exact_match_district_veto.py -v` — xã trùng tên khác huyện |
| 7. Geocoding Router | `pytest tests/test_geocoding_router.py -v` — deadline chung, đếm theo request, circuit breaker |
| 8. Ranker | `pytest tests/test_ranker_dedupe.py -v` — margin top-1/top-2, loại ứng viên lệch tỉnh |
| 9. Confidence Gate | `pytest tests/test_schemas_forbidden_default.py -v` — ca Đăk Yă về `REJECTED`, không crash |
| 10. Input Guard | `pytest tests/test_input_guard_word_boundary.py tests/test_input_guard_parsed_evidence.py -v` — `73`, `127`, `bi`, `Xom`, `Sói 799` |
| 11. Validator | `pytest tests/test_validator_carrier_gps.py tests/test_fix_validator_carrier_gps.py -v` |
| 12. Address Service | `pytest tests/test_pipeline_smoke.py -v` — idempotency, outbox, retry, DLQ |
| 13. Address Picker | `pytest tests/test_picker_hierarchy.py -v` + kiểm tra thủ công flow Tỉnh → Huyện → Xã |
| 14. Diff & Confirm | Kiểm tra thủ công: nhập địa chỉ lệch tỉnh, xác nhận modal diff tô đỏ cấp bị đổi |
| 15. Review Queue | `pytest tests/test_review_queue_state_machine.py -v` — state machine, re-validate sau khi khách sửa |
| 16. Feedback Loop | Kiểm tra `feedback_store` nhận webhook hoàn SPX, địa chỉ từng hoàn bị hạ điểm |
| 17. Bảo mật PII | `pytest tests/test_adapter_idempotency_audit.py tests/test_fix_adapter_idempotency_audit_pii.py -v` — audit mã hóa, không plaintext |
| 18. Observability | `pytest tests/test_observability_dead_code.py -v` + dashboard tỉ lệ VERIFIED / hoàn đơn theo tỉnh |
| 19. Adapter tích hợp | `pytest tests/test_adapter_allowlist.py -v` — trạng thái lạ không tạo nhãn |
| 20. Rollout an toàn | Chạy shadow mode 1–2 tuần, đối chiếu log chênh lệch với hệ thống cũ trước khi canary |

### 5.8. Triển khai production (khuyến nghị)

1. Chạy shadow mode 1–2 tuần, chỉ ghi log chênh lệch so với Goong/Pancake cũ, chưa chặn đơn.
2. Canary 5–10% đơn, theo dõi tỉ lệ hoàn và tỉ lệ `NEEDS_REVIEW` trên dashboard.
3. Tăng dần 50% rồi 100%, feature flag theo shop/khu vực để tắt nhanh khi có sự cố.
4. Theo dõi KPI hàng tuần: sai tỉnh, hoàn do sai địa chỉ, thời gian duyệt trung bình của hàng đợi CS.

---

## Hội đồng thực hiện

| Thành viên | Vai trò |
|---|---|
| **muse** | CEO / Team Lead — điều phối Hội đồng, chốt kiến trúc, khử trùng lặp, tổng kết báo cáo |
| **systems-arch** | Chuyên gia Kiến trúc Hệ thống |
| **algo-ml** | Chuyên gia Thuật toán & AI |
| **backend-sec** | Chuyên gia Backend & Bảo mật |
| **fullstack-ux** | Chuyên gia Ứng dụng & Tích hợp |

Tài liệu làm việc của Hội đồng lưu tại `docs/hoi-dong-nghien-cuu/`. Kiến trúc chi tiết xem `docs/ARCHITECTURE.md`.

---

*Báo cáo tổng kết bởi Trưởng nhóm muse, Hội đồng Chuyên gia AI MuseAssistantTeam — 22/11/2026. Đạt 100% đồng thuận sau 2 vòng kiểm định.*
