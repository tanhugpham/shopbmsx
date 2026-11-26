# UI thống nhất: một spreadsheet cho catalog và báo giá

Phase 4.3B dùng một spreadsheet `GOOGLE_SPREADSHEET_ID` cho catalog, nguồn, MENU và báo giá. Copy dòng từ tab catalog sang tab khách vẫn sai: sẽ đưa giá vốn, mã nội bộ hoặc tên cột sai vào bảng khách.

In bảng này trên máy:

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration quote-layout
```

## Một file, nhiều khu vực tab

| Khu vực | Biến môi trường | Tab liên quan | Ai ghi | Bạn làm gì |
| --- | --- | --- | --- | --- |
| Catalog Shop Xoài Non | `GOOGLE_SPREADSHEET_ID` | `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH` | Tool ghi khi `sync` | Xem giá gốc, tồn kho, cấu hình nội bộ. Không copy sang bảng khách. |
| Nguồn và ghép sản phẩm | `GOOGLE_SPREADSHEET_ID` | `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM` | Tool ghi khi `sync-sources` | Duyệt cặp cần ghép. `BANG_GIA_TOI_UU` là bảng khách của nhánh nguồn. |
| Báo giá chính thức | `GOOGLE_SPREADSHEET_ID` | `MENU`, `NASTELEID`, `BAO_GIA`, `BAO_GIA_TONG_HOP` | `NASTELEID` chỉ nhập tay. Tool ghi `BAO_GIA` từ API, rồi ghi `MENU` và `BAO_GIA_TONG_HOP` từ hai sheet đó | Nhập sản phẩm tay trên `NASTELEID`. Xem giá khách trên `MENU`. Không sửa cột A–H của `MENU`. |

Thiếu `GOOGLE_SPREADSHEET_ID` thì lệnh bỏ qua, không tạo file mới. `NASTELE_SPREADSHEET_ID` và `LAST_SPREADSHEET_ID` đã bỏ; nếu còn trong môi trường thì bị bỏ qua và `config-check` báo tên biến.

## Cột khóa trên NASTELEID và BAO_GIA

Hai tab dùng **cùng một dòng 1**. Tool khóa định dạng (header, đóng băng, khổ cột, số tiền). Có thể thêm cột phụ từ cột H trở đi.

| Cột | BAO_GIA (API shop) | NASTELEID (nhập tay) |
| --- | --- | --- |
| ID | 1, 2, 3… theo thứ tự catalog. Không lấy `local_…`. | Số bạn đánh. |
| PRODUCT | Tên khách, gồm biến thể nếu có. | Tên khách thấy. |
| DESCRIPTION | Mô tả biến thể, đã bỏ liên hệ nội bộ. | Mô tả tay. |
| DURATION | Suy ra từ tên, ví dụ `1 Tháng`. | Thời hạn bạn gõ. |
| WARRANTY | Suy ra từ tên, ví dụ `BHF`. | Bảo hành bạn gõ. |
| PRICE | Giá đã + lợi nhuận, làm tròn đẹp. | Giá đã gõ, không làm tròn lại. |
| STOCK | Số lượng. Hết hàng là `0`. API không gửi số thì `Còn hàng`. | Số bạn gõ. |

`MENU` có thêm cột `STATUS`. Tool suy ra từ `STOCK`: số `0` hoặc chữ `Hết` là `SOLD OUT`, từ 1 đến 5 là `LOW STOCK`, lớn hơn 5 hoặc chữ `Còn` là `IN STOCK`.

## Mẫu dòng NASTELEID

| ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Canva Pro | Học sinh | 1 Năm | Bảo hành 1:1 | 102700 | 12 |

Tool khóa dòng 1 và định dạng. Không ghi dữ liệu từ dòng 2.

## Mẫu dòng BAO_GIA (tool ghi)

| ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | ChatGPT — Plus | API mô tả | 1 Tháng | BHF | 203000 | 36 |

Không sửa tay các cột A–G. Không có `local_id`.

> **Cảnh báo Phase 4.3D/4.3E:** bản `BAO_GIA` đang có dữ liệu lệch cột (cột B là `Shop Xoài Non`,
> tên sản phẩm nằm ở C). Tool đã phát hiện và **từ chối đọc/ghi** BAO_GIA/BAO_GIA_TONG_HOP
> cho tới khi sửa theo đúng mẫu trên. Phase 4.3E đã lập kế hoạch dựng lại hai tab từ
> ProductID/VariantID, chưa ghi Google. Xem `docs/phase43e_migration.md`.

## Mẫu dòng BAO_GIA_TONG_HOP (tool ghi)

| ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Canva Pro | Học sinh | 1 Năm | Bảo hành 1:1 | 102700 | 12 |
| 2 | ChatGPT — Plus | API mô tả | 1 Tháng | BHF | 203000 | 36 |

Hai nguồn xếp chồng. `ID` trên bảng này được đánh lại từ 1. Giá Nastele giữ nguyên như đã gõ. Giá shop giữ giá đã làm tròn trên `BAO_GIA`.

## MENU (bảng khách)

`MENU` là bản khách của đúng hai sheet `BAO_GIA` và `NASTELEID`. Lệnh `sync`, `report` và `sync-menu` dùng cùng một đường:

1. API Shop Xoài Non có dòng thì ghi `BAO_GIA`. API lỗi hoặc không có dòng thì giữ `BAO_GIA` đang có.
2. Đọc `NASTELEID`. Không ghi dữ liệu sản phẩm vào sheet này. Catalog API không được đổ vào đây.
3. Cập nhật `MENU` bằng cách chép `PRODUCT`, `DESCRIPTION`, `DURATION`, `WARRANTY`, `PRICE`, `STOCK` từ hai sheet đó, rồi điền `STATUS`. Mã `SXN-01` / `NST-01` trên `MENU` không lấy từ cột `ID` của bảng con. `BAO_GIA_TONG_HOP` xếp chồng cùng hai nguồn.

Tool chỉ ghi **giá trị** các ô sản phẩm cột A–H. Không ghi đè Category Header (`🤖 SHOP XOÀI NON`, `AI ACCOUNTS`, `WINDOWS LICENSE`, `NASTELE`, …), banner, header cột, Warranty Policy từ cột J, màu, border hay khổ cột. Sản phẩm mới được thêm vào hàng trống trong đúng vùng danh mục. Chưa có vùng `NASTELE` thì dòng tay rơi vào vùng shop đang có. Trước khi ghi, mọi `@tiensky123` trong Description được đổi thành `@tanhung929`.

Từ Phase 4.3B, trước khi ghi MENU tool đọc cấu trúc thật: 62 vùng gộp, công thức, dropdown và khổ cột/dòng. Ô nằm trong vùng gộp hoặc có công thức bị từ chối; cột I–M bị từ chối; không gửi `batchUpdate` nào chạm MENU; không append dòng. Sản phẩm shop vượt số hàng trống của đúng danh mục thì dừng cả lượt ghi, không đè layout. Baseline và lệnh `menu-baseline` nằm ở [phase43b_single_spreadsheet.md](phase43b_single_spreadsheet.md#9-khóa-layout-menu-phase-43b).

`STOCK` là số thì `MENU` nhận đúng số đó. Ô ghi `Còn hàng` mà không có số thì giữ số đang có trên `MENU` và `STATUS` là `IN STOCK`. Ô `0` hoặc có chữ `Hết` thì `STOCK` là `0` và `STATUS` là `SOLD OUT`. Tên khách đã thêm hậu tố trong ngoặc, sau dấu `|` hoặc `[`, ví dụ `ChatGPT Plus (1 tháng)`, chỉ nhận `STOCK` và `STATUS`. Giá, tên và mô tả của dòng đó không bị viết lại.

Khi nguồn đọc thành công và còn dòng, dòng `IN STOCK` hoặc `LOW STOCK` không còn khớp sản phẩm nào trong đúng vùng được đưa về `0` / `SOLD OUT`. Dòng không bị xóa. `CONTACT` giữ nguyên. API shop lỗi, `BAO_GIA` rỗng, hoặc `NASTELEID` không có dòng dữ liệu thì không đưa các dòng còn lại của nguồn đó về 0.

## Không copy từ file catalog

| Cột trên catalog | Vì sao sai nếu dán sang tổng hợp | Cột đúng trên file tổng hợp |
| --- | --- | --- |
| `Giá gốc` / `Giá thấp nhất (VND)` / `Retail Cost (VND)` | Đó là giá vốn. | `PRICE` |
| `Lợi nhuận (VND)` / `Tỷ lệ lợi nhuận (%)` | Nội bộ, không hiện cho khách. | (không có) |
| `Product ID` / `Variant ID` / `local_…` | Không đưa mã nội bộ lên bảng khách. | `ID` |
| `Tên biến thể` tách cột | File tổng hợp ghép vào tên. | `PRODUCT` |
| `Trạng thái hàng` / `Availability` | Tên cột khác. | `STOCK` |

Nếu tab `NASTELEID` vẫn còn cột giá vốn, lệnh **không** lấy các cột đó. Chỉ đọc `PRICE`. Bảng cũ còn cột `Nguồn` thì dòng đúng `Shop Xoài Non` bị bỏ.

## Config

| Việc | File / ô | Ghi chú |
| --- | --- | --- |
| Bật file Google | `.env` → `GOOGLE_SPREADSHEET_ID` | File Google đã có, đã chia sẻ quyền Sửa cho service account. Chỉ dùng một ID. |
| Sửa giá Nastele | Tab `NASTELEID`, cột `PRICE` | Số nguyên VND. Tool không làm tròn lại. |
| Sửa thời hạn / bảo hành | Tab `NASTELEID`, cột `DURATION` và `WARRANTY` | Chép nguyên văn lên `MENU`. |
| Xem giá shop | Tab `BAO_GIA` | Đổi bằng `sync` / `report`, không gõ tay. |
| Xem bảng khách | Tab `MENU` | Ghép từ `BAO_GIA` và `NASTELEID`. Đổi bằng `sync`, `report` hoặc `sync-menu`. |
| Tỷ lệ lợi nhuận shop | `.env` `PRICING_*` | Chỉ ảnh hưởng `BAO_GIA`, không đổi số đã gõ trên `NASTELEID`. |
| Dry-run | `DRY_RUN=true` | Lập kế hoạch, không ghi ô. |
