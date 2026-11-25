# Phase 4.3F — Kiến trúc người bán lại nhiều nhà cung cấp

Tài liệu này mô tả phần refactor local đã triển khai. Không có live write Google,
không đổi Phase 4.3E, không bật mua hàng/Telegram/scheduler.

## 1. Kiến trúc hiện tại và điểm phụ thuộc nhà cung cấp

Luồng đang chạy:

```text
Shop API / Nastele API
  -> suppliers/providers.py (adapter riêng từng API)
  -> suppliers/models.py NormalizedOffer
  -> suppliers/matching.py + selection.py (đối chiếu, chọn giá)
  -> suppliers/source_sync.py (SO_SANH_NGUON, BANG_GIA_TOI_UU, CAN_GHEP_SAN_PHAM)
  -> menu_sync.py + combined_quote.py (BAO_GIA, NASTELEID, MENU)
```

Điểm còn phụ thuộc nguồn:

- `source_sync.py` đang viết cứng hai nhà cung cấp: cột `Shop Xoài Non ...`,
  `Nastele ...`, hàm đếm `only_shop`, `only_nastele`, hàm khôi phục `_SHOP_FIELDS`,
  `_NASTELE_FIELDS`. Thêm API thứ ba sẽ phải sửa nhiều nhánh.
- `config.py` chỉ chấp nhận `PREFERRED_SUPPLIER_ID` là `shop_xoai_non` hoặc `nastele`.
- `menu_sync.py` lấy `BAO_GIA` từ Shop API và đọc tay `NASTELEID`; MENU ghép hai
  nguồn đó. Vì vậy MENU hiện vẫn gắn với cách nhập của từng nhà cung cấp.
- `combined_quote.py` có trường nội bộ `ProductID`/`VariantID` trong dòng báo giá
  để phục vụ audit; các trường này không được đi vào MENU nhưng vẫn là điểm cần
  kiểm soát khi thêm nguồn mới.
- `matching.py` dùng tên đã chuẩn hóa để tìm ứng viên, nhưng chỉ gộp tự động khi
  đủ bằng chứng thuộc tính. Đây là điểm có thể tái sử dụng, không phải khóa định danh.

## 2. Kiến trúc mới đề xuất

Package mới `shop_integration/catalog/`:

```text
Supplier API A ─┐
Supplier API B ─┼─> SupplierAdapter.fetch() -> SupplierProduct
Supplier API C ─┘                         (lớp nguồn, có ID hoặc source_key)

SupplierProduct + StoreProduct + SupplierMapping
  -> CatalogSyncService.build_plan()
      1. fetch từng adapter
      2. chuẩn hóa SupplierProduct
      3. đối chiếu mapping nội bộ, kiểm tra tương đương
      4. áp ContentOverride (trường khóa không bị API ghi đè)
      5. tính giá bán từ giá vốn
      6. cập nhật tồn kho/trạng thái
      7. chiếu ra customer_menu_rows()
  -> MENU guard / writer chỉ ghi A:H khi có phê duyệt riêng
```

### Lớp nguồn — `SupplierProduct`

Có `supplier_id`, `supplier_name`, `supplier_product_id` (nếu có),
`supplier_variant_id` (nếu có), `source_key` (khi API không có ID ổn định),
tên/mô tả gốc, giá vốn, tồn kho, availability, attributes, warnings.

Khóa nội bộ `identity_key` chỉ lấy từ ID nguồn hoặc `source_key`; không lấy tên.

### Lớp bán — `StoreProduct`

Có `store_product_id` ổn định, tên/mô tả hiển thị, duration, warranty, giá bán,
trạng thái tồn, contact mặc định `@tanhung929`, `sales_content`, content override,
mapping nội bộ và `publication_status`.

`customer_row()` chỉ trả đúng 8 cột MENU hiện tại:
`ID`, `PRODUCT`, `DESCRIPTION`, `DURATION`, `WARRANTY`, `PRICE`, `STOCK`, `STATUS`.

### Mapping an toàn

`SupplierMapping` bắt buộc có đúng một trong hai khóa:

- `supplier_product_id` (+ `supplier_variant_id` nếu có), hoặc
- `source_key` do adapter/người vận hành kiểm soát.

Mapping phải `approved=True`. Mapping thiếu khóa, mapping chưa duyệt, mapping trỏ
tới store không tồn tại, hoặc một nguồn map tới nhiều store đều sinh issue và
không tự chọn nguồn.

Với API không có ID, adapter đặt `source_key` ổn định (ví dụ slug nội bộ đã kiểm
soát) và người vận hành duyệt mapping. Nếu không có `source_key`, sản phẩm vào
danh sách cần xác nhận, không bị bỏ qua âm thầm.

### Đối chiếu nhiều nguồn

Khi nhiều nguồn cùng map vào một store, catalog dùng `classify_pair` hiện có:

- `EXACT_MATCH`: được phép so giá và chọn nguồn rẻ nhất còn hàng.
- `REVIEW_REQUIRED`: đưa vào danh sách xác nhận, không tự chọn.
- `DIFFERENT_PRODUCT`: chặn, giữ dữ liệu cũ và đánh dấu cần xác minh.

Vì vậy cùng tên nhưng khác thời hạn/phiên bản/điều kiện bảo hành không bị gộp.
Hai sản phẩm trùng tên nhưng mapping khác store vẫn tồn tại độc lập.

### Content ownership

`ContentOverride.locked_fields` cho phép khóa `display_name`, `display_description`,
`duration`, `sales_content`, `contact`. Trường đã khóa giữ giá trị người vận hành;
API không ghi đè.

`warranty` không nằm trong danh sách override. Bảo hành lấy từ tên/mô tả nguồn nếu
nguồn nêu rõ; nếu không, giữ bảo hành đã có. Điều này tránh tự đổi điều kiện kích
hoạt/bảo hành bắt buộc.
## 3. Module có thể tái sử dụng

- `suppliers/models.py::NormalizedOffer` — lớp offer hiện có; `ExistingProviderAdapter`
  bọc provider Phase 4.2 mà không đổi client HTTP.
- `suppliers/matching.py` — `classify_pair`, `signature_of`, `compare_catalog`.
- `suppliers/selection.py` — `select_group`, `eligible_unit_cost` cho giá sỉ.
- `pricing.py` — `quote_cost`, `round_pretty_vnd`.
- `text_normalize.py` — làm sạch mô tả, chặn formula.
- `combined_quote.py` — `quote_duration`, `quote_warranty`, `plan_menu_cells`,
  `MENU_HEADERS`; projection mới tương thích 8 cột.
- `menu_layout.py` — guard merge/formula/dropdown/protected range.
- `menu_migration.py` — migration 8 thao tác hiện tại.
- `backup_service.py` — backup/checksum/restore plan.

## 4. Module cần điều chỉnh ở giai đoạn sau

- `source_sync.py`: thay các cột cứng `Shop/Nastele` bằng bảng nguồn động hoặc
  chuyển hẳn sang catalog mới. Chưa làm trong lượt này để không đổi đường live.
- `config.py`: mở `PREFERRED_SUPPLIER_ID` cho supplier_id bất kỳ; hiện vẫn giới hạn
  hai nguồn.
- `menu_sync.py`: cần một chế độ đọc `CatalogPlan` thay vì chỉ `BAO_GIA` + `NASTELEID`
  sau khi có phê duyệt migration.
- `combined_quote.py`: có thể bỏ dần metadata `ProductID`/`VariantID` khỏi dữ liệu
  trung gian khi MENU đã dùng store ID; không đổi trong lượt này.
- `menu_migration.py`: chỉ cần điều chỉnh nếu nguồn đích đổi cột. Với 8 thao tác
  hiện tại, chưa cần đổi.

## 5. Cách bảo vệ MENU

- `menu_projection.customer_menu_rows()` chỉ trả 8 cột hiện tại.
- `assert_customer_safe()` chặn cột lạ, cấu trúc lồng nhau, khóa dạng
  `supplier|product|variant` và giá trị nội bộ được truyền vào.
- Trùng ID MENU bị chặn trước khi ghi.
- `contact` mặc định `@tanhung929` nằm trong `StoreProduct`/`customer_profile`;
  MENU 8 cột hiện tại không có cột contact nên không tự chèn để giữ layout.
- Không có gateway Google trong `CatalogSyncService`.
- Đường ghi MENU hiện tại vẫn đi qua `MenuLayoutGuard`/`plan_menu_cells`; test mới
  xác nhận các ô dự kiến chỉ nằm trong A:H.
- Không đổi merge, công thức, dropdown, định dạng, vùng bảo hành J:M.

## 6. Đánh giá migration 8 thao tác Phase 4.3E

8 vùng đã được phê duyệt:

1. `BAO_GIA!A1:G25`
2. `BAO_GIA!H1:H25`
3. `BAO_GIA_TONG_HOP!A1:G26`
4. `MENU!A31:H31`
5. `MENU!A36:H36`
6. `MENU!K38`
7. `MENU!J39`
8. `MENU!J40`

Kết luận: **có thể tái sử dụng, chưa cần đổi migration**. Lý do:

- Catalog mới vẫn chiếu ra đúng 8 cột A:H của MENU.
- Nguồn `BAO_GIA`/`BAO_GIA_TONG_HOP` vẫn dùng cùng shape A:G.
- `K38`, `J39`, `J40` là layout/selector, không phụ thuộc ProductID của nguồn.
- Store ID trên MENU là mã hiển thị (`menu_code`), không phải ID nhà cung cấp.

Phase 4.3E vẫn đang dừng vì source fingerprint drift. Không có live write trong
lượt này. Muốn apply phải tạo plan v3 từ nguồn hiện tại và được phê duyệt lại.

## 7. Kết quả kiểm thử

File mới: `tests/test_catalog_multi_supplier.py` — 23 ca, bao phủ:

- nhiều API và một API lỗi không làm mất API còn lại;
- API có ID và API không có ID (`source_key` + mapping duyệt tay);
- trùng tên, trùng ID số giữa hai nguồn, cùng tên khác thời hạn/phiên bản/bảo hành;
- nhiều nguồn tương đương chọn giá rẻ, giữ backup;
- content override bị khóa, bảo hành do nguồn quyết định;
- giá/tồn kho thay đổi qua từng lượt;
- MENU không lộ ID nội bộ/giá vốn/mapping;
- sản phẩm mới được báo và tạo nháp, không bỏ qua âm thầm;
- nguồn lỗi/catalog rỗng/không đọc thấy không xóa hay ghi tồn 0;
- mapping trùng/không hợp lệ bị chặn;
- projection chỉ chạm A:H;
- adapter bọc provider Phase 4.2 tái sử dụng `NormalizedOffer`.

Toàn bộ suite local: **308 passed, 0 failed, 0 error, ~5.8s**; `compileall` sạch.
JUnit: `backups/pytest-phase43f.xml`.

## 8. Lộ trình migration an toàn

1. **Shadow read (đã có code):** chạy `CatalogSyncService` local với snapshot/adapter,
   so sánh store catalog với MENU hiện tại, không ghi. Lượt này chỉ test bằng adapter giả,
   chưa đọc nguồn thật.
2. **Bổ sung adapter thứ ba:** mỗi API mới chỉ cần một adapter + mapping đã duyệt;
   không sửa logic MENU.
3. **Duyệt mapping/content:** nhập `source_key` cho API không có ID, duyệt mapping,
   khóa các trường nội dung người vận hành sở hữu.
4. **Dry-run MENU:** chiếu catalog qua `plan_menu_cells`, kiểm tra A:H, selector
   K38/J39/J40, protected range, merge/formula/dropdown.
5. **Tạo plan v3 Phase 4.3E:** từ nguồn hiện tại, chạy precheck 25 mục, backup
   READY/checksum/restore plan, chỉ apply 8 thao tác khi tất cả PASS.
6. **Live có kiểm soát:** chỉ dùng script `phase43e_live_migration.py --apply
   --confirm` với plan v3, backup mới và freshness mới; dừng ngay nếu lỗi.
7. **Post-check:** 24 sản phẩm/24 ID, AI PRO, SXN-24 = `local_41`, K38 dropdown,
   J39/J40 formulas, layout/warranty không đổi.

## 9. Trạng thái an toàn của lượt này

- `DRY_RUN=true`, `ENABLE_PURCHASE=false`, `ENABLE_TELEGRAM=false`: không đổi.
- Không ghi Google Sheets/Docs.
- Không push GitHub, không deploy Render.
- Không tạo đơn, không bật scheduler.
- Không sửa `.env`, không đổi script migration, không apply plan.
