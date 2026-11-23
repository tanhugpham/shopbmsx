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
| Báo giá tổng hợp | `LAST_SPREADSHEET_ID` | `NASTELEID`, `BAO_GIA`, `BAO_GIA_TONG_HOP` | Người dùng chỉ sửa `NASTELEID`. Tool ghi `BAO_GIA` và `BAO_GIA_TONG_HOP` khi `sync` hoặc `report` | Nhập Nastele tay trên `NASTELEID`. Xem bảng khách trên `BAO_GIA_TONG_HOP`. |

File tổng hợp để trống `LAST_SPREADSHEET_ID` thì lệnh bỏ qua, không tạo file mới.

## Cột thống nhất trên file tổng hợp

Dòng 1 của **cả ba tab** phải cùng bộ cột này. Có thể thêm cột phụ bên phải (`Ghi chú`, màu). Tool không xóa cột phụ.

| Cột | Bắt buộc | NASTELEID (nhập tay) | BAO_GIA (tool) | BAO_GIA_TONG_HOP (tool) |
| --- | --- | --- | --- | --- |
| Nguồn | Có trên `BAO_GIA` và tổng hợp | Điền `NASTELEID` hoặc để trống. Không điền `Shop Xoài Non`. | Luôn là `Shop Xoài Non`. | Copy nguyên từ nguồn gốc. |
| Mã | Có | Mã Nastele, ví dụ `43`. Cột cũ `Nastele ID` vẫn đọc được. | `product_id\|variant_id`, ví dụ `local_81\|local_81`. | Copy nguyên. Không dùng `Product ID` của `SAN_PHAM`. |
| Tên sản phẩm | Có | Tên hiển thị cho khách. | Tên shop, ghép biến thể khi khác tên sản phẩm. | Copy nguyên từng nguồn. |
| Giá bán | Có | Giá đã bán, giữ nguyên số đã gõ. Không lấy giá gốc. | Giá shop đã cộng lợi nhuận rồi làm tròn đẹp. | Copy nguyên. Không tính lại, không chọn nguồn rẻ. |
| Tình trạng | Có | `Còn hàng` / `Hết hàng` / `Chưa có dữ liệu`. | `Còn hàng` / `Hết hàng` / nhãn API shop. | Copy nguyên từng nguồn. |
| Cấu hình | Không | Gói, thời hạn. Không dán HTML thô. | Mô tả biến thể đã chuẩn hóa. | Copy nguyên từng nguồn. |

## Mẫu dòng NASTELEID

| Nguồn | Mã | Tên sản phẩm | Giá bán | Tình trạng | Cấu hình |
| --- | --- | --- | --- | --- | --- |
| NASTELEID | 43 | Canva Pro 1 năm | 102700 | Còn hàng | Gói năm |

Tool chỉ đọc tab này. Không tạo tab, không ghi ô, không xóa dòng, không đổi định dạng.

## Mẫu dòng BAO_GIA (tool ghi)

| Nguồn | Mã | Tên sản phẩm | Giá bán | Tình trạng | Cấu hình |
| --- | --- | --- | --- | --- | --- |
| Shop Xoài Non | local_cg\|v1 | ChatGPT — Plus | 203000 | Còn hàng | API mô tả |

Không sửa tay các cột chuẩn trên tab này. Cột phụ bên phải được giữ.

## Mẫu dòng BAO_GIA_TONG_HOP (tool ghi)

| Nguồn | Mã | Tên sản phẩm | Giá bán | Tình trạng | Cấu hình |
| --- | --- | --- | --- | --- | --- |
| NASTELEID | 43 | Canva Pro 1 năm | 102700 | Còn hàng | Gói năm |
| Shop Xoài Non | local_cg\|v1 | ChatGPT — Plus | 203000 | Còn hàng | API mô tả |

Hai nguồn xếp chồng. Giá Nastele giữ nguyên như đã gõ. Giá shop giữ giá đã làm tròn trên `BAO_GIA`.

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
