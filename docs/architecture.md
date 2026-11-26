# Kiến trúc hiện tại

Hệ thống Python đồng bộ danh mục Shop Xoài Non (và Nastele) sang **một** Google
Spreadsheet, dựng bảng giá khách và ghi báo cáo vào một Google Document có sẵn.
Hệ thống **không** tạo đơn, không trừ ví, không lấy thông tin tài khoản đã giao.

## 1. Luồng dữ liệu

```text
Shop Xoài Non API  (/me, /products, /products/{id}, /quote)
    -> api_client.py            HTTP, retry, chặn thao tác nguy hiểm
    -> product_service.py       chuẩn hóa sản phẩm/biến thể
    -> pricing.py               giá bán = giá gốc + lợi nhuận
    -> sync_service.py          điều phối
         -> sheets_service.py   SAN_PHAM, BIEN_THE, BANG_GIA_BAN, LICH_SU_GIA, ...
         -> combined_quote.py   NASTELEID + BAO_GIA -> MENU, BAO_GIA_TONG_HOP
         -> docs_service.py     báo cáo trong vùng SHOP_XOAI_NON_BAO_CAO_*

Nastele Partner API (/api/partner/products)
    -> suppliers/nastele_client.py -> suppliers/source_sync.py
    -> SO_SANH_NGUON, BANG_GIA_TOI_UU, CAN_GHEP_SAN_PHAM, NASTELE_*
```

`POST /products/{id}/quote` chỉ chạy từ lệnh `quote`. Không có lời gọi mua hàng.

## 2. Thành phần

| Module | Vai trò |
| --- | --- |
| `config.py` | Đọc môi trường bằng pydantic-settings; API key là `SecretStr`; phát hiện biến cũ. |
| `api_client.py` | HTTP, timeout, retry, phân trang, chặn `POST /orders` và `/delivery`. |
| `product_service.py` | Chuẩn hóa sản phẩm, biến thể, mô tả; không suy diễn trường thiếu. |
| `pricing.py` | Bảng lợi nhuận theo khoảng giá, làm tròn VND. |
| `money.py` | Parse/format VND bằng `int`, từ chối số lẻ và dấu phân tách. |
| `sheet_layout.py` | Ghép dòng, lịch sử giá, giữ cột phụ của người dùng. |
| `sheets_service.py` | Đọc/ghi spreadsheet, đọc cấu trúc (merge, công thức, protected range). |
| `docs_service.py` | Chèn báo cáo vào document, thay đúng vùng đánh dấu. |
| `google_auth.py` | Service account, hai scope Sheets + Docs (không xin Drive). |
| `sync_service.py` | Điều phối một lượt `sync`/`report`, gate backup trước khi ghi. |
| `report_service.py`, `quote_form.py` | Dựng văn bản/bảng báo giá cho Docs và Sheets. |
| `cli.py` | Toàn bộ lệnh vận hành. |
| `combined_quote.py` | Hợp nhất `BAO_GIA` + `NASTELEID` thành `MENU`/`BAO_GIA_TONG_HOP`. |
| `sheet_format.py`, `sheet_io.py` | Định dạng khóa header/khổ cột, helper đọc ghi vùng. |
| `menu_layout.py` | Baseline layout MENU, whitelist ghi A:H, `MenuCapacity`, diff drift. |
| `menu_expand.py` | Tự chèn dòng khi nguồn nhiều hơn vùng chọn, dịch công thức/dropdown. |
| `menu_order.py` | Gom dòng cùng họ sản phẩm, hoán đổi khối `A:H`. |
| `menu_sync.py` | Một lượt `sync-menu`: API -> `BAO_GIA` -> `MENU`, guard, order, backup. |
| `menu_preflight.py` | Cổng an toàn trước khi GitHub Actions ghi MENU. |
| `menu_region_backup.py` | Snapshot vùng sắp ghi vào tab `_MENU_SYNC_BACKUP`. |
| `menu_audit.py` | Audit chỉ đọc ProductID/VariantID, dòng legacy, dòng thiếu. |
| `menu_migration.py` | Lập kế hoạch/áp migration schema nguồn (công cụ một lần, có `--confirm`). |
| `backup_service.py` | Snapshot Sheets/Docs, checksum, `restore-plan`, `restore --confirm`. |
| `content_overrides.py` | Khóa nội dung bán hàng trong `data/content_overrides.json`. |
| `catalog/` | `SupplierProduct`, `StoreProduct`, `SupplierMapping`, `CatalogSyncService`, projection MENU. |
| `suppliers/` | Nastele client, matching tên, chọn nguồn rẻ nhất, sync nguồn. |
| `telegram_adapter/` | Protocol cho bot sau này; `build_notifier` luôn trả bộ điều hợp tắt. |
| `web.py` | Web Service Render: `/health`, `/sync-*` có `X-Sync-Token`, luôn dry-run. |
| `workspace.py`, `time_utils.py`, `logging_utils.py`, `errors.py` | Mở gateway, giờ UTC+7, lọc log, lỗi nghiệp vụ. |

## 3. Một spreadsheet, bốn nhóm tab

`GOOGLE_SPREADSHEET_ID` là đích ghi duy nhất. Không tạo file Google mới, không đổi
quyền chia sẻ. `NASTELE_SPREADSHEET_ID` và `LAST_SPREADSHEET_ID` đã bỏ; nếu còn
trong môi trường thì bị bỏ qua và `config-check` liệt kê.

| Nhóm | Tab | Ai ghi | Người dùng thấy gì |
| --- | --- | --- | --- |
| Catalog nội bộ | `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH` | `sync`, `report` | giá gốc, lợi nhuận, tồn kho, lịch sử |
| Nguồn | `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM`, `NASTELE_*` | `sync-sources` (khi không dry-run, sau backup) | so giá hai nguồn, cặp cần duyệt |
| Báo giá | `NASTELEID`, `BAO_GIA`, `MENU`, `BAO_GIA_TONG_HOP` | `NASTELEID` chỉ nhập tay; còn lại do tool | bảng khách |
| Backup bền | `_MENU_SYNC_BACKUP` | `sync-menu-preflight --verify-backup`, `sync-menu` | snapshot + checksum + restore plan |

Tab ngoài `ALL_TABS` (`CAN_GHEP_SAN_PHAM`, `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`) không
bị lệnh `sync` cũ ghi. `Trang tính1` không bao giờ bị ghi.

## 4. Hợp đồng cột báo giá

`NASTELEID` và `BAO_GIA` dùng **cùng dòng tiêu đề**. Header, đóng băng dòng 1 và
khổ cột do tool khóa; cột phụ được thêm từ H trở đi.

| Cột | `BAO_GIA` (tool, từ API shop) | `NASTELEID` (nhập tay) |
| --- | --- | --- |
| `ID` | số thứ tự catalog, không lấy `local_…` | số người dùng đánh |
| `PRODUCT` | tên khách, gồm biến thể | tên khách thấy |
| `DESCRIPTION` | mô tả biến thể, bỏ liên hệ nội bộ | mô tả tay |
| `DURATION` | suy ra từ tên (`1 Tháng`) | thời hạn gõ tay |
| `WARRANTY` | suy ra từ tên (`BHF`) | bảo hành gõ tay |
| `PRICE` | giá đã cộng lợi nhuận, làm tròn đẹp | giá đã gõ, không làm tròn lại |
| `STOCK` | số lượng; hết hàng `0`; API không gửi số thì `Còn hàng` | số gõ tay |

`MENU` là bản khách của đúng hai tab trên, thêm cột `STATUS` suy từ `STOCK`:

| `STOCK` | `STATUS` |
| --- | --- |
| `0` hoặc chữ `Hết` | `SOLD OUT` |
| 1–5 | `LOW STOCK` |
| lớn hơn 5 hoặc chữ `Còn` | `IN STOCK` |

Quy tắc giữ nguyên qua mọi lượt ghi:

- `MENU` giữ mã vùng `SXN-…` / `NST-…`, không lấy `ID` của bảng con.
- `BAO_GIA_TONG_HOP` xếp chồng hai nguồn, `ID` đánh lại từ 1, không chọn nguồn rẻ hơn.
- Tool chỉ ghi **giá trị ô sản phẩm A–H**. Không ghi Category Header, banner,
  header cột, Warranty Policy từ cột J, màu, border, khổ cột.
- Sản phẩm mới vào hàng trống trong đúng vùng danh mục; dòng nguồn không còn khớp
  bị đưa về `0` / `SOLD OUT`, không bị xóa, `CONTACT` giữ nguyên.
- Tên đã có hậu tố trong ngoặc, sau `|` hoặc `[` chỉ nhận `STOCK`/`STATUS`; giá, tên,
  mô tả của dòng đó không bị viết lại.
- Trước khi ghi, `@tiensky123` trong Description được đổi thành `@tanhung929`.
- Không copy từ tab catalog: `Giá gốc`, `Lợi nhuận`, `Product ID`, `Variant ID`,
  `local_…`, tên biến thể tách cột, `Trạng thái hàng` đều không thuộc bảng khách.
  `BANG_GIA_BAN` và Google Doc cũng chỉ có giá bán, không có giá vốn.

## 5. Khóa layout MENU

Baseline chính thức: `data/menu-baseline.json` (commit trong Git), chụp lại bằng
`python -m shop_integration menu-baseline --out data/menu-baseline.json`.

| Hạng mục | Giá trị chuẩn |
| --- | --- |
| Dòng tiêu đề | 6 (`ID … STATUS`), đúng tên và đúng thứ tự |
| Vùng sản phẩm | `A8:H35` sau khi mở rộng; vùng mở rộng là A31:H35 |
| Vùng gộp | 62; preflight yêu cầu đúng 62 |
| Công thức | `G5` (`=NOW()`), `J39`, `J40` |
| Dropdown | `K38`, `ONE_OF_RANGE` → `MENU!$B$8:$B$35` |
| Protected range | `A1:H6`, `I1:M55` (`warningOnly=true`) |
| Conditional format | 8 rule cùng phủ `H7:H45` |
| Panel chi tiết | bắt đầu dòng 37 (`J37:M37`, merge `J40:M45`) |
| Cột được ghi | chỉ `A:H`; cột `I` là đệm; `J:M` là chính sách bảo hành |

Whitelist ghi (`menu_layout.py`):

- Chỉ ghi **giá trị** ô `A:H`; mọi ô `J:M` bị từ chối.
- Không ghi dòng tiêu đề, không ghi ngoài dòng dữ liệu của lần đọc, không `append`.
- Ô nằm trong vùng gộp, đang có công thức, hoặc trong protected range bị từ chối.
- Không gửi bất kỳ `batchUpdate` nào chạm `sheetId` của MENU: merge, unmerge,
  `repeatCell`, màu, border, `setDataValidation`, conditional format, banding,
  filter, dimension.
- `LayoutLockedGateway` bọc gateway để chặn cả lớp ghi giá trị lẫn `batchUpdate`
  trước khi tới Google.

Kiểm tra mỗi lượt trước khi ghi:

1. Dựng guard từ merge/công thức/dropdown/protected range đọc live.
2. Dòng tiêu đề `A6:H6` phải đúng `ID | PRODUCT | DESCRIPTION | DURATION |
   WARRANTY | PRICE | STOCK | STATUS`; lệch tên hoặc thứ tự thì dừng.
3. Kiểm tra dữ liệu nguồn: `PRODUCT` không trống, `PRICE` dương, `STOCK` hợp lệ,
   không trùng tên trong cùng nguồn.
4. Nếu đặt `MENU_BASELINE_FILE` (workflow đặt `data/menu-baseline.json`), so cấu
   trúc với baseline; lệch merge/công thức/dropdown/khổ cột/header thì dừng, không
   ghi ô nào. Ngoại lệ duy nhất: cấu trúc hiện tại đúng bằng baseline dịch xuống
   đúng số dòng tool vừa tự mở rộng.
5. Sản phẩm vượt số hàng trống của đúng danh mục thì dừng cả lượt ghi (trừ khi
   `MENU_AUTO_EXPAND=true` chèn thêm dòng).

## 6. Tự mở rộng MENU

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `MENU_AUTO_EXPAND` | `true` | Chèn dòng khi nguồn nhiều hơn vùng chọn `K`. `false` thì chỉ cảnh báo. |
| `MENU_EXPAND_STEP` | `10` | Số dòng chèn mỗi lần, 1–50. |
| `MENU_EXPAND_MAX_ROWS` | `40` | Trần dòng chèn trong một lượt, 1–200; thiếu thì lượt sau chèn tiếp. |

Chèn dòng làm dịch merge, dropdown `K38` và công thức `J39/J40` xuống đúng số dòng
mới; khổ cột, màu và conditional format giữ nguyên. Sau khi đã mở rộng tới trần,
muốn có baseline mới thì chụp lại có chủ ý, không sửa tay file baseline.

## 7. Gom sản phẩm cùng họ

`menu_order.py`, bật mặc định bằng `MENU_GROUP_SIMILAR=true`, chạy ngay trong lượt
`sync-menu` sau khi làm sạch mô tả:

1. `fold_menu_text` bỏ dấu tiếng Việt, viết thường, gộp khoảng trắng.
2. `menu_family` dò họ theo luật (`ChatGPT`, `Canva`, `Google Drive`, `Grok`,
   `Capcut`, `MUSE`, …). Không khớp luật nào thì lấy `~` + hai token đầu, nhờ vậy
   hai tên lạ khác nhau không bị gộp.
3. `cluster_menu_rows` gom theo họ, giữ **thứ tự xuất hiện đầu tiên**, nên thứ tự
   cụm ổn định qua các lượt sync; sản phẩm mới cùng họ tự vào đúng cụm.
4. `plan_menu_order` chỉ lấy dòng sản phẩm thật: bỏ dòng có công thức/merge trong
   `A:H`, bỏ dòng bị `MenuCapacity` khóa. Dòng không sắp xếp được chỉ bị bỏ khỏi
   vùng, không bị sửa.
5. `apply_menu_order` hoán đổi **nguyên khối `A:H`** của từng dòng (RAW), rồi đọc
   lại và so; lệch thì `raise MenuLayoutError` kèm hướng dẫn khôi phục từ
   `backups/`. Vì chỉ ghi `A:H`, merge, dropdown `K38`, công thức `J39/J40`,
   conditional format, protected range và banding không bị chạm tới.
6. Đã đúng thứ tự thì không ghi ô nào (idempotent); `DRY_RUN=true` chỉ ghi chú.
   `MENU_GROUP_SIMILAR=false` bỏ qua toàn bộ bước này và ghi chú.

## 8. Giá

VND giữ bằng `int`. Parser nhận số nguyên, chuỗi chữ số, và float chỉ khi phần thập
phân bằng 0; số lẻ, số âm, chuỗi có dấu phân tách bị từ chối.

- Lợi nhuận tính trên **giá gốc** (không phải margin trên doanh thu) bằng `Decimal`.
- Dưới 100000 và trên 200000: giá gốc × tỷ lệ, làm tròn nửa lên (`ROUND_HALF_UP`).
- Từ 100000 đến 200000 (kể cả hai đầu): mức cố định `PRICING_MIDDLE_PROFIT`
  (mặc định 35000, chỉ nhận 30000–40000).
- Giá bán = giá gốc + lợi nhuận. Luôn tính lại từ `prices.vnd` mới nhất.
- Ô Sheets ghi `RAW`. Chuỗi bắt đầu bằng `=`, `+`, `-`, `@`, tab hoặc xuống dòng
  được thêm zero-width space để lần khôi phục `USER_ENTERED` không chạy công thức;
  Product ID và Variant ID không bị thêm.

## 9. Điều phối đồng bộ

| Lệnh | Ghi gì | Gate |
| --- | --- | --- |
| `sync` | catalog `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH`, Docs | backup `verified` khi `DRY_RUN=false` |
| `report` | Docs (và bảng giá nếu cần) | như `sync` |
| `sync-sources` | `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `NASTELE_*` | backup trước khi ghi |
| `sync-menu` | `BAO_GIA`, `MENU`, `BAO_GIA_TONG_HOP` | `MENU_REQUIRE_DURABLE_BACKUP=true` và snapshot tab bền |

`DRY_RUN=true` vẫn được đọc Sheets để tính chênh giá nhưng không gọi `addSheet`,
`update`, `append`.

Sheets không có giao dịch nhiều tab: nếu một lệnh ghi thất bại giữa chừng, các tab
trước đó có thể đã đổi. Nhật ký chỉ được thêm sau khi các bảng chính đã ghi. Docs
nhận `insertText` trong vùng đánh dấu `SHOP_XOAI_NON_BAO_CAO_BEGIN/END`; chỉ thấy
một dấu thì dừng, không sửa document.

### Backup bền trước khi ghi MENU

`menu_region_backup.py` chụp `MENU!A1:H250`, `BAO_GIA!A1:H400`,
`BAO_GIA_TONG_HOP!A1:H400` (giá trị + công thức), che bí mật, tính SHA-256, ghi vào
tab `_MENU_SYNC_BACKUP` theo từng phần (PART), đọc lại và đối chiếu checksum. Chỉ
giữ 8 snapshot gần nhất. Không tạo file Drive mới, không đổi quyền chia sẻ, không
ghi cột `I:M` của MENU. Không có snapshot `verified` thì không ghi MENU.

## 10. Hai nhà cung cấp và catalog hai tầng

Shop Xoài Non đi qua `api_client.py`; Nastele có client riêng
(`suppliers/nastele_client.py`, header `X-API-Key`, trần 60 request/phút, một GET
trả cả danh mục). `POST /api/partner/orders` và mọi đường `orders` bị chặn trước
HTTP; khóa tài khoản/mật khẩu/2fa bị bỏ ngay cả khi lọt vào JSON.

- Khóa offer: `supplier_id|product_id|variant_id`. Nastele không có biến thể nên
  phần biến thể để trống và ghi nhãn `API không có biến thể`.
- `stock`: lớn hơn 0 còn hàng, bằng 0 hết hàng, null/vắng chưa xác nhận. Số lượng 0
  của Shop Xoài Non không tự thành hết hàng (shop có cờ `in_stock` riêng).
- Ghép tên (`suppliers/matching.py`): trùng multiset tên đã chuẩn hóa hoặc cùng
  thương hiệu + cùng nhóm tên + thuộc tính giao hàng bằng nhau → `EXACT_MATCH`; gần
  đúng (Jaccard ≥ 0,6) hoặc thiếu thuộc tính một bên → `REVIEW_REQUIRED`; còn lại
  `DIFFERENT_PRODUCT`. Chỉ `EXACT_MATCH` được so giá; cặp cần duyệt nằm ở
  `CAN_GHEP_SAN_PHAM` và hai ô do người dùng nhập không bị ghi đè.
- Chọn nguồn (`suppliers/selection.py`): giá lẻ luôn được xét, bậc sỉ chỉ khi
  `minQuantity <= COMPARISON_QUANTITY`. Chọn nguồn còn hàng có giá đơn vị thấp nhất;
  hòa thì `PREFERRED_SUPPLIER_ID`, rồi khóa tổ hợp. Nguồn rẻ hơn nhưng hết hàng
  hoặc chưa xác nhận không được chọn. Dữ liệu cũ/thiếu → `CẦN XÁC MINH`.
- `data=[]` từ Nastele là `NASTELE_CATALOG_EMPTY`: danh mục rỗng đọc được, không
  phải lỗi xác thực, không tạo offer giả, không đổi nguồn đã chọn.

Package `catalog/` bổ sung lớp bán độc lập API:

- `SupplierProduct`: dữ liệu nguồn đã chuẩn hóa, có ProductID/VariantID hoặc
  `source_key` do adapter/người vận hành kiểm soát.
- `StoreProduct`: sản phẩm bán cho khách, dùng `store_product_id`/`menu_code`;
  không chứa ID nguồn trong dữ liệu khách.
- `SupplierMapping`: mapping nội bộ, bắt buộc ID nguồn hoặc `source_key`, phải được
  duyệt; tên sản phẩm không phải khóa.
- `ContentOverride` (`data/content_overrides.json`): khóa tên/mô tả/thời hạn/nội
  dung bán hàng/contact; API không ghi đè trường đã khóa. Bảo hành do nguồn quyết định.
- `CatalogSyncService` + `menu_projection`: chỉ xuất đúng 8 cột MENU, chặn ID nội
  bộ/giá vốn/mapping.

## 11. Hợp đồng API Shop Xoài Non

Xác thực: header `x-api-key`. Đã đối chiếu OpenAPI + ví dụ ngày 2026-11-23.

| Đường | Dùng | Đã xác nhận |
| --- | --- | --- |
| `GET /products` | phân trang, `limit` ≤ 200, `offset`, `search` | `data[]` gồm `id`, `title`, `in_stock`, `variants[]`; biến thể có `id`, `prices.vnd`, `in_stock`, `available_quantity` |
| `GET /products/{id}` | một sản phẩm | đường dẫn + 404; thân giả định cùng shape danh sách |
| `POST /products/{id}/quote` | chỉ lệnh `quote` | body `variant_id`, `quantity`, `currency_code`; trả `unit_price`, `total`, `can_purchase` |
| `GET /orders`, `GET /orders/{id}` | chỉ in mã + trạng thái | văn bản tài liệu |
| `POST /orders`, `GET /orders/{id}/delivery` | **bị chặn trước khi mở kết nối** | — |

Chưa được tài liệu xác nhận: thân `GET /me`, thân `GET /wallet/balance`, tổng số
trang (client dừng khi trang ngắn hơn `limit`, trang rỗng, trang lặp, hoặc đủ 100
trang). CLI chỉ in tên khóa cho `/me`, và chỉ in số duy nhất cho `/wallet/balance`
kèm ghi chú chưa xác nhận.

Retry: chỉ `GET`, tối đa 4 lần kể cả lần đầu, cho timeout, lỗi kết nối, 408, 429,
5xx. 429 dùng `Retry-After` (trần 60 giây), không có thì 0,5 → 1 → 2 → 4 giây, trần
8. Không retry 401/403/404, lỗi JSON, và mọi `POST`. Chuyển hướng 3xx bị từ chối.

## 12. Google, Docs, Telegram, log

- `GoogleSheetsGateway` và `GoogleDocsGateway` là lớp mỏng quanh
  `google-api-python-client`; test dùng gateway trong bộ nhớ.
- Scope OAuth: `spreadsheets` + `documents`. Không xin Drive, nên service account
  không tự tìm file: phải đưa ID và chia sẻ từng file.
- `telegram_adapter/` chỉ là protocol; `TelegramDisabledNotifier.send` luôn lỗi;
  `ENABLE_TELEGRAM` không đổi hành vi.
- Logger `httpx`, `httpcore`, `googleapiclient` đặt mức WARNING. Formatter thay API
  key dài từ 8 ký tự và cụm `BEGIN PRIVATE KEY` bằng `***`. Thông điệp lỗi API
  không chép thân phản hồi.
- Bí mật chỉ nằm trong biến môi trường/`.env`; `CAU_HINH` chỉ ghi
  `api_key_configured=có/không`.

## 13. Giới hạn đã biết

- Vùng sản phẩm MENU là hữu hạn (`A8:H35` sau mở rộng); vượt trần thì phải mở rộng
  layout có chủ ý, không để tool ghi đè.
- Panel chính sách `J:M` bắt đầu dòng 37, nên không thể thêm sản phẩm vô hạn mà
  không đổi layout.
- Sheets không có transaction nhiều tab; lỗi giữa chừng để lại trạng thái một phần,
  khôi phục bằng tab `_MENU_SYNC_BACKUP` hoặc `restore --confirm`.
- Cron GitHub Actions có thể trễ hàng chục phút; không phải bảo đảm thời gian thực.
- Danh mục Nastele hiện có thể rỗng; hệ thống giữ dữ liệu cũ và báo
  `NASTELE_CATALOG_EMPTY` thay vì ghi đè.
- `menu-migrate` là công cụ một lần cho migration schema nguồn; không dùng lại cho
  kế hoạch mới khi chưa chụp baseline/plan mới.



