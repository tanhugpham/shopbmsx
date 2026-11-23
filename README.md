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
| `quote-layout` | In bảng cột thống nhất của file catalog và file báo giá tổng hợp. |
| `sync` | Đồng bộ danh mục sang spreadsheet, và sang document nếu đã cấu hình. |
| `report` | Đọc danh mục, so sánh giá, ghi document khi không chạy thử. |

## An toàn

- API key chỉ nằm trong biến môi trường hoặc `.env`.
- Nhật ký, Sheets và Docs được lọc nếu vô tình chứa API key dài từ 8 ký tự.
- Mọi chuyển hướng HTTP bị từ chối để không gửi API key sang máy chủ khác.
- `POST /orders` và `GET /orders/{id}/delivery` bị chặn trước khi mở kết nối.
- `ENABLE_PURCHASE=true` và `ENABLE_TELEGRAM=true` bị bỏ qua.

Deploy Render miễn phí (Web Service, không Cron, không thẻ): [docs/render_free.md](docs/render_free.md).

Chi tiết cài đặt: [docs/setup_vi.md](docs/setup_vi.md).

Kiến trúc và hợp đồng API: [docs/architecture.md](docs/architecture.md).

Bảng cột file catalog và file báo giá tổng hợp: [docs/bao_gia_tong_hop.md](docs/bao_gia_tong_hop.md).
