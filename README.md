# Dự án Nghiên cứu #1

## Đề bài

tôi có một bài toán như này : một cửa hàng bán đồ tiêu dùng cho khách trên Việt Nam qua các đơn đặt giao hàng trên toàn quốc do số lượng khách hàng đặt đơn qua app tăng mỗi ngày có thể đến hàng ngàn đơn trên ngày nên cần có hệ thống autofill address đang dùng là goong.io và pancake thì tỉ lệ giao đơn thành công chỉ cỡ 60-70 % và bị gặp một số sự cố như sai địa chỉ nên bị hoàn đơn gây giảm doanh thu và độ uy tín của shop á gặp một số sự có được user sử dụng hệ thống feedback như sau: Không đọc được thì điền mặc định “Xã Đăk Yă, Mang Yang, Gia Lai”;Khách ghi rõ tỉnh mà máy điền tỉnh khác;khách ghi “xã Cuôr Đăng, Đắk Lắk” → máy điền Phường Phả Lại, Chí Linh, Hải Dương.;Chữ quá ít mà vẫn điền và cho đi :“Xom” → Thanh Mỹ, Nghệ An,“Sói 799, đường Lam Sơn” → P2 Tân Bình. SPX báo sai địa chỉ (hoàn)v“73” → Hợp Giang, Cao Bằng,“127" → Hiến Nam, Hưng Yên,“bi” / “Bị” → Phú Cường, Thủ Dầu Một  ; hiểu đề bài đi ,tôi cần một giải pháp

## Hội đồng Chuyên gia (MuseAssistantTeam)

| Chuyên gia | Vai trò | Chuyên ngành |
|---|---|---|
| muse | CEO | Ban giám đốc |
| systems-arch | Chuyên gia Kiến trúc Hệ thống | Hội đồng Nghiên cứu |
| algo-ml | Chuyên gia Thuật toán & AI | Hội đồng Nghiên cứu |
| backend-sec | Chuyên gia Backend & Bảo mật | Hội đồng Nghiên cứu |
| fullstack-ux | Chuyên gia Ứng dụng & Tích hợp | Hội đồng Nghiên cứu |

## Quy trình thực hiện trên Repo

- Trưởng nhóm định nghĩa cấu trúc thư mục chuẩn trên `main` và tổng hợp các hướng giải quyết không trùng lặp.
- Mỗi chuyên gia nhận các hướng được chia đều, triển khai trên nhánh riêng và gộp vào `main` (có thương lượng giải quyết conflict).
- Toàn bộ hội đồng rà soát chéo trên `main`, mở nhánh `fix/<ten_loi>`, chạy kiểm thử tự động rồi gộp cho tới khi đạt 100% đồng thuận.
