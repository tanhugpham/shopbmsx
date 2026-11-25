# Báo cáo kiểm thử

Ngày chạy: 2026-11-25.

Môi trường: Windows, Python 3.13.15, thư viện trong `requirements.txt` (httpx 0.28.1, pydantic 2.14.4, pytest 9.2.0, tenacity 9.2.1, google-api-python-client 2.202.0).

## Lệnh

```powershell
cd shopxoainon-integration
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
python -m pytest --tb=short
```

Kết quả lần chạy sau cùng: **309 passed, 0 failed** (Phase 4.3F). Suite gồm test Phase 4.1, nhiều nhà cung cấp, Phase 4.2.1 cho catalog rỗng, 16 test Phase 4.3B cho một spreadsheet duy nhất, 14 test Phase 4.3D cho vùng MENU A31:H35, 17 test Phase 4.3E cho migration nguồn/MENU, 23 test Phase 4.3F cho catalog hai tầng và MENU khách, cùng test scheduler chỉ ghi khi opt-in.

Không có test nào bị bỏ qua. Không có test nào ghi đè kết quả mạng bằng dữ liệu giả và gọi đó là thành công ngoài đời.

## Phạm vi đã kiểm tra bằng mock

`tests/test_api_client.py`

- `GET /me` gửi đúng header `x-api-key` và URL `https://example.test/api/v1/me`. Phần tóm tắt chỉ giữ tên khóa.
- HTTP 401 không thử lại. Thông điệp tiếng Việt không chứa API key dù thân phản hồi lặp lại key.
- Thiếu API key thì không mở HTTP.
- Timeout và lỗi kết nối được thử lại đúng số lần. Thông điệp timeout không chứa key.
- HTTP 429 đọc `Retry-After: 12` rồi thử lại. Trần 60 giây và backoff được kiểm tra riêng.
- HTTP 503 được thử lại. HTTP 403 và 404 không thử lại.
- HTTP 302 không bị đi theo.
- Danh sách rỗng, hai trang `limit=2`, và trang bị lặp lại thì dừng.
- Tham số `search` được chuyển tiếp.
- JSON hỏng và phản hồi thiếu mảng `data` báo lỗi, không thử lại.
- Báo giá đúng body đã xác nhận. HTTP 500 của báo giá chỉ gọi một lần.
- Số lượng báo giá bằng 0 không mở HTTP.
- `POST /orders`, path traversal về `/orders`, và `/orders/{id}/delivery` không mở HTTP.
- Đơn hàng có `password` chỉ còn `order_id` và `status`.
- Ví có một số nguyên được đánh dấu `confirmed=False`. Hai số nguyên thì không chọn số dư.
- Giá `9500`, `"9500"`, `9500.0` thành int. `9500.5`, `"9.500"`, số âm và boolean bị từ chối.

`tests/test_product_service.py`

- Giữ `local_81`, `local_82_a`, `local_82_b`, `local_083`.
- Giá thấp nhất/cao nhất, số lượng `0`, và trạng thái hết hàng.
- Sản phẩm thiếu tên, thiếu giá, thiếu biến thể, thiếu id, hoặc có giá lẻ không làm vỡ cả danh sách.
- Mô tả cấp sản phẩm trong fixture không được ghi sang bảng. Mô tả biến thể chỉ xuất hiện khi khóa đó có trong JSON.
- Mã trùng giữ bản cuối.

`tests/test_sheets_service.py`

- Ghép dòng giữ cột `Ghi chú nội bộ`, đánh dấu dòng vắng mặt là `Không có trong API`, và xóa dòng thừa bằng ô trống.
- Lịch sử giá chỉ ghi biến thể đã có giá cũ khác giá mới.
- Lần ghi thật tạo tab còn thiếu, redact API key trong tên sản phẩm, giữ số nguyên, và không ghi key vào `CAU_HINH`.
- `DRY_RUN` không gọi `update`, `append` hay `addSheet`.
- Gateway Google Sheets thật được mock: `valueInputOption=RAW`.

`tests/test_docs_service.py`

- Báo cáo tiếng Việt được chèn tại index 1 khi `endIndex` là 2.
- API key trong cảnh báo bị thay bằng `***`.
- Dry-run không đọc và không ghi document.
- Văn bản chứa `BEGIN PRIVATE KEY` bị từ chối.

`tests/test_sync_service.py`

- Đồng bộ hai trang, 3 sản phẩm và 4 biến thể, với `ENABLE_PURCHASE=true` và `ENABLE_TELEGRAM=true`.
- Mọi request của lần đồng bộ là `GET` tới `/products`. Không có báo giá, không có tạo đơn.
- Một thay đổi giá 9000 -> 9500 được ghi vào lịch sử và vào báo cáo.
- Tên sản phẩm trùng API key thành `***` trên Sheets và không xuất hiện trong Google Doc.
- Mô tả sản phẩm không được ghi.
- Dry-run vẫn tính được thay đổi giá 100 -> 500 và không ghi file.

`tests/test_security.py`

- `repr`, `str` và `model_dump` của cấu hình không chứa API key.
- `purchase_allowed` và `telegram_active` luôn là false.
- Formatter nhật ký xóa key và cụm private key. Logger `httpx`/`httpcore` ở mức WARNING.
- CLI không có lệnh mua hàng hoặc lấy tài khoản đã giao.
- Telegram luôn từ chối gửi, kể cả khi cờ bật.
- Scope chỉ có Spreadsheets và Documents.
- JSON không phải service account, và JSON service account hỏng, trả lỗi tiếng Việt không kèm nội dung khóa.
- HTTP 404/403 của Google được dịch sang tiếng Việt.
- Client đặt `follow_redirects=False`.
- Base URL `http://` bị từ chối.

## Kiểm tra lệnh

`python -m shop_integration --help` in đủ `ping`, `products`, `product`, `wallet`, `orders`, `order`, `quote`, `sync`, `report`.

`python -m shop_integration ping` khi không có `SHOP_XOAI_NON_API_KEY` thoát mã 2 và in `Lỗi cấu hình: Thiếu SHOP_XOAI_NON_API_KEY.`

## Đọc API thật

Đã kiểm tra biến môi trường Process, User và Machine. `SHOP_XOAI_NON_API_KEY`, `GOOGLE_SPREADSHEET_ID`, `GOOGLE_DOCUMENT_ID` và `GOOGLE_CREDENTIALS_FILE` đều trống. Trong thư mục dự án chỉ có `.env.example`.

Vì vậy **chưa đếm được số sản phẩm thật**. Chưa gọi Google Sheets hay Google Docs thật.

Thăm dò không kèm key, cùng ngày:

`GET https://shopxoainon.online/api/v1/products?limit=1` trả **HTTP 401**, thân rỗng.

Kết quả này chỉ cho thấy máy chủ từ chối request không xác thực. Đây không phải lần đọc danh mục thành công.

## Cách xác minh tiếp

Điền `.env` theo `docs/setup_vi.md`, giữ `DRY_RUN=true`, rồi chạy:

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration ping
python -m shop_integration products
```

Dòng `Số sản phẩm:` của lệnh thứ hai là số liệu đọc thật. Chỉ đặt `DRY_RUN=false` sau khi số liệu đó đúng.

## Phase 4.2 — test tự động

`tests/test_nastele_client.py`, `tests/test_matching.py`, `tests/test_source_selection.py` và `tests/test_multi_supplier.py` kiểm tra bằng mock:

- Header `X-API-Key`, không in key khi HTTP 401, 403, timeout hoặc thân lỗi.
- HTTP 429 được thử lại. Đơn hàng và `POST` không mở HTTP. Trường `accounts` bị bỏ.
- Trần tần suất theo cửa sổ 60 giây.
- Số lượng so sánh 1 không dùng bậc sỉ 10 hoặc 25. Số lượng 25 dùng bậc thấp hơn khi bậc đó hợp lệ.
- Nguồn rẻ hơn nhưng hết hàng hoặc chưa xác nhận không được chọn. Hòa giá dùng nhà cung cấp ưu tiên.
- `EXACT_MATCH` mới được so giá. Tên gần giống chỉ vào `REVIEW_REQUIRED`. Mô tả không bị thực thi.
- Cùng một mã số ở hai nhà cung cấp không thành một khóa.
- Một nhà cung cấp lỗi thì dòng của nhà cung cấp kia vẫn còn. Dòng cũ của nhà cung cấp lỗi không bị đánh dấu mất.
- `CAN_GHEP_SAN_PHAM` giữ `Review Status` và mã chuẩn đã duyệt. Ô công thức được chặn. `DRY_RUN` không ghi. Ghi thật đòi backup trước.
- Tab `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM` và các tab `NASTELE_*` không nằm trong `ALL_TABS`.
- Bảng giá khách không có giá vốn hay lợi nhuận. Vùng Docs lệch một dấu thì không ghi.
- Workflow GitHub để `DRY_RUN=true` và `ENABLE_PURCHASE=false`.

Các test mock không thay cho lần đọc ngoài đời.

## Phase 4.2.1 — danh mục Nastele rỗng

Đọc lại https://nastele.online/api/partner/docs ngày 2026-11-23. Danh sách sản phẩm là `GET /api/partner/products`, không có tham số bắt buộc, không có phân trang và không có endpoint danh mục. `POST /api/partner/orders` không được gọi.

Đọc thật, không ghi Google:

| Endpoint | HTTP | Cấu trúc đã thấy |
| --- | --- | --- |
| `GET /api/partner/products` | 200 | `success=true`, `data` là mảng dài 0 |
| `GET /api/partner/products?page=1&perPage=100` | 200 | cùng mảng rỗng. Tham số không có trong tài liệu |
| `GET /api/partner/products?status=active` | 200 | cùng mảng rỗng. Tham số không có trong tài liệu |
| `GET /api/partner/balance` | 200 | `success=true`, `data` có `balance` kiểu số và `currency` kiểu chuỗi. Không ghi số dư |
| `GET /api/partner/products/43` | 404 | không phải JSON. Mã 43 là ví dụ tài liệu, không có trong tài khoản này |

Xác thực: PASS. Catalog: EMPTY, trạng thái `NASTELE_CATALOG_EMPTY`. Không phải lỗi parse. Shop Xoài Non cùng lúc: HTTP 200, 24 nguồn. Còn hàng và có giá hợp lệ: 11, cả 11 được chọn. Hết hàng theo cờ `in_stock`: 13, có giá nhưng không được chọn. Còn hàng thiếu giá: 0. Dữ liệu không rõ: 0. Báo cáo Google đã lưu nói 15 còn hàng. Năm mã chuyển sang hết hàng là `local_24`, `local_35`, `local_55`, `local_56`, `local_85`. Một mã chuyển sang còn hàng là `local_67`. Không thấy mốc 16 còn hàng hay 14 nguồn được chọn trong sheet, Doc hoặc lần đọc này.

Backup local: FAIL cho scheduled live sync. Workflow giữ `DRY_RUN=true`. Khôi phục không ghi `Trang tính1` hay tab ngoài vùng quản lý.

Kiểm tra đọc thật ngày 2026-11-23, không ghi Google: Shop Xoài Non `GET /products` HTTP 200, 24 nguồn. Nastele `GET /api/partner/products` HTTP 200, `success=true`, mảng `data` dài 0. Spreadsheet chính đọc được 5 tab, chưa có `SO_SANH_NGUON`. Spreadsheet Nastele đọc được 1 tab, không tạo tab mới. Google Doc đọc được, một vùng báo cáo Shop, chưa có vùng bảng giá khách. `DRY_RUN` vẫn `true`. Không gọi đơn hàng.

## Phase 4.3B — một Google Spreadsheet

Spreadsheet chính thức: `196goR1lDZN6zJfoC3a7vOVyo5u9o-pvxXpc3mnGrGSY`. Đọc thật ngày 2026-11-25, không ghi ô:

| Tab | Dòng dữ liệu | Ghi chú |
| --- | --- | --- |
| `MENU` | 39 | Header ở dòng 6, 62 vùng merge, 29 ô cột J–M có nội dung |
| `NASTELEID` | 1 | Dòng tiêu đề |
| `BAO_GIA` | 23 | Tool sẽ cập nhật |
| `BAO_GIA_TONG_HOP` | 24 | Tool sẽ cập nhật |

Chưa có `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM` và các tab `NASTELE_*`. Lần ghi thật đầu tiên sẽ tạo tab; hiện **BLOCKED** vì backup chưa đạt. `restore-plan` chặn bởi xung đột Docs; không chạy `restore`.

16 test Phase 4.3B nằm trong `tests/test_phase43_single_spreadsheet.py`: một ID duy nhất, biến cũ bị bỏ qua, `config-check`, dùng chung credentials/gateway, hai writer cùng một spreadsheet, catalog Nastele rỗng không tạo dòng, giá và ghép nguồn, chống nhân đôi, MENU giữ cột J–M và merge, dry-run không ghi, tab LEGACY, không mua hàng/Telegram.
