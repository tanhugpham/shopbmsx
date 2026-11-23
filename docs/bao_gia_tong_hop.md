# UI thống nhất: file catalog và file báo giá tổng hợp

Hai file Google là hai việc khác nhau. Copy dòng từ file catalog sang file tổng hợp sẽ đưa giá vốn, mã nội bộ hoặc tên cột sai vào bảng khách.

In bảng này trên máy:

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration quote-layout
```

## Hai file

| File | Biến môi trường | Tab liên quan | Ai ghi | Bạn làm gì |
| --- | --- | --- | --- | --- |
| Catalog Shop Xoài Non | `GOOGLE_SPREADSHEET_ID` | `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH` | Tool ghi khi `sync` | Xem giá gốc, tồn kho, cấu hình nội bộ. Không copy sang file tổng hợp. |
| Báo giá chính thức | `LAST_SPREADSHEET_ID` | `MENU`, `NASTELEID`, `BAO_GIA`, `BAO_GIA_TONG_HOP` | `MENU` là bảng khách. Tool ghi `MENU`, `BAO_GIA`, `BAO_GIA_TONG_HOP`. Catalog Nastele ghi vào `NASTELEID` khi API có hàng | Xem giá khách trên `MENU`. Không sửa cột A–H của `MENU`. |

File tổng hợp để trống `LAST_SPREADSHEET_ID` thì lệnh bỏ qua, không tạo file mới.

## Cột khóa trên NASTELEID và BAO_GIA

Hai tab dùng **cùng một dòng 1**. Tool khóa định dạng (header, đóng băng, khổ cột, số tiền). Có thể thêm cột phụ từ cột H trở đi.

| Cột | BAO_GIA (API shop) | NASTELEID (nhập tay) |
| --- | --- | --- |
| STT | 1, 2, 3… theo thứ tự catalog. Không lấy `local_…`. | 1, 2, 3… do bạn đánh. |
| Nguồn | `Shop Xoài Non` | `NASTELEID` hoặc để trống |
| Tên sản phẩm | `title` sản phẩm | Tên khách thấy |
| Tên biến thể | `title` biến thể | Gói / thời hạn |
| Mô tả | `description` biến thể | Mô tả tay |
| Giá bán | Giá đã + lợi nhuận, làm tròn đẹp | Giá đã gõ, không làm tròn lại |
| Tình trạng | Còn hàng / Hết hàng | Còn hàng / Hết hàng / Chưa có dữ liệu |

## Mẫu dòng NASTELEID

| STT | Nguồn | Tên sản phẩm | Tên biến thể | Mô tả | Giá bán | Tình trạng |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | NASTELEID | Canva Pro | Gói năm | Học sinh | 102700 | Còn hàng |

Tool khóa dòng 1 và định dạng. Không ghi dữ liệu từ dòng 2.

## Mẫu dòng BAO_GIA (tool ghi)

| STT | Nguồn | Tên sản phẩm | Tên biến thể | Mô tả | Giá bán | Tình trạng |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Shop Xoài Non | ChatGPT | Plus | API mô tả | 203000 | Còn hàng |

Không sửa tay các cột A–G. Không có `local_id`.

## Mẫu dòng BAO_GIA_TONG_HOP (tool ghi)

| STT | Nguồn | Tên sản phẩm | Tên biến thể | Mô tả | Giá bán | Tình trạng |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | NASTELEID | Canva Pro | Gói năm | Học sinh | 102700 | Còn hàng |
| 1 | Shop Xoài Non | ChatGPT | Plus | API mô tả | 203000 | Còn hàng |

Hai nguồn xếp chồng. Giá Nastele giữ nguyên như đã gõ. Giá shop giữ giá đã làm tròn trên `BAO_GIA`.

## MENU (bảng khách)

Tool chỉ ghi **giá trị** các ô sản phẩm cột A–H. Không ghi đè Category Header (`🤖 SHOP XOÀI NON`, `AI ACCOUNTS`, `WINDOWS LICENSE`, …), banner, header cột, Warranty Policy từ cột J, màu, border hay khổ cột. Sản phẩm mới được thêm vào hàng trống trong đúng vùng danh mục. Trước khi ghi, mọi `@tiensky123` trong Description được đổi thành `@admin`.

## Không copy từ file catalog

| Cột trên catalog | Vì sao sai nếu dán sang tổng hợp | Cột đúng trên file tổng hợp |
| --- | --- | --- |
| `Giá gốc` / `Giá thấp nhất (VND)` / `Retail Cost (VND)` | Đó là giá vốn. | `Giá bán` |
| `Lợi nhuận (VND)` / `Tỷ lệ lợi nhuận (%)` | Nội bộ, không hiện cho khách. | (không có) |
| `Product ID` / `Variant ID` tách cột | Khóa tổng hợp là một ô `Mã`. | `Mã` |
| `Tên biến thể` tách cột | File tổng hợp ghép vào tên. | `Tên sản phẩm` |
| `Trạng thái hàng` / `Availability` | Tên cột khác, map lệch. | `Tình trạng` |

Nếu tab `NASTELEID` vẫn còn cột giá vốn, lệnh **không** lấy các cột đó. Chỉ đọc `Giá bán`. Dòng có `Nguồn` = `Shop Xoài Non` trên `NASTELEID` bị bỏ.

## Config

| Việc | File / ô | Ghi chú |
| --- | --- | --- |
| Bật file tổng hợp | `.env` → `LAST_SPREADSHEET_ID` | File Google đã có, đã chia sẻ quyền Sửa cho service account. |
| Sửa giá Nastele | Tab `NASTELEID`, cột `Giá bán` | Số nguyên VND. Tool không làm tròn lại. |
| Sửa gói Nastele | Tab `NASTELEID`, cột `Cấu hình` | Tự do, một dòng. |
| Xem giá shop | Tab `BAO_GIA` | Đổi bằng `sync` / `report`, không gõ tay. |
| Xem bảng khách | Tab `BAO_GIA_TONG_HOP` | Đổi bằng `sync` / `report`. |
| Tỷ lệ lợi nhuận shop | `.env` `PRICING_*` | Chỉ ảnh hưởng `BAO_GIA`, không đổi số đã gõ trên `NASTELEID`. |
| Dry-run | `DRY_RUN=true` | Lập kế hoạch, không ghi ô. |
