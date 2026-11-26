# shopbmsx — đồng bộ Shop Xoài Non / Nastele sang Google Sheets + Docs

Hệ thống Python đọc danh mục **Shop Xoài Non Customer API v1** (và **Nastele Partner
API**), ghi vào **một** Google Spreadsheet có sẵn, dựng bảng giá khách trên tab
`MENU` và thêm báo cáo vào một Google Document có sẵn.

Hệ thống **không** tạo đơn, không trừ ví Telegram, không lấy thông tin tài khoản đã
giao. `POST /orders` và `/delivery` bị chặn trước khi mở kết nối.

## Đang chạy những gì

- Đọc catalog, tính giá bán từ giá gốc + lợi nhuận, ghi `SAN_PHAM`, `BIEN_THE`,
  `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH`.
- Hợp nhất `BAO_GIA` (từ API shop) và `NASTELEID` (nhập tay) thành `MENU` và
  `BAO_GIA_TONG_HOP`; chỉ ghi cột `A:H`, không đụng `J:M`.
- `sync-menu` chạy trên GitHub Actions: preflight → snapshot bền vùng sắp ghi vào
  tab `_MENU_SYNC_BACKUP` → ghi MENU → gom sản phẩm cùng họ về cạnh nhau.
- Tự mở rộng vùng MENU khi nguồn nhiều hơn số dòng trống.
- So sánh hai nhà cung cấp: `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`,
  `CAN_GHEP_SAN_PHAM`, các tab `NASTELE_*`.
- Web Service Render chỉ để `/health` và gọi tay; **luôn dry-run**, scheduler tắt.

## Cấu trúc

```text
src/shop_integration/     mã nguồn (api, pricing, sheets, menu, suppliers, catalog)
tests/                    382 test, không cần mạng
docs/                     setup_vi.md, architecture.md, operations.md
docs/archive/             tài liệu phase cũ (chỉ để tham khảo)
scripts/                  start_web.py (Render), render_sync.sh (chạy tay)
data/                     menu-baseline.json, content_overrides.json
.github/workflows/        ci.yml, sync-menu.yml, sync-sources.yml
backups/                  snapshot local, đã gitignore, không commit
```

## Cài đặt nhanh

```powershell
cd shopbmsx
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Điền `.env`: `SHOP_XOAI_NON_API_KEY`, `GOOGLE_SPREADSHEET_ID`, và đặt JSON service
account tại `./GOOGLE_CREDENTIALS_FILE.json` (hoặc trỏ `GOOGLE_CREDENTIALS_FILE`).
Chia sẻ spreadsheet và document cho email service account với quyền **Người chỉnh
sửa**. Không commit `.env` hay file JSON đó.

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration config-check
python -m shop_integration ping
python -m shop_integration sync-menu        # DRY_RUN=true: chỉ lập kế hoạch
```

`DRY_RUN=true` là mặc định: mọi lệnh chỉ đọc. Chỉ đặt `DRY_RUN=false` khi thật sự
muốn ghi Google, và chỉ sau khi `backup`/preflight đã đạt.

## Lệnh chính

| Lệnh | Việc làm |
| --- | --- |
| `config-check` | In cấu hình hiệu lực, biến cũ bị bỏ qua. Không gọi mạng. |
| `ping`, `products`, `product <id>`, `wallet` | Đọc API shop; chỉ in số đếm/mã khóa. |
| `quote` | `POST /products/{id}/quote`. Không tạo đơn, không trừ ví. |
| `quote-layout` | In bảng cột chuẩn của spreadsheet. |
| `sync` | Đồng bộ catalog sang Sheets, ghi Docs nếu đã cấu hình. |
| `sync-menu` | API shop → `BAO_GIA` → `MENU`/`BAO_GIA_TONG_HOP`, kèm guard + backup bền. |
| `sync-menu-preflight --verify-backup` | Cổng an toàn trước khi ghi MENU. |
| `menu-baseline [--out|--compare]` | Chụp/so cấu trúc MENU (merge, công thức, dropdown, protected range). |
| `menu-audit` | Đọc MENU, đối chiếu ProductID/VariantID, dòng lạ. Chỉ đọc. |
| `menu-migrate` | Lập kế hoạch migration schema nguồn; mặc định chạy thử. |
| `sync-sources`, `sources` | Đọc Nastele, so giá, ghi tab nguồn khi không dry-run. |
| `backup`, `backup-check`, `backup-readiness` | Snapshot Sheets/Docs, kiểm checksum, đánh giá đủ điều kiện live. |
| `restore-plan`, `restore --confirm` | Đối chiếu và khôi phục khi không còn xung đột. |
| `report` | So sánh giá và ghi báo cáo vào Google Doc. |
| `content-override` | Đọc/ghi khóa nội dung bán hàng trong `data/content_overrides.json`. |

## An toàn

- API key và JSON service account chỉ nằm trong biến môi trường hoặc `.env`.
- Nhật ký, Sheets, Docs được lọc nếu vô tình chứa API key dài từ 8 ký tự.
- Chuyển hướng HTTP bị từ chối để không gửi key sang máy chủ khác.
- `ENABLE_PURCHASE` và `ENABLE_TELEGRAM` luôn `false`; mã không có đường tạo đơn.
- Render không ghi MENU. GitHub Actions `sync-menu` là scheduler ghi duy nhất;
  cron `*/5` có thể trễ.
- Mọi lượt ghi MENU đều có snapshot bền trên tab `_MENU_SYNC_BACKUP` trước khi ghi;
  thiếu backup `verified` thì không ghi.

## Tài liệu

- Cài đặt, cấu hình, triển khai: [docs/setup_vi.md](docs/setup_vi.md)
- Kiến trúc, hợp đồng cột, khóa layout MENU: [docs/architecture.md](docs/architecture.md)
- Lịch chạy, backup, khôi phục, xử lý lỗi: [docs/operations.md](docs/operations.md)

## Kiểm thử

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

382 test, dùng HTTP giả lập và gateway Google trong bộ nhớ; không cần key thật và
không gọi mạng. CI chạy cùng lệnh này trên mỗi push/PR.
