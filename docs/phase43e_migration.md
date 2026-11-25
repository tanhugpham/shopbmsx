# Phase 4.3E — Migration dữ liệu nguồn và MENU (chỉ lập kế hoạch)

Lượt này **không ghi Google Sheets/Docs**. `DRY_RUN=true`, không tạo đơn, không push
GitHub, không deploy Render, không bật scheduler. Toàn bộ kết quả dưới đây là kế hoạch
đã được kiểm tra bằng code và audit trực tiếp qua Google Sheets API (chỉ đọc).

## 1. Contract của từng tab nguồn

Schema chuẩn được suy ra từ contract riêng, **không dùng một phép dồn cột chung**.

| Tab | Contract | Dữ liệu thật đang có | Lưới đích |
| --- | --- | --- | --- |
| `BAO_GIA` | Chỉ Shop Xoài Non, tool ghi từ API. 7 cột `ID | PRODUCT | DESCRIPTION | DURATION | WARRANTY | PRICE | STOCK`. | Header 8 cột (H trùng `STOCK`), 23 dòng dữ liệu lệch: B = `Shop Xoài Non`, C/D = tên sản phẩm, E = mô tả dài, F = giá, G = tồn. | `A1:G25` (header + 24 sản phẩm), xóa giá trị `H1:H25`. Không xóa cột H. |
| `BAO_GIA_TONG_HOP` | Xếp chồng NASTELEID trước, Shop Xoài Non sau. Cùng 7 cột. | Header 7 cột đúng, 24 dòng dữ liệu: dòng 2 là `AI PRO` nhập tay đúng, 23 dòng shop bị lệch như trên. | `A1:G26` (header + 1 tay + 24 shop), ID đánh lại 1..25. |

Điểm quan trọng:

- `BAO_GIA` là tab tool ghi từ API, nên migration **dựng lại toàn bộ** 24 dòng từ API hiện
  tại. Vì vậy `PRODUCT`, `DESCRIPTION`, `DURATION`, `WARRANTY`, `PRICE`, `STOCK` của cả
  24 sản phẩm sẽ theo API, không chỉ đổi vị trí cột. Nếu muốn giữ nguyên giá/tồn cũ thì
  phải duyệt một phương án khác trước khi ghi.
- Trong 136 ô lệch có cả thay đổi giá/tồn hiện tại của API so với bản cũ, không chỉ lỗi
  lệch cột. Đây là lý do migration phải chờ duyệt.
- `local_41` là sản phẩm thứ **11** trong thứ tự API hiện tại, nhưng là sản phẩm thứ
  **24** còn thiếu trên MENU. Không dùng `ID` API làm mã MENU.
- Mọi dòng shop dùng để ghi đều mang `ProductID`/`VariantID`. Migration **không** đọc
  tên sản phẩm rồi tự dồn cột; nó dựng lại hai tab từ chính các dòng API đã có identity.
- Dòng legacy không khớp identity bị báo riêng, không được dùng để chuyển dữ liệu.

## 2. Kế hoạch sửa `BAO_GIA`

1. Kiểm tra 24 dòng API: đủ `ProductID`, `VariantID`, `PRICE`; không trùng identity;
   không có hai identity khác nhau dùng cùng tên.
2. Ghi `'BAO_GIA'!A1:G25` bằng lưới 7 cột dựng từ API (header + 24 sản phẩm).
3. Xóa giá trị `'BAO_GIA'!H1:H25` để bỏ cột `STOCK` trùng. Không xóa cột, không đổi
   độ rộng cột.
4. Không xóa dòng, không chèn dòng. Nếu có dữ liệu ngoài dòng 25 trong A:G, migration
   dừng và yêu cầu duyệt tay.

## 3. Kế hoạch sửa `BAO_GIA_TONG_HOP`

1. Giữ dòng 2 `AI PRO` nhập tay nguyên giá trị.
2. Ghi 24 dòng shop đã sửa vào dòng 3–26, ID đánh lại 1..25.
3. Ghi `'BAO_GIA_TONG_HOP'!A1:G26`.
4. Không xóa/chèn dòng; không đụng cột ngoài A:G.

## 4. Kế hoạch MENU

| Ô / vùng | Hiện tại | Sau migration |
| --- | --- | --- |
| `A8:H30` | `SXN-01` … `SXN-23` | Giữ nguyên, không ghi |
| `A31:H31` | Trống | `SXN-24 | Adobe All Apps 1 Tháng riêng tư - BH7D | … | 1 Tháng | BH7D | 90000 | 7 | IN STOCK` |
| `A36:H36` | `SXN-24 | Shop Xoài Non | ChatGPT Plus 1 tháng riêng tư - BHF | …` | Xóa giá trị A:H; **không xóa dòng**, không đụng I:M |
| `K38` | `=MENU!$B$8:$B$30` | `=MENU!$B$8:$B$35` |
| `J39` | `VLOOKUP(... B8:H30 ...)` | `VLOOKUP(... B8:H35 ...)` |
| `J40` | `VLOOKUP(K38; BAO_GIA_TONG_HOP!C2:E24; 3; FALSE)` và `B8:C30` | `VLOOKUP(K38; BAO_GIA_TONG_HOP!B2:E26; 4; FALSE)` và `B8:C35` |

`J40` phải đổi cả cột tra cứu: sau khi sửa schema, cột B là `PRODUCT`, cột C là
`DESCRIPTION`. Giữ `C2:E24` sẽ không còn tra được sản phẩm.

Không thay đổi:

- Cột I và các cột J–M ngoài đúng 3 ô `K38`, `J39`, `J40`.
- Merge `J31:M35`, `J37:M37`, `J39:M45` và 62 vùng gộp khác.
- Chiều cao dòng, độ rộng cột, conditional format `H7:H45`, protected range
  `A1:H6`, `I1:M55` (warningOnly).
- Nội dung chính sách bảo hành J:M.

## 5. Backup readiness

Lệnh dry-run tạo snapshot local mới, không dùng lại snapshot cũ:

```
backups/migration-<thời-điểm>/snapshot.json
backups/migration-<thời-điểm>/structures.json
backups/migration-<thời-điểm>/manifest.json
```

| Hạng mục | Kết quả |
| --- | --- |
| Trạng thái | `READY` |
| Dữ liệu | Values + formulas của tất cả tab |
| Metadata | Merge, protected range, data validation, công thức, độ rộng cột, chiều cao dòng, số conditional format của 4 tab migration |
| Integrity | SHA-256 từng file, đọc lại và so khớp; manifest ghi `reused_snapshot=false` |
| Bí mật | Từ chối lưu nếu thấy `PRIVATE KEY` hoặc secret cấu hình |
| Restore plan | Có một dòng cho mỗi thao tác đã lập; không tự động restore |
| Giới hạn | Không phải bản sao style/ảnh/comment; `complete_fidelity=false` |

Lần chạy cuối: `backups/migration-20261125T123504+0700/`,
snapshot checksum `cc32cabd…c86e`, structures checksum `85e1fce4…340d`,
9 dòng restore plan cho 8 thao tác + 1 dòng giới hạn.

Plan 11:56 có 135 ô lệch. Dry-run xác minh lại lúc 12:35 phát hiện nguồn API đã đổi
thành 136 ô lệch (vẫn đúng 8 thao tác), nên plan và snapshot 12:35 là bản duy nhất
được dùng để xin duyệt. Baseline không đổi.

Nếu thiếu tab, thiếu khóa metadata, checksum lệch, snapshot cũ xung đột baseline, hoặc
backup không đọc lại được, migration chuyển `BLOCKED` và không ghi.

## 6. Kết quả dry-run trực tiếp

Lệnh:

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:DRY_RUN = "true"
python -m shop_integration menu-migrate --json backups/menu-migration-phase43e.json
```

Kết quả:

- `status=PLANNED`, `dry_run=true`, `applied=false`, `operations=8`.
- `BAO_GIA`: 24 → 25 dòng, 136 ô lệch, 2 thao tác (ghi A1:G25, xóa H1:H25).
- `BAO_GIA_TONG_HOP`: 25 → 26 dòng, 136 ô lệch, 1 thao tác (ghi A1:G26).
- `MENU`: `SXN-24` gán cho `local_41/local_41` tại dòng 31; legacy dòng 36 trùng
  `SXN-01`; K38 mở tới `B8:B35`; J39/J40 mở tới dòng 35; J40 đọc
  `BAO_GIA_TONG_HOP!B2:E26`.
- Sau migration: **24 sản phẩm, 24 ID duy nhất**.
- Backup: `READY`.
- Không có thao tác insert/delete row, không có format.

Xác minh pre-live (chỉ đọc, `DRY_RUN=true`):

- Snapshot `12:35` phủ đúng 8 range restore, gồm values, formulas J39/J40 và dropdown K38;
  không có range ngoài phạm vi migration.
- `BAO_GIA`/`BAO_GIA_TONG_HOP` chứa giá bán từ pricing engine, không lẫn giá vốn.
- `MENU` ghi `local_41` bằng giá bán `90000`, không dùng giá vốn `69000`; `SXN-24`
  không phải `local_1`.
- Dòng `AI PRO` nhập tay giữ nguyên qua migration và lần lập sync kế tiếp.
- Không ghi J:M ngoài `K38`, `J39`, `J40`; merge, dropdown, protected range, kích thước
  cột/dòng không đổi so với snapshot `11:56`.
- API hai lần đọc cách nhau 8 giây cho cùng SHA-256:
  `50f581534de30b216dc743983ee510e3d84841a555890efeb2a5ec03da1a0f09`
  (`backups/phase43e-source-freshness.json`).
- Plan `11:56` không còn dùng để ghi vì nguồn đã đổi; baseline `phase43b` giữ nguyên.

## 7. Kiểm thử

`tests/test_phase43e_migration.py` có 17 ca:

1. Hai tab dùng hai contract khác nhau; lưới đích đúng 7 cột, thứ tự ID đúng.
2. `local_41` nằm đúng trong lưới đích với PRODUCT/DESCRIPTION/DURATION/WARRANTY/PRICE/STOCK.
3. `ProductID`/`VariantID` bắt buộc; thiếu hoặc trùng bị từ chối.
4. Chẩn đoán nguồn chỉ ra đúng `local_41` là identity duy nhất còn thiếu.
5. `SXN-24` gán cho `local_41` tại `A31:H31`.
6. Chỉ xóa A36:H36 sau khi xác minh trùng `SXN-01`; không xóa dòng, không đụng I:M.
7. `K38` mở tới `B8:B35`; `J39` dùng `B8:H35`; `J40` dùng `B8:C35` và
   `BAO_GIA_TONG_HOP!B2:E26`.
8. Sau migration có 24 sản phẩm và 24 ID duy nhất.
9. Không ghi cột I–M ngoài `K38`, `J39`, `J40`.
10. Guard từ chối ô ngoài allowlist, thao tác format và validation ngoài K38.
11. Migration lặp lại là idempotent: lần hai `UP_TO_DATE`, 0 thao tác.
12. Backup READY có checksum, manifest, restore plan; mỗi thao tác có dòng restore.
13. Backup BLOCKED khi thiếu tab bắt buộc.
14. Target row không trống thì dừng; không có thao tác insert/delete; không trùng ID.
15. Restore từ snapshot phủ đúng 8 range, gồm values, formulas J39/J40 và dropdown K38;
    không có range ngoài phạm vi migration.
16. PRICE trên BAO_GIA/BAO_GIA_TONG_HOP là giá bán từ pricing engine, không phải giá vốn.
17. Dòng AI PRO nhập tay giữ nguyên qua migration và lần lập sync kế tiếp; `SXN-24`
    là `local_41`, không phải `local_1`.

Kết quả toàn bộ: `pytest` **285 passed**, `compileall` sạch.

## 8. Thay đổi code

- `src/shop_integration/menu_migration.py` (mới)
  - `SourceContract` riêng cho `BAO_GIA` và `BAO_GIA_TONG_HOP`.
  - `validate_source_identities`: bắt buộc ProductID/VariantID, chống trùng identity/tên.
  - `plan_source_repairs`: dựng lại nguồn từ API, chẩn đoán lệch cột, mất/trùng identity.
  - `plan_menu_migration`: gán `SXN-24`, xóa legacy, mở dropdown/công thức, kiểm tra
    24 identity và 24 ID sau migration.
  - `MigrationGuard`: allowlist tuyệt đối; từ chối format, insert/delete, ô ngoài phạm vi.
  - `create_migration_backup`: snapshot local mới + checksum + restore plan.
  - `run_migration`: dry-run mặc định; chỉ ghi khi `live=True` và backup READY.
- `src/shop_integration/cli.py`: thêm `menu-migrate` với `--json`, `--baseline`,
  `--backup-dir`, `--apply`, `--confirm`.
- `tests/test_phase43e_migration.py` (mới): 17 ca.
- `docs/phase43d_menu_capacity.md`, `docs/phase43b_single_spreadsheet.md`,
  `docs/bao_gia_tong_hop.md`: trỏ sang Phase 4.3E và cập nhật số test.

## 9. Hành động ghi thật đang chờ duyệt

Thứ tự bắt buộc sau khi người vận hành duyệt:

1. `DRY_RUN=false` và chạy `menu-migrate --apply --confirm` (hoặc thực hiện đúng 8 thao tác
   dưới đây bằng tay):
   - `'BAO_GIA'!A1:G25` ← lưới 7 cột từ API.
   - `'BAO_GIA'!H1:H25` ← rỗng.
   - `'BAO_GIA_TONG_HOP'!A1:G26` ← 1 dòng tay + 24 dòng shop.
   - `'MENU'!A31:H31` ← `SXN-24` / Adobe.
   - `'MENU'!A36:H36` ← rỗng.
   - `'MENU'!K38` data validation `ONE_OF_RANGE` = `=MENU!$B$8:$B$35`.
   - `'MENU'!J39` công thức mới dùng `B8:H35`.
   - `'MENU'!J40` công thức mới dùng `BAO_GIA_TONG_HOP!B2:E26` và `B8:C35`.
2. Chạy lại `menu-audit` và `menu-baseline --compare backups/menu-baseline-phase43b.json`.
   Baseline cũ sẽ **lệch có chủ đích** ở K38/J39/J40; chỉ sau khi xác minh mới chạy
   `menu-baseline --out backups/menu-baseline-phase43e.json` để chốt baseline mới.
3. Chạy `sync-menu` dry-run, xác nhận `menu=24`, không còn cảnh báo lệch cột.
4. Chỉ sau đó mới bật `DRY_RUN=false` cho `sync-menu`.

Cần duyệt rõ:

- Đồng ý dựng lại `BAO_GIA`/`BAO_GIA_TONG_HOP` từ API và xóa header H trùng.
- Đồng ý dùng `SXN-24` cho `local_41` và xóa giá trị dòng 36 (không xóa dòng).
- Đồng ý đổi `J40` sang `B2:E26` / `B8:C35` (bắt buộc vì schema đổi).
- Đồng ý tạo baseline mới sau migration.

## 10. Lệnh kiểm tra

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:DRY_RUN = "true"
python -m shop_integration menu-migrate --json backups/menu-migration-phase43e.json
python -m shop_integration menu-audit --json backups/menu-audit-phase43e.json
python -m pytest -q
```
