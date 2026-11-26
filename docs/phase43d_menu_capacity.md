# Phase 4.3D — Vùng mở rộng MENU A31:H35 và dòng SXN-24

Lượt này **chỉ đọc**. Không ghi Google Sheets, không tạo đơn, không push GitHub.
`DRY_RUN=true` được giữ nguyên.

> Phase 4.3E đã lập kế hoạch sửa schema nguồn và migration MENU dựa trên kết quả dưới đây.
> Xem [phase43e_migration.md](phase43e_migration.md). Chưa có lần ghi Google nào.

## 1. Kết quả audit trực tiếp

Đọc bằng Google Sheets API (`menu-baseline` + `menu-audit`), không dựa vào ảnh:

| Hạng mục | Giá trị thật |
| --- | --- |
| Dòng tiêu đề | 6 (`ID ... STATUS`) |
| Sản phẩm SXN | 23 dòng, dòng 8–30 (`SXN-01` … `SXN-23`) |
| Dòng 31–35 | A:H trống; J:M có ghi chú chính sách và merge `J31:M31` … `J35:M35` |
| Dòng 36 | `SXN-24`, B=`Shop Xoài Non`, C/D=`ChatGPT Plus 1 tháng riêng tư - BHF`, E=mô tả dài, F=`203000`, H=`IN STOCK` |
| Panel chi tiết | bắt đầu dòng 37 (`J37:M37`), K38 dropdown, J39/J40 công thức, `J40:M45` merge |
| Dropdown K38 | `ONE_OF_RANGE` → `=MENU!$B$8:$B$30` |
| Công thức J39/J40 | `VLOOKUP(K38; B8:H30; …)` và `VLOOKUP(K38; BAO_GIA_TONG_HOP!C2:E24; …)` |
| Merge toàn tab | 62 |
| Vùng bảo vệ | 2: `A1:H6` và `I1:M55`, đều `warningOnly=true` |
| Conditional format | 8 rule, cùng phủ `H7:H45` (cột STATUS) |
| Banding | không có |
| Fingerprint baseline cũ | vẫn khớp `backups/menu-baseline-phase43b.json` |
| Fingerprint mới (schema 2, gồm protected range) | `f4e9a3afe510bf353ad64f6b71d2e3ee9b26f9a560605b276d5fdf830678dd01` |

`A31:H35` **không** có merge, công thức hay protected range. Chỉ J:M của các dòng này bị merge/khóa.
Vì vậy A31:H35 là vùng A:H trống thật, dùng được cho sản phẩm mà không chạm J:M.

## 2. Nguyên nhân SXN-24 bị lệch

1. Tab `BAO_GIA` đang lệch cột: dòng 1 ghi `ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK | STOCK`,
   nhưng 23 dòng dữ liệu lại có dạng `ID | Shop Xoài Non | <tên sản phẩm> | <tên sản phẩm> | <mô tả dài> | <giá> | <trạng thái>`.
   Tức cột B đang là **tên nguồn**, không phải PRODUCT.
2. `shop_sheet_rows()` trước đây tin hoàn toàn vào tên cột, nên đọc B thành PRODUCT.
   Khi API shop không có dòng giá và MENU được làm mới từ BAO_GIA, dòng đầu tiên bị ghi thành một “sản phẩm” mới:
   `PRODUCT = Shop Xoài Non`, `DESCRIPTION = ChatGPT Plus ...`, `WARRANTY = mô tả dài`.
3. Vì vậy `SXN-24` **không phải sản phẩm thứ 24**. Nó là bản sao lệch cột của `SXN-01`:
   - Tên thật trong C: `ChatGPT Plus 1 tháng riêng tư - BHF`
   - Khớp API: `ProductID=local_1`, `VariantID=local_1`
   - Trùng `SXN-01`; giá `203000` là giá cũ của SXN-01.
4. Sản phẩm thứ 24 thật là `local_41` — `Adobe All Apps 1 Tháng riêng tư - BH7D`,
   giá bán tính theo policy hiện tại `90000`, tồn `7`. Sản phẩm này **chưa có dòng MENU**.
5. `BAO_GIA_TONG_HOP` cũng bị lệch tương tự và hiện có 24 dòng dữ liệu (header + 24), trong khi
   `BAO_GIA` chỉ có 23 dòng dữ liệu (header + 23).

Kết luận: lỗi gốc nằm ở BAO_GIA/BAO_GIA_TONG_HOP, không phải ở tên sản phẩm hay ở dropdown K38.

## 3. Có dùng A31:H35 được không?

**Có, về mặt ô và layout.** Điều kiện kỹ thuật:

- A31:H35 trống, không merge/công thức/protected.
- J31:M35 là panel chính sách riêng; ghi A:H không sửa J:M.
- Conditional format `H7:H45` đã phủ các dòng 31–36 nên STATUS vẫn được tô màu.
- Không cần chèn/xóa dòng, không đổi chiều cao cột/dòng.

**Nhưng chưa dùng được ngay** vì vùng chọn và công thức chỉ đọc tới dòng 30:

- K38: `MENU!$B$8:$B$30`
- J39/J40: `VLOOKUP(... B8:H30 ...)`
- J40 còn tham chiếu `BAO_GIA_TONG_HOP!C2:E24`

Nếu ghi sản phẩm vào dòng 31 mà không mở rộng các vùng trên, sản phẩm sẽ nằm trên MENU nhưng
không xuất hiện trong dropdown và panel chi tiết không tra được. Vì vậy code mới **không ghi im lặng**:
nó dừng và nêu đúng dòng + đúng range cần mở rộng.

## 4. Phương án xử lý dòng 36

| Phương án | Việc phải làm | Ưu / nhược |
| --- | --- | --- |
| A. Sửa tại chỗ dòng 36 | Ghi lại A36:H36 bằng `local_41` (giữ `SXN-24`), mở K38/J39/J40 tới dòng 36 | Giữ đúng số thứ tự; nhưng SXN-24 nằm sau 5 dòng ghi chú A:H trống, nhìn tách khỏi SXN-23 |
| B. Dùng dòng 31 cho sản phẩm 24 | Ghi `local_41` vào A31:H31, mở K38/J39/J40 tới dòng 31 (hoặc 35), xử lý dòng 36 | Danh sách liền mạch; cần quyết định xóa/để trống dòng 36 và cấp ID (SXN-24 hay SXN-25) |
| C. Giữ nguyên, chỉ cảnh báo (hiện tại) | Không ghi gì; audit báo SXN-24 là bản sao lệch cột | An toàn tuyệt đối; sync vẫn dừng tới khi có phê duyệt |

Đề xuất: **C trước, sau đó B** nếu muốn danh sách liền mạch. Chỉ chọn A nếu muốn giữ nguyên vị trí dòng 36.

Nếu chọn B, cần quyết định rõ:

- Cấp cho `local_41` mã `SXN-24` (tái sử dụng ID của dòng lỗi) hay `SXN-25` (bỏ trống ID 24).
- Dòng 36 xử lý ra sao: để nguyên bản sao (xấu), xóa giá trị A:H (không xóa dòng), hay sửa thành sản phẩm khác.
- Không dùng tên làm định danh: phải đối chiếu `ProductID=local_41`, `VariantID=local_41`.

## 5. Thay đổi code trong lượt này

- `menu_layout.py`
  - Baseline schema 2: đọc thêm `protectedRanges`, mở rộng thành `protected_cells` trong A:M.
  - Guard chặn ô A:H nằm trong protected range, kể cả `warningOnly`.
  - `MenuCapacity`: tách vùng A:H khỏi panel J:M, xác định `panel_start_row=37`, `last_writable_row=36`,
    đọc vùng chọn K38/J39/J40 (`B8:B30`).
  - Diff baseline báo drift protected range; baseline cũ chưa có dữ liệu này vẫn so sánh được.
- `combined_quote.py`
  - Ô A:H trống chỉ bị coi là “hàng trống” khi không có merge/công thức/protected trong A:H;
    nội dung J:M không còn tự động khóa A:H.
  - Không dùng hàng trống trước sản phẩm đầu tiên của vùng implicit (tránh ghi vào dòng 7).
  - Sản phẩm mới rơi ngoài vùng chọn K38/J39/J40 thì **dừng với thông điệp nêu dòng và range**,
    không bỏ qua im lặng.
  - `shop_quote_rows`/`nastele_quote_rows` mang thêm `ProductID`, `VariantID` (không ghi vào bảng).
  - `validate_menu_rows` từ chối hai sản phẩm cùng tên nhưng khác ProductID/VariantID.
  - Phát hiện BAO_GIA/BAO_GIA_TONG_HOP lệch cột (cột B là tên nguồn) và dừng trước khi đọc/ghi.
- `menu_audit.py` (mới): audit chỉ đọc, đối chiếu ProductID/VariantID, liệt kê hàng mở rộng,
  dòng legacy ngoài vùng chọn, sản phẩm nguồn còn thiếu.
- `cli.py`: thêm `menu-audit [--json PATH]`, chỉ đọc Google + API shop.
- `sheets_service.py`: `get_sheet_structure` và `list_sheet_meta` trả protected ranges.

## 6. Kiểm thử

`tests/test_phase43d_menu_capacity.py` có 14 ca:

1. Baseline đọc protected range; guard chặn A6/I31/J31 nhưng cho A31/H35.
2. Diff báo drift protected range.
3. Capacity đặt panel dòng 37, A:H dùng tới 36, hàng trống 31–35.
4. Ô protected trong A31 không còn là hàng trống.
5. Sản phẩm 24 dừng với thông điệp nêu dòng 31 + K38 + J39/J40, không ghi ô.
6. Khi mở rộng vùng chọn tới 35, sản phẩm 24 ghi được vào dòng 31 (kế hoạch, không Google).
7. SXN-24 bị quarantine, khớp `local_1/local_1`, trùng SXN-01, lý do lệch cột.
8. Sản phẩm thiếu là `local_41`, gợi ý dòng 31.
9. BAO_GIA lệch cột bị từ chối trước khi đọc/ghi.
10. Trùng tên khác ProductID/VariantID bị từ chối.
11. `shop_quote_rows` giữ ProductID/VariantID.
12. Tên nguồn đã đổi nhẹ (`Grok Premium...`) vẫn khớp theo token + giá; Adobe vẫn thiếu.
13. `expire_missing` không đụng dòng legacy 36 ngoài vùng chọn.
14. `CombinedQuoteWriter` vẫn dùng capacity khi được truyền `layout_guard` (đường `menu_sync`).

Toàn bộ: `pytest` **268 passed**, `compileall` sạch.

Phase 4.3E thêm 17 ca migration, nâng tổng lên **285 passed**; xem
[phase43e_migration.md](phase43e_migration.md).

## 7. Cần duyệt trước khi ghi Google

1. **Sửa BAO_GIA và BAO_GIA_TONG_HOP** theo đúng header 7 cột
   (`ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK`), bỏ cột STOCK trùng ở H.
   Đây là việc bắt buộc trước mọi sync MENU.
2. Chọn phương án dòng 36 (A/B/C) và cấp ID cho `local_41`.
3. Nếu chọn A hoặc B, duyệt chính xác các thay đổi Google:
   - `K38` validation: `MENU!$B$8:$B$30` → `$B$8:$B$31` (hoặc `$B$35`)
   - `J39`, `J40`: `B8:H30` → `B8:H31` (hoặc `H35`)
   - `J40`: `BAO_GIA_TONG_HOP!C2:E24` → `C2:E25` sau khi BAO_GIA_TONG_HOP có 24 sản phẩm
   - Nếu chọn B: ghi A31:H31 cho `local_41`; xử lý dòng 36 (giữ/clear) sau khi duyệt.
4. Sau khi duyệt, chạy lại `menu-baseline --out backups/menu-baseline-phase43d.json`
   và `menu-baseline --compare` để chốt baseline mới. Không tự đổi baseline trong lượt này.
5. Chỉ sau đó mới bật `DRY_RUN=false` cho `sync-menu`.

## 8. Lệnh audit

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:DRY_RUN = "true"
python -m shop_integration menu-baseline --compare backups/menu-baseline-phase43b.json
python -m shop_integration menu-audit --json backups/menu-audit-phase43d.json
```

`menu-audit` không ghi Google Sheets. File JSON chỉ là báo cáo local.

## 9. Tự mở rộng vùng sản phẩm (bỏ giới hạn 23/26/30 dòng)

`src/shop_integration/menu_expand.py` chèn thêm dòng rỗng ngay sau dòng sản phẩm
cuối cùng khi nguồn có nhiều sản phẩm hơn số ô của vùng chọn. Không cần sửa tay
`K38`, `J39`, `J40` hay hàng trống: mọi vùng phụ thuộc dòng cuối được căng lại
trong cùng một `batchUpdate`.

Nguyên tắc:

- Vùng sản phẩm = `product_start_row` … dòng cuối dropdown `K` của MENU.
  Số ô cần có = sản phẩm API + số dòng nhập tay trên `NASTELEID`
  (`menu_sync._menu_zone_target`).
- Chèn `MENU_EXPAND_STEP` dòng một lần (mặc định 10), tối đa `MENU_EXPAND_MAX_ROWS`
  (mặc định 40 mỗi lượt); thiếu nhiều hơn thì lượt sync sau chèn tiếp và ghi chú
  lại số chỗ còn thiếu.
- Điểm chèn là ngay sau dòng cuối vùng chọn, và không vượt quá `panel_start_row - 1`
  (panel chi tiết `J:M` dời xuống nguyên khối, nội dung không bị sửa).
- Requests theo thứ tự: `insertDimension` → `setDataValidation` cho `K` (nếu vùng
  chọn đổi) → `updateCells` cho công thức `J` (chỉ ô có `B…:…`) → định dạng điều
  kiện cột `H` → `updateProtectedRange` cho vùng bảo vệ nằm dưới điểm chèn.
- Không gộp/bỏ gộp ô (preflight vẫn yêu cầu 62 merge), không ghi giá trị ô sản phẩm,
  không đụng `J:M`.

Đọc/ghi baseline khi vùng đã dài ra:

- `menu_expand.menu_selector_span` đọc vùng chọn từ chính rule của dropdown `K`.
  `menu_preflight` dùng hàm này, nên một tham chiếu chéo sheet kiểu
  `BAO_GIA_TONG_HOP!B2:E26` trong `J40` không còn kéo vùng chọn lên dòng 2.
- `menu_expansion_shift` / `menu_preflight._shift_rows` nhận ra "baseline cũ + N
  dòng" và trả về `N`, nên preflight chấp nhận layout đã tự mở rộng thay vì báo
  lệch. Lệch thật (công thức/merge/bảo vệ khác) vẫn bị chặn.

Cờ môi trường: `MENU_AUTO_EXPAND` (mặc định `true`), `MENU_EXPAND_STEP` (1–50),
`MENU_EXPAND_MAX_ROWS` (1–200). Xem [setup_vi.md](setup_vi.md).

Sau lần mở rộng thật đầu tiên, baseline cũ vẫn dùng được: preflight và
`_menu_layout_guard` nhận ra phép dịch dòng và chỉ ghi chú. Muốn chốt mốc mới thì
chạy `python -m shop_integration menu-baseline --out data/menu-baseline.json`.

`tests/test_phase43d_menu_capacity.py` thêm 10 ca (file có 29 ca): chạy thử không ghi,
chèn dòng + căng `K`/`J`, không đổi merge, no-op khi còn chỗ, tắt bằng
`MENU_AUTO_EXPAND=false`, trần `MENU_EXPAND_MAX_ROWS`, dời protected range và định
dạng điều kiện, baseline dịch đúng sau khi chèn, phát hiện lệch thật, preflight đọc
vùng chọn từ dropdown thay vì `B2:E26`, và `_menu_zone_target` cộng dòng nhập tay.

Toàn bộ: `pytest` **350 passed** (14 ca gốc Phase 4.3D vẫn xanh).

