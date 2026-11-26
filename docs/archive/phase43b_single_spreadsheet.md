# Phase 4.3B — Một Google Spreadsheet, cấu hình sạch, code gọn hơn

Ngày: 2026-11-25. Lượt này **chỉ đọc Google**, không ghi Sheets/Docs, không tạo đơn, không bật lịch.

## 1. Spreadsheet chính thức

`GOOGLE_SPREADSHEET_ID=196goR1lDZN6zJfoC3a7vOVyo5u9o-pvxXpc3mnGrGSY`

Đây là đích ghi duy nhất cho: catalog Shop Xoài Non, nguồn Nastele, ghép sản phẩm, định giá, `MENU`, `BAO_GIA`, `BAO_GIA_TONG_HOP`. Mã nguồn không hardcode ID; ID chỉ nằm trong `.env` (và `.env.example`).

`GOOGLE_DOCUMENT_ID` vẫn giữ cho Google Docs. Không đổi file Docs.

## 2. Audit phụ thuộc biến môi trường (trước Phase 4.3B)

| Biến | Nơi dùng trước đây | Sau Phase 4.3B |
| --- | --- | --- |
| `GOOGLE_SPREADSHEET_ID` | `config.spreadsheet_id`, `sync_service`, `menu_sync` (nhánh catalog), `cli`, workflow, `render.yaml` | Vẫn là biến duy nhất. `Settings.spreadsheet_id` là nguồn duy nhất cho mọi writer. |
| `NASTELE_SPREADSHEET_ID` | `config`, `cli._source_command`, `source_sync` (báo cáo + trạng thái), `nastele_sheet`, workflow `sync-menu`/`sync-sources`, `render.yaml`, 5 tài liệu, 2 test | **Bỏ field**. Nếu còn trong môi trường: bị bỏ qua, `config-check` in tên biến. |
| `LAST_SPREADSHEET_ID` | `config`, `menu_sync`, `sync_service._update_combined_quote`, `combined_quote` (bảng layout + lỗi), workflow, `render.yaml`, 6 tài liệu, 3 test | **Bỏ field**. Cùng cơ chế cảnh báo như trên. |
| `GOOGLE_DOCUMENT_ID` | `config`, `cli`, `backup_service`, report Docs | Giữ nguyên. |
| `GOOGLE_CREDENTIALS_FILE` | `config`, `google_auth`, `cli`, `menu_sync`, workflow | Giữ nguyên; chỉ còn một chỗ mở credentials qua `workspace.open_workspace`. |

## 3. Env cleanup

- `.env` được sao lưu trước khi sửa: `backups/env-before-phase43b-*.env`.
- Đổi tên: `DEFAULT_PURCHASE_QUANTITY` → `COMPARISON_QUANTITY`; `PREFERRED_SUPPLIER_ON_TIE` → `PREFERRED_SUPPLIER_ID` (giá trị cũ `shopxoainon` được map về `shop_xoai_non`).
- Xóa: `NASTELE_SPREADSHEET_ID`. `LAST_SPREADSHEET_ID` vốn không còn trong `.env`.
- Bỏ 2 dòng trùng `ENABLE_PURCHASE`, `ENABLE_TELEGRAM`.
- Giữ `DRY_RUN=true`, `ENABLE_PURCHASE=false`, `ENABLE_TELEGRAM=false`.
- Không in, không đổi giá trị API key. Kiểm tra chỉ in `True/False` cho việc key có mặt.
- Lệnh mới `python -m shop_integration config-check` in cấu hình hiệu lực, biến cũ bị bỏ qua và danh sách tab LEGACY. Không gọi mạng.

## 4. Kết quả đọc thật (read-only)

| Tab | Có | Dòng dữ liệu | Cột |
| --- | --- | --- | --- |
| `MENU` | có | 39 | 12 |
| `NASTELEID` | có | 1 | 7 |
| `BAO_GIA` | có | 23 | 8 |
| `BAO_GIA_TONG_HOP` | có | 24 | 7 |
| `SO_SANH_NGUON`, `BANG_GIA_TOI_UU`, `CAN_GHEP_SAN_PHAM` | chưa có | — | — |
| `SAN_PHAM`, `BIEN_THE`, `BANG_GIA_BAN`, `LICH_SU_GIA`, `NHAT_KY_DONG_BO`, `CAU_HINH` | chưa có | — | — |
| `NASTELE_*` (5 tab) | chưa có | — | — |

`MENU`: dòng tiêu đề ở index 5 (dòng 6), 62 vùng merge, 29 ô ở cột J–M có nội dung. Vì vậy không được ghi đè toàn bộ tab và không được đụng cột I–M.



## 5. Tab LEGACY (đánh dấu, không xóa)

`sheet_layout.LEGACY_TAB_NOTES` đánh dấu các tab cũ trùng mục đích. Không xóa, không ẩn tự động trong phase này.

| Tab LEGACY | Vì sao | Tab chính thức |
| --- | --- | --- |
| `BANG_GIA_TOI_UU` | Bảng giá khách của nhánh nguồn | `BAO_GIA_TONG_HOP` |
| `NASTELE_SAN_PHAM` | Danh mục Nastele chi tiết | `NASTELEID` (nhập tay) |
| `NASTELE_GIA_SI` | Bậc giá sỉ, chỉ tham chiếu | `SO_SANH_NGUON` |
| `NASTELE_LICH_SU_GIA` | Lịch sử giá Nastele | `LICH_SU_GIA` |
| `NASTELE_NHAT_KY` | Nhật ký Nastele | `NHAT_KY_DONG_BO` |
| `NASTELE_CAU_HINH` | Cấu hình Nastele | `CAU_HINH` |

Trước khi ẩn/hợp nhất: đối chiếu công thức, named range, protected range và người đang dùng. Chưa làm trong phase này.

## 6. Giảm trùng lặp code

| Module mới | Thay cho | Tác dụng |
| --- | --- | --- |
| `sheet_io.py` | `_read`/`_ensure` lặp ở `sheets_service`, `source_sync`, `nastele_sheet`; `_a1` ở `menu_sync` | Một chỗ đọc tab, tạo tab, đổi cột sang A1. |
| `sheet_format.py` | `build_format_requests` trong `nastele_sheet` + 2 bản `_format_*` | Một bộ request định dạng dùng cho catalog, nguồn và Nastele. `nastele_sheet` vẫn re-export tên cũ. |
| `workspace.py` | 4 chỗ tự `load_service_account_credentials` + `GoogleSheetsGateway` | Một credentials, một gateway Sheets/Docs cho mỗi lệnh. |

`menu_sync` chỉ còn một lượt làm sạch mô tả cho tất cả tab (trước đây đọc `SAN_PHAM`/`BIEN_THE` hai lần khi hai ID khác nhau). `menu_sync` dùng chung `menu_header_row` với `combined_quote`.

Public API, tên hàm và hành vi CLI giữ nguyên, trừ: `NASTELE_SPREADSHEET_ID`/`LAST_SPREADSHEET_ID` bị bỏ và lỗi thiếu ID đổi thành `GOOGLE_SPREADSHEET_ID`.

## 7. An toàn ghi (không thay đổi trong lượt này)

- `DRY_RUN=true`. Mọi lệnh đã chạy đều dry-run, không ghi ô.
- `scheduled_menu_live()` mặc định `false`: Web/Render chỉ lập kế hoạch trừ khi đặt `ENABLE_SCHEDULED_MENU_LIVE=true`. `render.yaml` đặt `false`. Workflow `sync-menu` vẫn đặt `true` khi được phép chạy.
- `backup-readiness`: **FAIL / NOT_READY**. Snapshot local không đủ cho scheduled live sync.
- `restore-plan`: **chặn bởi Docs** (văn bản hiện tại khác snapshot). Snapshot mới nhất thuộc spreadsheet catalog cũ `1u2AZ…`; file chính thức không có tab catalog nên lệnh không đề xuất ghi tab nào. Không chạy `restore`.
- Không tạo đơn, không gọi endpoint `orders`, không bật Telegram, không đổi quyền Google, không push GitHub, không deploy Render.

## 8. Lệnh đã chạy

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"
python -m shop_integration config-check
python -m shop_integration sources
python -m shop_integration sync-menu
python -m shop_integration backup-readiness
python -m shop_integration restore-plan
python -m pytest
```

Kết quả: `sources` rc=0 (Shop 24 nguồn, Nastele 0 nguồn `NASTELE_CATALOG_EMPTY`, 13 sản phẩm được chọn nguồn). `sync-menu` rc=0, `live=false`, `menu=22`, `cells=300` là kế hoạch, không ghi. `backup-readiness` FAIL. `restore-plan` rc=1 do xung đột Docs.

Vì catalog/source tab chưa có trong file chính thức, lần ghi thật đầu tiên sẽ tạo tab. Việc đó vẫn **BLOCKED** cho tới khi backup đạt và có xác nhận.

## 9. Khóa layout MENU (Phase 4.3B)

Lượt này chỉ đọc MENU, không ghi Google Sheets/Docs, không gọi API ghi.

### 9.1 Baseline thật (chỉ đọc)

Đo bằng `python -m shop_integration menu-baseline`:

| Hạng mục | Giá trị |
| --- | --- |
| Dòng dữ liệu / cột | 40 / 12 |
| Dòng tiêu đề | 6 |
| Vùng gộp | 62 |
| Công thức | 3 (`G5` `=NOW()`, `J39`, `J40`) |
| Dropdown | 1 (`K38`, `ONE_OF_RANGE` → `MENU!$B$8:$B$30`) |
| Rule định dạng điều kiện | 8 |
| Khổ cột A–M | có, 13 cột |
| Chiều cao dòng tùy chỉnh | 0 |
| Ô khóa do merge/công thức | 265 |
| Fingerprint | `b5bfba37ce591afd04cc45258c6dd2821e92256dd643d077ba4bbdcb2520a06a` |

Baseline lưu tại `backups/menu-baseline-phase43b.json` (file local, không phải nguồn sự thật).

Phase 4.3D dùng schema 2: `menu-baseline` hiện báo thêm 2 protected range (`A1:H6`, `I1:M55`),
tổng **332 ô khóa**, fingerprint `f4e9a3afe510bf353ad64f6b71d2e3ee9b26f9a560605b276d5fdf830678dd01`.
Baseline schema 1 ở trên vẫn so sánh được (bỏ qua phần protected range chưa được chụp).

### 9.2 Whitelist ghi

`src/shop_integration/menu_layout.py` định nghĩa:

- Chỉ ghi **giá trị** ô `MENU` cột **A–H**.
- Cột **I** là khoảng đệm, cột **J–M** là chính sách bảo hành: mọi ô J–M bị từ chối.
- Không ghi dòng tiêu đề, không ghi ngoài `data_rows` của lần đọc, không `append` dòng.
- Ô nằm trong vùng gộp hoặc đang có công thức bị từ chối.
- Không gửi bất kỳ `batchUpdate` nào chạm `sheetId` của MENU: merge, unmerge, `repeatCell`, màu, border, `setDataValidation`, conditional format, banding, filter, dimension (khổ cột/dòng).
- Không ghi đè toàn bộ tab và không format MENU.

`LayoutLockedGateway` bọc gateway trong `menu_sync` để chặn cả lớp ghi giá trị lẫn `batchUpdate` trước khi tới Google.

### 9.3 Kiểm tra trước khi ghi

`menu_sync` đọc cấu trúc MENU mỗi lượt (`get_sheet_structure`), rồi:

1. Dựng guard từ merge/công thức/dropdown hiện có và giới hạn dòng theo dữ liệu thật.
2. Dòng tiêu đề A–H phải đúng `ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK | STATUS`; lệch thứ tự hoặc tên cột thì dừng.
3. Kiểm tra dữ liệu nguồn: `PRODUCT` không trống, `PRICE` là số dương, `STOCK` hợp lệ, không trùng tên trong cùng nguồn.
4. Nếu đặt `MENU_BASELINE_FILE`, so với baseline đã lưu; lệch merge/công thức/dropdown/khổ cột/dòng tiêu đề thì dừng, không ghi ô nào.
5. Sản phẩm shop vượt số hàng trống của đúng vùng danh mục thì dừng toàn bộ lượt ghi để không đè layout. Dòng NASTELEID nhập tay vẫn giữ hành vi cũ: bỏ qua kèm ghi chú.

### 9.4 Trạng thái hiện tại (read-only)

`sync-menu` dry-run đọc thật: shop 24 dòng, MENU có 23 sản phẩm đúng (dòng 8–30), 5 hàng A:H trống
(dòng 31–35) nhưng nằm ngoài dropdown K38 (`B8:B30`) và công thức J39/J40 (`B8:H30`).
Dòng 36 là `SXN-24` lệch cột, không phải sản phẩm thứ 24.

Phase 4.3E đã lập kế hoạch migration: dựng lại `BAO_GIA`/`BAO_GIA_TONG_HOP` từ API
(ProductID/VariantID), gán `local_41` cho `SXN-24` tại dòng 31, xóa giá trị legacy dòng 36,
mở `K38` tới `B8:B35`, `J39/J40` tới dòng 35 và `J40` đọc `BAO_GIA_TONG_HOP!B2:E26`.
Lượt 4.3E vẫn `DRY_RUN=true`, chưa ghi Google. Xem
[`phase43e_migration.md`](phase43e_migration.md).

### 9.5 Test

`tests/test_phase43b_menu_layout.py` có 13 ca: baseline/roundtrip/fingerprint, chặn I/J–M, chặn ô merge/công thức, chặn `batchUpdate` MENU, chặn append, overflow không ghi, dừng khi dòng tiêu đề lệch, giữ J:M + merge + header, phát hiện drift, kiểm tra dữ liệu nguồn, `menu_sync` đọc cấu trúc và dừng khi lệch baseline.

Phase 4.3D thêm `tests/test_phase43d_menu_capacity.py` 14 ca cho A31:H35, protected range,
dòng SXN-24, ProductID/VariantID và BAO_GIA lệch cột.

Phase 4.3E thêm `tests/test_phase43e_migration.py` 17 ca cho contract nguồn, identity,
A31:H31, legacy dòng 36, dropdown/công thức, guard, backup, restore coverage,
pricing, bảo toàn AI PRO và idempotency.

Kết quả: `pytest` **285 passed**.

### 9.6 Lệnh mới

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"
python -m shop_integration menu-baseline --out backups/menu-baseline-phase43b.json
python -m shop_integration menu-baseline --compare backups/menu-baseline-phase43b.json
python -m shop_integration menu-migrate --json backups/menu-migration-phase43e.json
python -m shop_integration menu-audit --json backups/menu-audit-phase43d.json
```

`menu-baseline` chỉ đọc Google. `--compare` trả mã 1 khi layout lệch baseline. `menu-audit` chỉ đọc
Google + API shop, không ghi.
