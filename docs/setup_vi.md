# Hướng dẫn cài đặt

Tài liệu này dành cho người vận hành trên Windows PowerShell.

## 1. Môi trường Python

Cần Python 3.11 trở lên. Repo dùng tên `shopbmsx`; Render pin Python `3.13.7`
qua `.python-version` và biến `PYTHON_VERSION`.

```powershell
cd shopbmsx
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Chạy lệnh và test với `PYTHONPATH` trỏ vào `src`:

```powershell
$env:PYTHONPATH = "src"
python -m pytest
python -m shop_integration --help
```

## 2. Biến môi trường

```powershell
Copy-Item .env.example .env
```

Điền các giá trị sau vào `.env`:

| Biến | Ý nghĩa |
| --- | --- |
| `SHOP_XOAI_NON_BASE_URL` | Mặc định `https://shopxoainon.online/api/v1`. Phải dùng HTTPS. |
| `SHOP_XOAI_NON_API_KEY` | API key dạng `sxn_live_...`, gửi trong header `x-api-key`. |
| `GOOGLE_SPREADSHEET_ID` | ID của spreadsheet duy nhất (catalog, nguồn, MENU, báo giá), lấy trên URL Google Sheets. |
| `GOOGLE_DOCUMENT_ID` | ID của document có sẵn. |
| `GOOGLE_CREDENTIALS_FILE` | Mặc định `./GOOGLE_CREDENTIALS_FILE.json`. Đường dẫn tương đối tính từ thư mục gốc dự án. Đường dẫn tuyệt đối vẫn dùng được. |
| `APP_ENV` | `development` hoặc tên môi trường khác. |
| `DRY_RUN` | `true` để không ghi Google. `false` để ghi. |
| `ENABLE_PURCHASE` | Giữ `false`. Giá trị `true` vẫn không mở được đường tạo đơn. |
| `ENABLE_TELEGRAM` | Giữ `false`. Chưa có bot vận hành. |
| `MENU_AUTO_EXPAND` | Mặc định `true`: MENU tự chèn thêm dòng khi nguồn có nhiều sản phẩm hơn vùng chọn `K`. `false` để giữ nguyên layout và chỉ ghi cảnh báo. |
| `MENU_EXPAND_STEP` | Số dòng chèn mỗi lần, 1–50, mặc định `10`. |
| `MENU_EXPAND_MAX_ROWS` | Trần số dòng chèn trong một lượt sync, 1–200, mặc định `40`. Thiếu hơn thì lượt sau chèn tiếp. |
| `MENU_GROUP_SIMILAR` | Mặc định `true`: gom sản phẩm cùng loại (ChatGPT, Canva, Google Drive, ...) về cạnh nhau trên MENU. Chỉ đổi thứ tự dòng A:H, không sửa dữ liệu, không ghi J:M. `false` để giữ nguyên thứ tự như nguồn trả về. |
| `REQUEST_TIMEOUT_SECONDS` | Thời gian chờ mỗi request. Mặc định 30 giây. |
| `NASTELE_BASE_URL` | Mặc định `https://nastele.online`. Phải dùng HTTPS. |
| `NASTELE_API_KEY` | Key Nastele, gửi trong header `X-API-Key`. Để trống nếu chưa có. Không ghi vào Git. |
| `PREFERRED_SUPPLIER_ID` | `shop_xoai_non` hoặc `nastele`. Dùng khi hai nguồn cùng giá. Mặc định `shop_xoai_non`. |
| `COMPARISON_QUANTITY` | Số lượng dùng để so giá. Mặc định `1`, nên không lấy giá sỉ của bậc 10 hay 25. |
| `NASTELE_RATE_LIMIT_PER_MINUTE` | Từ 1 đến 60. Trần tài liệu là 60 request mỗi phút cho mỗi key. |
| `ENABLE_SCHEDULED_SYNC` | Giữ `false`. Thread sync trong tiến trình web; GitHub Actions là scheduler duy nhất. |
| `ENABLE_SCHEDULED_MENU_LIVE` | Giữ `false`. Mọi lời gọi `/sync-menu` trên Render chỉ dry-run. |
| `SCHEDULED_SYNC_SECONDS` | Chu kỳ thread khi được bật, mặc định `300`. Không dùng khi scheduler tắt. |
| `MENU_BASELINE_FILE` | File baseline để so cấu trúc MENU trước khi ghi. Workflow đặt `data/menu-baseline.json`. |
| `MENU_REQUIRE_DURABLE_BACKUP` | `true` trên GitHub Actions: bắt buộc snapshot tab `_MENU_SYNC_BACKUP` trước khi ghi MENU. |
| `PRICING_RATE_LOW`, `PRICING_RATE_HIGH` | Tỷ lệ lợi nhuận dưới 100000 và trên 200000, mặc định `0.30`. |
| `PRICING_MIDDLE_MIN`, `PRICING_MIDDLE_MAX` | Khoảng áp mức lợi nhuận cố định, mặc định `100000`–`200000`. |
| `PRICING_MIDDLE_PROFIT` | Lợi nhuận cố định trong khoảng giữa, mặc định `35000`, chỉ nhận 30000–40000. |
| `SYNC_TOKEN` | Chỉ dùng trên Render: token cho `/sync-*` qua header `X-Sync-Token`. |
| `GOOGLE_CREDENTIALS_JSON` | Chỉ dùng trên Render/GitHub Actions: nội dung JSON service account, ghi ra file lúc khởi động. |

Không dán API key hay JSON service account vào mã nguồn, test, Google Sheets, Google
Docs, ảnh chụp hoặc Git.

## 3. Google Cloud

1. Tạo một project Google Cloud, hoặc dùng project đã được duyệt.
2. Bật **Google Sheets API** và **Google Docs API**.
3. Tạo service account. Đặt JSON key tại thư mục gốc dự án với tên `GOOGLE_CREDENTIALS_FILE.json`, hoặc một đường dẫn tương đối hay tuyệt đối khác.
4. Ghi đường dẫn đó vào `GOOGLE_CREDENTIALS_FILE` trong `.env` nếu không dùng tên mặc định. File này đã nằm trong `.gitignore`.

Phạm vi OAuth mà mã xin:

- `https://www.googleapis.com/auth/spreadsheets`
- `https://www.googleapis.com/auth/documents`

Mã không xin quyền Google Drive. Vì vậy service account không tự tìm file. Bạn phải đưa ID và chia sẻ từng file.

## 4. Chia sẻ file có sẵn

1. Mở spreadsheet. Chọn Chia sẻ. Thêm email service account, quyền **Người chỉnh sửa**.
2. Làm tương tự với Google Doc, quyền **Người chỉnh sửa**.
3. Không để chương trình tạo spreadsheet hoặc document mới.

Khi `DRY_RUN=false`, lần ghi đầu có thể tạo các tab còn thiếu trong spreadsheet đó:

- `SAN_PHAM`
- `BIEN_THE`
- `BANG_GIA_BAN`
- `LICH_SU_GIA`
- `NHAT_KY_DONG_BO`
- `CAU_HINH`

Tab đã có được giữ nguyên. Cột lạ do người dùng thêm ở cuối hàng tiêu đề được giữ. Dòng sản phẩm không còn trong API được đánh dấu `Không còn trong API`, không bị xóa và không bị đổi thành hết hàng. Giá và trạng thái tồn kho lần trước được giữ.

## 5. Kiểm tra kết nối

Giữ `DRY_RUN=true` cho lần đầu.

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration ping
python -m shop_integration products
python -m shop_integration wallet
```

`ping` thành công khi `GET /me` trả HTTP 200. Lệnh này không in giá trị hồ sơ khách.

`products` in số sản phẩm sau khi đi hết các trang `limit`/`offset`. Đây là lệnh dùng để kiểm tra đọc thật.

`wallet` có thể in một số nguyên nếu phản hồi chỉ chứa một số. Trường số dư chưa có trong OpenAPI, nên dòng in ra ghi rõ là chưa được tài liệu xác nhận.

## 6. Đồng bộ

```powershell
python -m shop_integration sync
```

Với `DRY_RUN=true`, lệnh đọc API và, nếu đã có credentials, đọc Sheets để tính thay đổi giá. Không có ô nào bị ghi.

Trước lần ghi thật, tạo và kiểm tra backup. Giữ `DRY_RUN=true` cho đến khi được phê duyệt.

```powershell
python -m shop_integration backup
python -m shop_integration backup-check
python -m shop_integration restore-plan
```

`backup` đọc Sheets và Docs rồi lưu snapshot vào `backups/`. Thư mục này không được đưa lên Git. Snapshot không phải bản sao hoàn chỉnh: không có định dạng, biểu đồ, ảnh hay comment. Ô chứa bí mật được che trước khi lưu.

## 7. Nastele và chọn nguồn rẻ nhất

Nastele là nhà cung cấp thứ hai. Client của Shop Xoài Non không bị thay. Lệnh đọc:

```powershell
python -m shop_integration sources
```

Lệnh này gọi `GET /api/partner/products` khi có `NASTELE_API_KEY`. Không gọi đơn hàng, không lưu tài khoản, không ghi Sheets. Nếu thiếu key, dòng trạng thái là `BLOCKED`, không phải danh mục giả.

```powershell
python -m shop_integration sync-sources
```

Với `DRY_RUN=true`, lệnh chỉ in đối chiếu. Khi `DRY_RUN=false`, lệnh tạo backup trước rồi mới ghi các tab nguồn, tách khỏi danh mục Shop Xoài Non:

- `SO_SANH_NGUON`: một dòng cho mỗi sản phẩm chuẩn. Có mã Shop Xoài Non, mã Nastele, giá, tồn kho, nguồn được chọn, chênh lệch, lợi nhuận và giá bán.
- `BANG_GIA_TOI_UU`: bảng giá cho khách. Không có giá vốn, lợi nhuận hay API key.
- `CAN_GHEP_SAN_PHAM`: cặp cần đối chiếu. Điền `Review Status` là `EXACT_MATCH` hoặc `DIFFERENT_PRODUCT`, và có thể điền `Approved Canonical Product ID`. Lần sau không ghi đè hai ô này.

Tab Nastele dùng chung spreadsheet `GOOGLE_SPREADSHEET_ID`. Thiếu ID hoặc chưa có quyền Sửa thì trạng thái là BLOCKED. Không tự tạo file Google. Khi đã có quyền, dữ liệu Nastele vào `NASTELE_SAN_PHAM`, `NASTELE_GIA_SI`, `NASTELE_LICH_SU_GIA`, `NASTELE_NHAT_KY` và `NASTELE_CAU_HINH` trong cùng file.

Chỉ cặp `EXACT_MATCH` mới được so giá. Giá sỉ Nastele chỉ dùng khi `minQuantity` nhỏ hơn hoặc bằng `COMPARISON_QUANTITY`. Nguồn rẻ hơn nhưng hết hàng không được chọn. Dữ liệu cũ hoặc thiếu được đánh `CẦN XÁC MINH` và không bị ghi tồn kho 0.

`backup-check` kiểm tra checksum. `restore-plan` chỉ đọc và báo xung đột. `restore --confirm` mới ghi khôi phục, và chỉ khi không có xung đột chặn. Lệnh này không ghi đè `Trang tính1`.

Khi ghi thật, sync từ chối chạy nếu backup chưa `verified`. Sheets được đọc lại để đếm dòng trước khi ghi Docs. Nếu Docs lỗi, Sheets không bị tự động khôi phục. Chạy lại không tạo thêm dòng sản phẩm và không tạo thêm vùng báo cáo nếu mã nội dung không đổi.

`BANG_GIA_BAN` và Google Doc chỉ hiện giá bán cho khách, không hiện giá gốc hay lợi nhuận. Doc là báo giá: số thứ tự, tên sản phẩm, giá bán, tình trạng. Không có mã `local_…`. Biến thể thiếu giá gốc bị bỏ khỏi tab giá khách và được đánh dấu `Thiếu giá` ở `BIEN_THE`.

Giá bán tính từ `prices.vnd`. Dưới 100000 VND và trên 200000 VND, lợi nhuận mặc định là 30% giá gốc, làm tròn nửa lên về đồng. Từ 100000 đến 200000, lợi nhuận mặc định là 35000 VND. Có thể đổi bằng biến môi trường, không bắt buộc vì mã đã có mặc định:

```text
PRICING_RATE_LOW=0.30
PRICING_RATE_HIGH=0.30
PRICING_MIDDLE_MIN=100000
PRICING_MIDDLE_MAX=200000
PRICING_MIDDLE_PROFIT=35000
```

`PRICING_MIDDLE_PROFIT` chỉ nhận từ 30000 đến 40000. Tên và mô tả được giải mã thực thể HTML, escape `\u`, và bỏ thẻ. Tiếng Việt có dấu được giữ. Ô số lượng vẫn phân biệt `Không được cung cấp`, `null`, số 0 và trạng thái hết hàng.

Báo cáo Google Docs nằm trong một vùng có dấu `SHOP_XOAI_NON_BAO_CAO_BEGIN` và `SHOP_XOAI_NON_BAO_CAO_END`. Lần chạy sau thay vùng đó. Nội dung ngoài hai dấu không bị ghi đè. Nếu chỉ thấy một dấu, lệnh dừng và không sửa document.

## 8. Một file Google cho catalog và báo giá

Phase 4.3B dùng **một** spreadsheet `GOOGLE_SPREADSHEET_ID` cho catalog, nguồn, MENU và báo giá. Không copy dòng từ tab catalog sang tab khách.

| Khu vực | Biến | Tab bạn xem / sửa |
| --- | --- | --- |
| Catalog Shop Xoài Non | `GOOGLE_SPREADSHEET_ID` | `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`. Nội bộ, có giá gốc. |
| Nguồn và ghép sản phẩm | `GOOGLE_SPREADSHEET_ID` | `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM`. |
| Báo giá chính thức | `GOOGLE_SPREADSHEET_ID` | `NASTELEID` (sửa tay), `BAO_GIA` (tool, từ API), `MENU` (ghép hai tab trên), `BAO_GIA_TONG_HOP` (tool). |

`NASTELEID` và `BAO_GIA` khóa **cùng một dòng tiêu đề** với các cột sản phẩm của `MENU`. `ID` là số thứ tự, không lấy `local_id` API. `MENU` thêm cột `STATUS`.

| ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | tên khách thấy | mô tả tay | thời hạn | bảo hành | giá đã gõ | số lượng |
| 1 | tên shop đã ghép biến thể | `description` | suy ra từ tên | suy ra từ tên | giá đã làm tròn đẹp | số lượng API |

Không đặt `Giá gốc` hay `Product ID` vào file này. Hợp đồng cột đầy đủ:
[architecture.md](architecture.md#4-hợp-đồng-cột-báo-giá).

```powershell
python -m shop_integration quote-layout
```

## 9. Báo giá API

Báo giá không nằm trong đồng bộ định kỳ.

```powershell
python -m shop_integration quote --product-id local_81 --variant-id local_81 --quantity 1
```

Lệnh này gọi `POST /products/{id}/quote`. Nó không tạo đơn và không trừ ví. Request báo giá không được thử lại tự động.

## 10. Kiểm thử tự động

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

Test dùng HTTP giả lập và gateway Google trong bộ nhớ. Chúng không cần API key thật và không gọi mạng Shop Xoài Non hay Google.

## 11. Triển khai Render và GitHub Actions

### 11.1 Render Web Service (miễn phí, không thẻ)

Cron trên Render cần gói trả phí, nên repo chỉ dùng **Web Service**. Lịch ghi MENU
do GitHub Actions đảm nhiệm.

1. https://dashboard.render.com → **New → Web Service** → chọn repo
   `tanhugpham/shopbmsx`, nhánh `main`.
2. Điền: Name `shopxoainon-web`, Region Singapore, Runtime Python,
   **Instance type Free**.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `PYTHONPATH=src python scripts/start_web.py`
   - Health Check Path: `/health`
3. Thêm biến môi trường:

| Key | Value |
| --- | --- |
| `PYTHONPATH` | `src` |
| `PYTHON_VERSION` | `3.13.7` |
| `DRY_RUN` | `true` |
| `ENABLE_SCHEDULED_SYNC` | `false` |
| `ENABLE_SCHEDULED_MENU_LIVE` | `false` |
| `ENABLE_PURCHASE` | `false` |
| `ENABLE_TELEGRAM` | `false` |
| `SHOP_XOAI_NON_BASE_URL` | `https://shopxoainon.online/api/v1` |
| `NASTELE_BASE_URL` | `https://nastele.online` |
| `GOOGLE_CREDENTIALS_FILE` | `./GOOGLE_CREDENTIALS_FILE.json` |
| `SHOP_XOAI_NON_API_KEY`, `NASTELE_API_KEY` | key thật (secret) |
| `GOOGLE_SPREADSHEET_ID`, `GOOGLE_DOCUMENT_ID` | ID file đã chia sẻ |
| `GOOGLE_CREDENTIALS_JSON` | nguyên JSON service account |
| `SYNC_TOKEN` | chuỗi bí mật dài 20+ ký tự |

4. Mở `https://<service>.onrender.com/health`: JSON phải có `"status": "ok"` và
   `"dry_run": true`. Free sẽ ngủ sau ~15 phút, lần đầu có thể chờ ~1 phút.

Gọi tay (vẫn dry-run, không ghi Google):

```powershell
$env:TOKEN = "SYNC_TOKEN_BAN_DAT"
Invoke-WebRequest -Uri "https://<service>.onrender.com/sync-sources" -Method POST `
  -Headers @{ "X-Sync-Token" = $env:TOKEN }
```

Không có token → HTTP 403. Render **không** ghi MENU: `ENABLE_SCHEDULED_SYNC=false`,
`ENABLE_SCHEDULED_MENU_LIVE=false`, và `DRY_RUN=true` bị khóa trong mã.

### 11.2 GitHub Actions

Repo → **Settings → Secrets and variables → Actions**, thêm:

| Secret | Dùng cho |
| --- | --- |
| `SHOP_XOAI_NON_API_KEY` | `sync-menu`, `sync-sources` |
| `GOOGLE_SPREADSHEET_ID` | `sync-menu`, `sync-sources` |
| `GOOGLE_CREDENTIALS_JSON` | ghi file credentials trên runner |
| `NASTELE_API_KEY` | `sync-sources` (tùy chọn) |
| `GOOGLE_DOCUMENT_ID` | báo cáo Docs (tùy chọn) |

| Workflow | Lịch | Ghi Google |
| --- | --- | --- |
| `sync-menu.yml` | cron `*/5 * * * *` (thực tế trễ hơn), có `workflow_dispatch` | Có: preflight → backup bền → `DRY_RUN=false` |
| `sync-sources.yml` | `17 1 * * *` UTC | Không: luôn `DRY_RUN=true` |
| `ci.yml` | mỗi push/PR | Không: chỉ `pytest` |

Push lên `main` chỉ deploy khi check `ci` đạt (`autoDeployTrigger: checksPass`).

### 11.3 Điều không làm

- Không bấm **New → Blueprint** nếu nó đòi Cron/Starter/thẻ.
- Không chọn instance **Starter**, không bật Cron Render.
- Không tắt `DRY_RUN` trên Render, không đặt `ENABLE_SCHEDULED_*` thành `true`.
- Không dùng `setInterval` trình duyệt hoặc ping liên tục để giả Cron.

Chi tiết vận hành, backup và khôi phục: [operations.md](operations.md).

## 12. Khi gặp lỗi

Bảng đầy đủ theo triệu chứng: [operations.md](operations.md#7-sự-cố-thường-gặp).

| Thông báo | Việc cần làm |
| --- | --- |
| API key không hợp lệ (HTTP 401) | Tạo lại key trong bot Telegram. Key mới thu hồi key cũ. |
| Không đủ quyền (HTTP 403) | Kiểm tra key và quyền của tài khoản sở hữu ví. |
| HTTP 429 | Đợi. Lệnh đọc tôn trọng `Retry-After`, tối đa 60 giây mỗi lần, rồi thử lại có giới hạn. |
| Không tìm thấy file Google | Kiểm tra ID và quyền chia sẻ cho đúng email service account. |
| Tệp service account không hợp lệ | JSON phải có `type` bằng `service_account`. |
