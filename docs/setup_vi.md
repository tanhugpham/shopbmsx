# Hướng dẫn cài đặt

Tài liệu này dành cho người vận hành trên Windows PowerShell.

## 1. Môi trường Python

Cần Python 3.11 trở lên. Thư mục dự án là `shopxoainon-integration`.

```powershell
cd shopxoainon-integration
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
| `GOOGLE_SPREADSHEET_ID` | ID của spreadsheet có sẵn, lấy trên URL Google Sheets. |
| `GOOGLE_DOCUMENT_ID` | ID của document có sẵn. |
| `GOOGLE_CREDENTIALS_FILE` | Mặc định `./GOOGLE_CREDENTIALS_FILE.json`. Đường dẫn tương đối tính từ thư mục gốc dự án. Đường dẫn tuyệt đối vẫn dùng được. |
| `APP_ENV` | `development` hoặc tên môi trường khác. |
| `DRY_RUN` | `true` để không ghi Google. `false` để ghi. |
| `ENABLE_PURCHASE` | Giữ `false`. Giá trị `true` vẫn không mở được đường tạo đơn. |
| `ENABLE_TELEGRAM` | Giữ `false`. Chưa có bot vận hành. |
| `REQUEST_TIMEOUT_SECONDS` | Thời gian chờ mỗi request. Mặc định 30 giây. |
| `NASTELE_BASE_URL` | Mặc định `https://nastele.online`. Phải dùng HTTPS. |
| `NASTELE_API_KEY` | Key Nastele, gửi trong header `X-API-Key`. Để trống nếu chưa có. Không ghi vào Git. |
| `PREFERRED_SUPPLIER_ID` | `shop_xoai_non` hoặc `nastele`. Dùng khi hai nguồn cùng giá. Mặc định `shop_xoai_non`. |
| `COMPARISON_QUANTITY` | Số lượng dùng để so giá. Mặc định `1`, nên không lấy giá sỉ của bậc 10 hay 25. |
| `NASTELE_RATE_LIMIT_PER_MINUTE` | Từ 1 đến 60. Trần tài liệu là 60 request mỗi phút cho mỗi key. |

Không dán API key hay JSON service account vào mã nguồn, test, Google Sheets, Google Docs, ảnh chụp hoặc Git.

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

Spreadsheet riêng của Nastele dùng `NASTELE_SPREADSHEET_ID`. Để trống thì trạng thái là BLOCKED. Không tự tạo file Google. Khi đã có ID và quyền Sửa, dữ liệu Nastele vào `NASTELE_SAN_PHAM`, `NASTELE_GIA_SI`, `NASTELE_LICH_SU_GIA`, `NASTELE_NHAT_KY` và `NASTELE_CAU_HINH`.

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

## 7. File báo giá tổng hợp

Hai file Google tách nhau. Không copy dòng từ file catalog sang file tổng hợp.

| File | Biến | Tab bạn xem / sửa |
| --- | --- | --- |
| Catalog Shop Xoài Non | `GOOGLE_SPREADSHEET_ID` | `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`. Nội bộ, có giá gốc. |
| Báo giá tổng hợp | `LAST_SPREADSHEET_ID` | `NASTELEID` (sửa tay), `BAO_GIA` (tool), `BAO_GIA_TONG_HOP` (tool). |

Ba tab file tổng hợp dùng **cùng một dòng tiêu đề**:

| Nguồn | Mã | Tên sản phẩm | Giá bán | Tình trạng | Cấu hình |
| --- | --- | --- | --- | --- | --- |
| `NASTELEID` hoặc trống | mã Nastele, ví dụ `43` | tên khách thấy | giá đã bán, số nguyên VND | `Còn hàng` / `Hết hàng` | gói, thời hạn |
| `Shop Xoài Non` | `product_id\|variant_id` | tên + biến thể | giá đã làm tròn đẹp | tool ghi | mô tả biến thể |

Cột cũ `Nastele ID` trên tab `NASTELEID` vẫn đọc được. Không đặt `Giá gốc`, `Product ID` hay `Giá thấp nhất (VND)` vào file này. Bảng đầy đủ: [bao_gia_tong_hop.md](bao_gia_tong_hop.md).

```powershell
python -m shop_integration quote-layout
```

## 8. Báo giá API

Báo giá không nằm trong đồng bộ định kỳ.

```powershell
python -m shop_integration quote --product-id local_81 --variant-id local_81 --quantity 1
```

Lệnh này gọi `POST /products/{id}/quote`. Nó không tạo đơn và không trừ ví. Request báo giá không được thử lại tự động.

## 9. Kiểm thử tự động

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

Test dùng HTTP giả lập và gateway Google trong bộ nhớ. Chúng không cần API key thật và không gọi mạng Shop Xoài Non hay Google.

## 10. Render miễn phí

Cron Render cần thẻ. Dùng **New → Web Service**, instance **Free**. Start command: `python -m shop_integration.web`. Lịch chạy dùng GitHub Actions. Bảng bấm từng bước: [render_free.md](render_free.md).

## 11. Khi gặp lỗi

| Thông báo | Việc cần làm |
| --- | --- |
| API key không hợp lệ (HTTP 401) | Tạo lại key trong bot Telegram. Key mới thu hồi key cũ. |
| Không đủ quyền (HTTP 403) | Kiểm tra key và quyền của tài khoản sở hữu ví. |
| HTTP 429 | Đợi. Lệnh đọc tôn trọng `Retry-After`, tối đa 60 giây mỗi lần, rồi thử lại có giới hạn. |
| Không tìm thấy file Google | Kiểm tra ID và quyền chia sẻ cho đúng email service account. |
| Tệp service account không hợp lệ | JSON phải có `type` bằng `service_account`. |
