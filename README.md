# Tích hợp Shop Xoài Non, Google Sheets và Google Docs

Hệ thống Python đọc danh mục Shop Xoài Non Customer API v1, ghi vào một Google Spreadsheet có sẵn và thêm báo cáo vào một Google Document có sẵn.

Hệ thống không tạo đơn, không trừ ví Telegram và không lấy thông tin tài khoản đã giao. Bộ điều hợp Telegram chỉ là giao diện cho giai đoạn sau, không mở kết nối mạng.

## Yêu cầu

- Python 3.11 trở lên
- Một API key `x-api-key` của Shop Xoài Non
- Một spreadsheet và một document đã tạo sẵn, chia sẻ quyền Sửa cho service account

## Cài đặt nhanh

```powershell
cd shopxoainon-integration
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Điền `.env` trên máy local. Không commit file này. Đặt JSON service account tại `./GOOGLE_CREDENTIALS_FILE.json` trong thư mục gốc dự án, hoặc ghi đường dẫn khác vào `GOOGLE_CREDENTIALS_FILE`. Đường dẫn tương đối luôn tính từ thư mục gốc dự án.

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration ping
python -m shop_integration products
python -m shop_integration sync
```

`DRY_RUN=true` là mặc định. Lệnh `sync` khi đó chỉ đọc API và Sheets, rồi in báo cáo. Đặt `DRY_RUN=false` khi muốn ghi file Google.

## Lệnh

| Lệnh | Việc làm |
| --- | --- |
| `ping` | `GET /me`. Chỉ in tên khóa JSON, không in giá trị hồ sơ. |
| `products` | Đếm toàn bộ sản phẩm qua `limit` và `offset`. |
| `product <id>` | `GET /products/{id}` với các trường đã được tài liệu xác nhận. |
| `wallet` | `GET /wallet/balance`. Schema chưa được OpenAPI mô tả. |
| `orders`, `order <id>` | Chỉ in mã đơn và trạng thái. |
| `quote` | `POST /products/{id}/quote` khi được gọi tường minh. |
| `quote-layout` | In bảng cột thống nhất của spreadsheet duy nhất. |
| `sync` | Đồng bộ danh mục sang spreadsheet, và sang document nếu đã cấu hình. |
| `sync-menu` | Đồng bộ MENU từ API shop và NASTELEID. `DRY_RUN=true` chỉ lập kế hoạch. |
| `menu-baseline` | Chụp cấu trúc MENU (merge, công thức, dropdown, khổ cột/dòng) ra JSON. Chỉ đọc. `--compare` để kiểm tra drift. |
| `menu-audit` | Đọc MENU và đối chiếu ProductID/VariantID, hàng mở rộng A:H, dòng legacy ngoài K38. Chỉ đọc. |
| `menu-migrate` | Lập kế hoạch sửa `BAO_GIA`/`BAO_GIA_TONG_HOP` và MENU, chụp snapshot local, kiểm tra backup/guard. Mặc định chỉ chạy thử. |
| `report` | Đọc danh mục, so sánh giá, ghi document khi không chạy thử. |
| `config-check` | In cấu hình hiệu lực, biến cũ bị bỏ qua và tab LEGACY. Không gọi mạng. |

## An toàn

- API key chỉ nằm trong biến môi trường hoặc `.env`.
- Nhật ký, Sheets và Docs được lọc nếu vô tình chứa API key dài từ 8 ký tự.
- Mọi chuyển hướng HTTP bị từ chối để không gửi API key sang máy chủ khác.
- `POST /orders` và `GET /orders/{id}/delivery` bị chặn trước khi mở kết nối.
- `ENABLE_PURCHASE=true` và `ENABLE_TELEGRAM=true` bị bỏ qua.
- Render không ghi MENU: `ENABLE_SCHEDULED_SYNC=false`, `ENABLE_SCHEDULED_MENU_LIVE=false`. Workflow `sync-menu` là scheduler duy nhất, khoảng mỗi 5 phút sau preflight, với `DRY_RUN=false`. Cron GitHub có thể trễ.

Deploy Render miễn phí (Web Service, không Cron, không thẻ): [docs/render_free.md](docs/render_free.md).

Chi tiết cài đặt: [docs/setup_vi.md](docs/setup_vi.md).

Kiến trúc và hợp đồng API: [docs/architecture.md](docs/architecture.md).

Bảng cột của một spreadsheet dùng chung: [docs/bao_gia_tong_hop.md](docs/bao_gia_tong_hop.md).

Phase 4.3B — một spreadsheet, cấu hình sạch, tab LEGACY: [docs/phase43b_single_spreadsheet.md](docs/phase43b_single_spreadsheet.md).

Khóa layout MENU (A–H, merge, công thức, dropdown, J–M): mục 9 của [docs/phase43b_single_spreadsheet.md](docs/phase43b_single_spreadsheet.md#9-khóa-layout-menu-phase-43b).

Phase 4.3F — catalog hai tầng, adapter nhiều nhà cung cấp và MENU khách an toàn: [docs/phase43f_multi_supplier.md](docs/phase43f_multi_supplier.md).

Phase 4.3H — audit cuối, plan migration mới từ snapshot tươi và readiness auto-sync: [docs/phase43h_final_migration_readiness.md](docs/phase43h_final_migration_readiness.md).
