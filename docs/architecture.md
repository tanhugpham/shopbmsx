# Kiến trúc

## Phạm vi đã làm

Yêu cầu ban đầu mô tả đầy đủ Phase 1 và bắt đầu Phase 2 đến câu “Do not invent descriptions”. Phần còn lại của Phase 2 và toàn bộ Phase 3 không có trong prompt. Phần triển khai bên dưới bám các file đã được chỉ định, các tab đã được nêu tên, và các ràng buộc an toàn đã được nêu.

Phase 3 trong mã này gồm:

- Điều phối đồng bộ trong `sync_service.py`
- Báo cáo tiếng Việt trong `report_service.py` và `docs_service.py`
- CLI trong `cli.py`
- Giao diện Telegram trong `telegram_adapter/`, không có client mạng

## Luồng dữ liệu

```text
Shop Xoài Non API
    GET /products?limit&offset
        -> ProductService (chỉ trường đã xác nhận)
        -> so sánh với BIEN_THE
        -> ghi SAN_PHAM, BIEN_THE
        -> nối thêm LICH_SU_GIA và NHAT_KY_DONG_BO
        -> cập nhật CAU_HINH
        -> thêm báo cáo vào cuối Google Doc
```

`POST /products/{id}/quote` chỉ chạy từ lệnh `quote`. `SyncService` không gọi nó.

## Thành phần

| Module | Vai trò |
| --- | --- |
| `config.py` | Đọc môi trường bằng pydantic-settings. API key là `SecretStr`. |
| `api_client.py` | HTTP, timeout, thử lại, phân trang, chặn thao tác nguy hiểm. |
| `product_service.py` | Chuẩn hóa sản phẩm và biến thể. |
| `sheet_layout.py` | Ghép dòng, lịch sử giá, giữ cột phụ. |
| `sheets_service.py` | Đọc và ghi spreadsheet có sẵn. |
| `docs_service.py` | Chèn báo cáo vào document có sẵn. |
| `google_auth.py` | Service account và hai scope Sheets/Docs. |
| `sync_service.py` | Điều phối một lần đồng bộ. |
| `report_service.py` | Dựng văn bản báo cáo. |
| `cli.py` | Lệnh vận hành. |
| `telegram_adapter/` | Protocol cho bot sau này. `build_notifier` luôn trả bộ điều hợp tắt. |

## Hợp đồng API đã đối chiếu ngày 2026-11-23

Nguồn:

- https://shopxoainon.online/api/docs
- https://shopxoainon.online/api/openapi.json

OpenAPI 3.0.3 khai báo đường dẫn và tham số, nhưng không khai báo schema phản hồi. Các trường dưới đây được xác nhận bởi ví dụ HTML hoặc bởi OpenAPI.

### Đã xác nhận

Xác thực: header `x-api-key`.

`GET /products`

- Query `limit`: mặc định 200, tối đa 200
- Query `offset`: số sản phẩm bỏ qua, bắt đầu từ 0
- Query `search`: tìm theo tiêu đề, không phân biệt hoa thường
- Thân phản hồi có mảng `data`
- Sản phẩm: `id`, `title`, `in_stock`, `variants`
- Biến thể: `id`, `prices.vnd`, `in_stock`, `available_quantity`
- Mã sản phẩm trong ví dụ có dạng `local_81`
- Giá ví dụ là số nguyên VND `9500`

`GET /products/{id}`

- OpenAPI xác nhận đường dẫn và HTTP 404
- Mã giả định cùng các trường sản phẩm với danh sách. Nếu thân không có `data` là object nhưng có `id`, client vẫn nhận object đó và ghi chú là cấu trúc chưa được mô tả đầy đủ

`POST /products/{id}/quote`

- Body: `variant_id`, `quantity`, `currency_code`
- `data`: `product_id`, `quantity`, `unit_price`, `total`, `available_quantity`, `can_purchase`
- Không thử lại, vì đây là POST

`GET /orders/{id}`

- Văn bản tài liệu nói phản hồi có `status`, ví dụ `completed`

`POST /orders` và `GET /orders/{id}/delivery`

- Tài liệu có mô tả body, `Idempotency-Key`, `checkout_id`, `order_id`, `status`, `payment`
- Mã nguồn không gọi hai đường này

Mã lỗi được OpenAPI mô tả bằng lời:

- 401: key thiếu hoặc không hợp lệ
- 403: thiếu scope
- 400: có thể là thiếu số dư hoặc thiếu hàng, trên đường tạo đơn
- 409: giao hàng đang chờ, trên đường delivery

### Quan sát thực tế, không phải schema

Ngày 2026-11-23, `GET https://shopxoainon.online/api/v1/products?limit=1` không kèm API key trả HTTP 401 với thân rỗng. Đây chỉ chứng minh máy chủ từ chối request không xác thực. Đây không phải một lần đọc danh mục thành công.

### Chưa được tài liệu xác nhận

Những phần này được đọc ở dạng thận trọng. Đồng bộ danh mục không phụ thuộc vào chúng.

- Thân `GET /me`. CLI chỉ in tên khóa.
- Thân `GET /wallet/balance`. Nếu `data` có đúng một số nguyên, CLI in số đó và ghi rõ là chưa xác nhận. Nếu có nhiều số, CLI không chọn.
- Thân `GET /orders`. Client yêu cầu `data` là mảng. Mỗi phần tử chỉ giữ `order_id` hoặc `id` hoặc `checkout_id`, cùng `status` nếu là chuỗi. Mọi trường giao tài khoản bị bỏ.
- Tổng số trang. Không có trường total. Client dừng khi trang ngắn hơn `limit`, khi trang rỗng, khi trang lặp lại, hoặc khi đạt 100 trang.

Mô tả ở cấp sản phẩm không có trong schema đã xác nhận và không được ghi sang bảng. Tên và mô tả của biến thể được chuẩn hóa khi khóa đó có trong JSON.

## Cột Google Sheets

`SAN_PHAM`:

- STT
- Product ID
- Tên sản phẩm đã decode
- Mô tả ngắn
- Số biến thể
- Giá gốc thấp nhất
- Giá gốc cao nhất
- Giá bán thấp nhất
- Giá bán cao nhất
- Trạng thái hàng
- Ngày cập nhật
- Trạng thái đồng bộ
- Tên gốc

Giá gốc là min/max của `prices.vnd`. Giá bán được tính từ giá gốc đó, theo từng biến thể, rồi lấy min/max. Ngày cập nhật là thời điểm đồng bộ theo UTC+7. Trạng thái hàng ưu tiên boolean `in_stock` của sản phẩm. Nếu trường đó vắng, hệ thống suy ra từ biến thể. Không có cờ nào thì “Không được cung cấp”. JSON null giữ chữ `null`. Không suy ra hết hàng từ số lượng bằng 0. Mô tả ngắn chỉ lấy khi mọi mô tả biến thể không rỗng là cùng một chuỗi, rồi cắt ở khoảng 180 ký tự. Nhiều mô tả khác nhau thì ghi “Chưa có dữ liệu”.

`BIEN_THE`:

- STT
- Product ID
- Variant ID
- Tên sản phẩm
- Tên biến thể
- Mô tả
- Giá gốc
- Tỷ lệ lợi nhuận (%)
- Lợi nhuận (VND)
- Giá bán đề xuất (VND)
- Trạng thái còn hàng
- Số lượng tồn kho
- Manual upgrade
- Ngày cập nhật
- Trạng thái đồng bộ
- Tên gốc
- Mô tả gốc

Ô số lượng dùng `Không được cung cấp` khi khóa vắng, `null` khi JSON null, và số nguyên kể cả 0. Tên, mô tả hoặc nâng cấp thủ công không có trong API thì ghi “Chưa có dữ liệu”. Cột gốc giữ chuỗi API trước khi chuẩn hóa để đối chiếu. Giá gốc không hợp lệ để trống và trạng thái là “Thiếu giá”, không ghi 0.

`BANG_GIA_BAN` là bảng giá cho khách: STT, tên sản phẩm, tên biến thể, giá bán, tình trạng hàng, ngày cập nhật. Tab này không có giá gốc, lợi nhuận hay ID. Biến thể thiếu giá gốc không được đưa vào. Hết hàng vẫn được liệt kê. Tab được ghi lại từ danh mục hiện tại. Cột lạ giữ tên nhưng không ghép được ghi chú theo dòng vì không có khóa.

`LICH_SU_GIA` chỉ thêm dòng khi giá gốc của một biến thể đã có trên bảng thay đổi. Đổi tên cột từ `Giá (VND)` sang `Giá gốc` không tạo lịch sử nếu số tiền không đổi. Giá bán thay đổi theo giá gốc mới, không cộng thêm 30% lên giá bán cũ.

`NHAT_KY_DONG_BO` nối thêm một dòng sau mỗi lần ghi thật, gồm số thêm mới, số cập nhật, số không đổi và số lỗi.

`CAU_HINH` là cặp khóa/giá trị vận hành. Ô này ghi `api_key_configured` là `có` hoặc `không`, không ghi key. `enable_purchase` luôn được ghi `false`. Năm tham số tính giá được ghi dạng chữ, không phải bí mật.

Lần ghi đầu sau khi đổi schema map cột cũ sang tên mới, giữ cột người dùng ở cuối và không xóa dòng. Sản phẩm không còn trong API đầy đủ vẫn được đánh dấu `Không còn trong API`.

## Tiền

VND được giữ bằng `int`. Parser nhận số nguyên, chuỗi chữ số, và float chỉ khi phần thập phân bằng 0. Số lẻ, số âm và chuỗi có dấu phân tách bị từ chối.

Lợi nhuận tính trên giá gốc bằng `Decimal`, không phải margin trên doanh thu. Dưới 100000 và trên 200000, lợi nhuận là giá gốc nhân tỷ lệ rồi làm tròn nửa lên về VND nguyên (`ROUND_HALF_UP`, 0,5 thành 1). Từ 100000 đến 200000 kể cả hai đầu, lợi nhuận là mức cố định cấu hình, mặc định 35000, cho phép 30000 đến 40000. Giá bán bằng giá gốc cộng lợi nhuận. Mọi lần tính bắt đầu từ `prices.vnd` mới nhất. Ô Sheets ghi bằng `RAW`. Chuỗi bắt đầu bằng `=`, `+`, `-`, `@`, tab hoặc xuống dòng được thêm một ký tự zero-width space để lần khôi phục `USER_ENTERED` không chạy công thức. Product ID và Variant ID không bị thêm ký tự đó.

## Thử lại

Chỉ `GET` được thử lại, tối đa 4 lần tính cả lần đầu. Điều kiện: timeout, lỗi kết nối, HTTP 408, 429 và 5xx.

HTTP 429 dùng `Retry-After` nếu parse được, trần 60 giây. Không có header thì chờ 0,5 giây, 1 giây, 2 giây, 4 giây rồi trần 8 giây.

HTTP 401, 403, 404 và lỗi JSON không được thử lại. Mọi POST, kể cả báo giá, không được thử lại. Chuyển hướng 3xx bị từ chối và không được đi theo.

## Google

`GoogleSheetsGateway` và `GoogleDocsGateway` là lớp mỏng quanh `google-api-python-client`. Test nghiệp vụ dùng gateway trong bộ nhớ.

`DRY_RUN=true` vẫn được phép đọc Sheets để tính chênh giá, nhưng không gọi `addSheet`, `update` hay `append`.

Sheets không có giao dịch nhiều tab. Nếu một lệnh ghi giữa chừng thất bại, các tab trước đó có thể đã thay đổi. Nhật ký chỉ được thêm sau khi các bảng chính đã ghi.

Document nhận `insertText` tại `endIndex - 1`, tức cuối nội dung hiện có.

## Telegram

`CatalogQuery`, `SyncCommand`, `ReportQuery` và `TelegramNotifier` mô tả các thao tác đọc và đồng bộ sau này. Không có protocol mua hàng hay lấy tài khoản. `TelegramDisabledNotifier.send` luôn ném lỗi. `ENABLE_TELEGRAM` không đổi hành vi này.

## Nhật ký

Logger `httpx`, `httpcore` và `googleapiclient` bị đặt mức WARNING để thư viện không in header. Formatter của ứng dụng thay API key dài từ 8 ký tự và cụm `BEGIN PRIVATE KEY` bằng `***`. Thông điệp lỗi API không chép thân phản hồi, vì thân có thể lặp lại key hoặc dữ liệu giao hàng.

## Báo giá tổng hợp — UI thống nhất

File `LAST_SPREADSHEET_ID` tách khỏi catalog `GOOGLE_SPREADSHEET_ID`. `NASTELEID` và `BAO_GIA` khóa cùng cột với phần sản phẩm của `MENU`: `ID`, `PRODUCT`, `DESCRIPTION`, `DURATION`, `WARRANTY`, `PRICE`, `STOCK`. `ID` là số thứ tự trên từng sheet, không lấy local id API. `MENU` thêm cột `STATUS`.

`NASTELEID` do người dùng nhập. Tool chỉ tạo/khóa dòng tiêu đề và định dạng, không ghi dữ liệu sản phẩm và không đổ catalog API vào tab này. Cột giá vốn không map sang `PRICE`.

`BAO_GIA` nhận giá shop đã cộng lợi nhuận rồi làm tròn đẹp, cùng số lượng ở cột `STOCK`. Hiện tại nguồn API của tab này là Shop Xoài Non. `MENU` chép sáu cột nội dung từ `BAO_GIA` và `NASTELEID`, giữ mã vùng `SXN-` / `NST-`, rồi điền `STATUS` từ `STOCK`. `BAO_GIA_TONG_HOP` xếp chồng hai nguồn đó, không chọn nguồn rẻ hơn. `sync`, `report` và `sync-menu` dùng cùng writer này. Mỗi lần chạy khóa lại header, đóng băng dòng 1 và khổ cột.

Bảng cột đầy đủ nằm ở `docs/bao_gia_tong_hop.md`. Lệnh `quote-layout` in cùng các bảng đó, không gọi mạng.

## Phase 4.2 — hai nhà cung cấp

Shop Xoài Non vẫn đi qua `api_client.py` và `sync`. Nastele có client riêng trong `suppliers/nastele_client.py`.

Đã đọc https://nastele.online/api/partner/docs ngày 2026-11-23. Phạm vi dùng:

- `GET /api/partner/products`
- `GET /api/partner/products/{id}`

Xác thực là header `X-API-Key`. Trần tài liệu là 60 request mỗi phút. Danh sách sản phẩm không có phân trang trong tài liệu, nên một GET là cả danh mục. Trường đã đọc: `id`, `name`, `price`, `description`, `category_id`, `stock`, `wholesale_tiers.minQuantity`, `wholesale_tiers.unitPrice`. Không có trường phí bắt buộc trong schema sản phẩm, nên giá không bị cộng thêm khoản phí tự đặt. `category_id` chỉ để hiển thị, không dùng để ghép sản phẩm.

`POST /api/partner/orders` và mọi đường có `orders` bị chặn trước HTTP. Phản hồi đơn có thể chứa tài khoản, mật khẩu và 2fa. Những khóa đó bị bỏ ngay cả khi lọt vào JSON sản phẩm. Không có cờ môi trường nào mở mua hàng.

Mỗi offer có khóa `supplier_id|product_id|variant_id`. Nastele không có biến thể trong tài liệu, nên phần biến thể để trống và được ghi nhãn `API không có biến thể`. Mã `43` của Nastele không trùng khóa với mã `43` của Shop Xoài Non.

`stock` của Nastele là số lượng. Lớn hơn 0 thì còn hàng, bằng 0 thì hết hàng, null hoặc vắng thì chưa xác nhận. Số lượng 0 của Shop Xoài Non không tự thành hết hàng, vì shop có cờ `in_stock` riêng.

Ghép sản phẩm nằm trong `suppliers/matching.py`:

- Trùng multiset tên đã chuẩn hóa: `EXACT_MATCH`.
- Cùng thương hiệu, cùng nhóm tên, và mọi thuộc tính giao hàng đã biết đều bằng nhau: `EXACT_MATCH`. `1 year` có thể khớp `1 năm`. `12 tháng` không bị đổi thành `1 năm`.
- Cùng thương hiệu nhưng thiếu thuộc tính một bên, hoặc tên gần theo Jaccard từ 0,6: `REVIEW_REQUIRED`.
- Phần còn lại: `DIFFERENT_PRODUCT`.

Mô tả không tham gia khóa và không được thực thi. Chỉ `EXACT_MATCH` đi vào so giá. Cặp `REVIEW_REQUIRED` nằm ở tab `CAN_GHEP_SAN_PHAM`. Ô `Review Status` và `Approved Canonical Product ID` do người dùng nhập và không bị ghi đè. Tab này không thuộc `ALL_TABS`, nên lần sync Shop Xoài Non và lần khôi phục không ghi đè nó.

So giá nằm trong `suppliers/selection.py`. Giá lẻ luôn được xét. Bậc sỉ chỉ được xét khi `minQuantity` không lớn hơn số lượng dự kiến. Mặc định số lượng là 1, nên bậc 10 và 25 của ví dụ Canva không được đem so với giá lẻ một đơn vị. Nguồn được chọn là nguồn còn hàng có giá đơn vị thấp nhất. Hòa giá thì dùng `PREFERRED_SUPPLIER_ID`, rồi tới khóa tổ hợp. Nguồn rẻ hơn nhưng hết hàng hoặc chưa xác nhận không được chọn. Giá bán đề xuất vẫn là `quote_cost` trên giá đã chọn. Mỗi sản phẩm chuẩn có một dòng ở `SO_SANH_NGUON`. Khóa chuẩn là `cxn_` cộng 12 ký tự hex, hoặc mã người dùng đã duyệt. Không dùng product id của một nhà cung cấp làm khóa toàn cục.

Nếu dữ liệu một bên đang cũ, thiếu giá hoặc chưa xác nhận tồn kho, trạng thái là `CẦN XÁC MINH`. Lệnh không chọn lại nhà cung cấp từ phía còn lại và không ghi tồn kho 0 đè lên số cũ.

`GET /api/partner/products` trả HTTP 200, `success=true` và `data=[]` thì trạng thái là `NASTELE_CATALOG_EMPTY`. Đây là danh mục rỗng đã đọc được, không phải lỗi xác thực và không phải bằng chứng mọi sản phẩm Nastele đã bị xóa. Lệnh không tạo offer giả, không ghi Nastele là nguồn rẻ hơn, và không đổi `Selected Supplier` đã lưu chỉ vì mảng này rỗng. Phong bì `data.items=[]` kèm `pagination` cũng đi vào trạng thái đó. `data` không còn là mảng và cũng không phải phong bì phân trang rỗng thì bị từ chối như schema đổi, để dữ liệu cũ được giữ. Tài liệu đối tác không có endpoint danh mục và không có tham số bắt buộc trên danh sách sản phẩm. `page`, `perPage`, `status` không nằm trong hợp đồng của endpoint này.

Backup trong `backups/` là snapshot local, không đủ cho scheduled live sync trên GitHub Actions. Workflow giữ `DRY_RUN=true`. Job Actions không được ghi khi chỉ có snapshot local. Khôi phục không ghi `Trang tính1` và không ghi tab ngoài vùng quản lý. Mã không tạo file Google Drive và không đổi quyền chia sẻ.

`BANG_GIA_TOI_UU` và vùng Docs `BANG_GIA_KHACH_BEGIN` / `BANG_GIA_KHACH_END` chỉ hiện số thứ tự, tên, mô tả, giá bán, tình trạng và thời điểm. Không hiện giá vốn, lợi nhuận, mã sản phẩm hay API key. Vùng `SHOP_XOAI_NON_BAO_CAO` là báo giá cho khách: STT, tên, giá bán, tình trạng. Không có cột Product ID và không có giá gốc. Mã sản phẩm vẫn nằm trong Sheets để đồng bộ.

Spreadsheet Nastele dùng `NASTELE_SPREADSHEET_ID`. Khi biến này trống, trạng thái là BLOCKED và không tạo file mới. Tab `NASTELE_SAN_PHAM`, `NASTELE_GIA_SI`, `NASTELE_LICH_SU_GIA`, `NASTELE_NHAT_KY`, `NASTELE_CAU_HINH` chỉ được ghi sau khi đã đọc snapshot cục bộ. API key không được ghi vào ô. Lần đọc Nastele lỗi thì giữ dòng cũ và chỉ thêm cảnh báo vào nhật ký.

Tab `SO_SANH_NGUON`, `BANG_GIA_TOI_UU` và `CAN_GHEP_SAN_PHAM` cũng nằm ngoài `ALL_TABS`. `sync` cũ không tạo và không ghi các tab này. `sync-sources` khi không dry-run phải có backup trước. Workflow `.github/workflows/sync-sources.yml` đặt `DRY_RUN=true` và `ENABLE_PURCHASE=false`. Chưa đẩy GitHub.

Giá không hợp lệ vẫn không bị đổi thành 0.
