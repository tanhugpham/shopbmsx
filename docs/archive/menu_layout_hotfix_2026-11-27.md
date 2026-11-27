# Hotfix layout MENU — 2026-11-27

## 1. Tóm tắt

Sáng 2026-11-27, tab `MENU` bị lệch layout so với `data/menu-baseline.json`:
dòng tiêu đề `A8:H8` bị nhân bản, panel chính sách `J:M` bị cắt, mất dropdown `K38`
và hai công thức `J39/J40`, footer dời xuống, khổ cột `A/B` bị auto-fit. Vì vậy
`sync-menu-preflight` FAIL và mọi lượt `sync-menu` tiếp theo dừng trước khi ghi
(không có dữ liệu nào bị ghi đè trong lúc lệch).

Đã khôi phục: 62 vùng gộp, dropdown chọn sản phẩm, hai công thức panel, vùng bảo vệ,
khổ cột, panel `J:M`; 35 sản phẩm và giá/tồn kho giữ nguyên. Preflight hiện `PASS`.

## 2. Mốc thời gian và bằng chứng

| Thời điểm | Dữ kiện |
| --- | --- |
| 08:20 → 08:35 | 4 lượt `sync-menu` trên GitHub Actions ghi bình thường. Snapshot `_MENU_SYNC_BACKUP` lưu 28 → 35 sản phẩm, `MENU!A8` vẫn là `SXN-01` (chưa có dòng tiêu đề lặp) |
| 08:35:56 | Snapshot bền cuối trước sự cố: 35 sản phẩm, `A8 = SXN-01`, panel `J:M` đủ |
| 09:43 | Lần đọc đầu tiên phát hiện lệch: 54 vùng gộp (baseline 62), `A8:H8 = ID…STATUS`, `J:M` mất khối `📋 CHI TIẾT MÔ TẢ SẢN PHẨM`/`🔍 Chọn SP`, khổ cột `A 75→83`, `B 230→300` |
| 09:59 | `sync-menu-preflight` FAIL: `MENU phải giữ 62 vùng gộp… Baseline 62, hiện tại 54` |

Ba bằng chứng quyết định nguyên nhân **không** phải pipeline:

1. `spreadsheets.get` trả `sheets[0].tables = [{name: "Bảng_1", range: A8:H55}]` —
   vùng `A8:H55` đã bị chuyển thành **Google Sheets Table** (Bảng). Tính năng này tự
   thêm dòng tiêu đề, auto-fit khổ cột và bật bộ lọc/sắp xếp cho vùng dữ liệu.
2. Tab `_MENU_SYNC_BACKUP` **không** có snapshot nào giữa 08:35:56 và 09:59:21
   (`f9fcb9620db6` do chính preflight tạo). Mọi lượt ghi live của tool đều chụp
   snapshot trước khi ghi, nên không có lượt ghi live nào trong khoảng đó.
3. Code trong repo không có đường nào tạo ra trạng thái đó: `CombinedQuoteWriter`
   từ chối `full_grid`/`format_ui` cho `MENU`, `MenuLayoutGuard` chỉ cho ghi `A:H`,
   và `menu_expand` chỉ chèn dòng — không tạo/xóa merge, không ghi `J:M`.

Kết luận: vùng `A:H` bị chỉnh trực tiếp trên Google Sheets (chuyển thành Table, sắp
xếp lại dòng, tỉa bớt panel `J:M`), không phải do `sync-menu`.

## 3. Lỗi tiềm ẩn trong code (đã sửa)

Quá trình khôi phục phát hiện một lỗi thật của tool, độc lập với sự cố trên:

* `menu_expand.shift_menu_baseline` và `_formula_requests` chỉ thay vùng
  `B8:H<n>` trong công thức `J:M`, **không** dời tham chiếu ô chọn `K<dòng>`.
  Google tự dời tham chiếu khi chèn dòng, nhưng tool ghi lại công thức bằng chuỗi
  tường minh nên ghi đè bằng `K38` cũ ⇒ sau mỗi lần tự mở rộng, panel chọn sản phẩm
  hiển thị `—` vì `VLOOKUP` trỏ ô trống.
  Điều này đã tái hiện thật: lượt cron 10:34 tự mở rộng 10 dòng và ghi
  `VLOOKUP(K38; B8:H52)` trong khi ô chọn đã là `K55`.
* `is_menu_product_row` coi dòng tiêu đề lặp là dòng sản phẩm (vì `B` và `F` không
  trống), nên nếu lọt vào pipeline thì tool có thể ghi sản phẩm đè lên dòng đó.

## 4. Thay đổi trong repo

| File | Thay đổi |
| --- | --- |
| `src/shop_integration/menu_repair.py` | **Mới**: lập kế hoạch + ghi khôi phục layout (merge, panel `J:M`, dropdown, công thức, vùng bảo vệ, khổ cột) và tự kiểm tra sau khi ghi |
| `src/shop_integration/cli.py` | **Mới**: lệnh `sync-menu-restore` (mặc định chỉ đọc, `--apply --confirm` mới ghi) |
| `data/menu-policy-panel.json` | **Mới**: bản lưu panel `J:M` (dòng 7–40) từ `backups/phase43-final/migration-20261125T143440+0700/structures.json` |
| `src/shop_integration/menu_expand.py` | Dời tham chiếu `K<dòng>` khi dời panel (`shift_menu_baseline`, `_formula_requests`) |
| `src/shop_integration/menu_layout.py` | Phát hiện dòng tiêu đề bị nhân bản; chuẩn hóa `K<dòng>` khi so công thức |
| `src/shop_integration/combined_quote.py` | `is_menu_header_row`: dòng tiêu đề lặp không phải dòng sản phẩm |
| `data/menu-baseline.json` | Sửa công thức `J40`: `BAO_GIA_TONG_HOP!B2:E26` cột **4** (WARRANTY) → cột **2** (DESCRIPTION). Baseline cũ trả về mã bảo hành thay vì mô tả |
| `tests/test_menu_repair.py` | **Mới**: 9 test cho kế hoạch, ghi, kiểm tra, chặn khi dữ liệu không liền mạch |
| `docs/operations.md` | Mục 6.4 + 3 dòng xử lý sự cố |
| `docs/archive/menu_layout_hotfix_2026-11-27.md` | Hồ sơ sự cố này |

## 5. Đã làm trên Google Sheet

1. Backup: `_MENU_SYNC_BACKUP` (checksum `24f155dc9eb3`) + `backups/menu-restore-*.json`.
2. `deleteTable` cho bảng `Bảng_1` (bảng này chặn xóa dòng tiêu đề và đã làm trống
   `A8:H55` khi bị xóa — dữ liệu được ghi lại ngay từ snapshot bền).
3. Ghi lại 35 dòng sản phẩm vào `A8:H42` từ snapshot bền; giá `194.000 ₫` dạng text
   được đưa về số `194000`.
4. `sync-menu-restore --apply --confirm`: 52 lần bỏ gộp + 52 lần gộp lại, ghi panel
   `J7:M57`, footer `A63:H70`, đặt dropdown `K55 = MENU!$B$8:$B$52`, hai công thức
   `J56/J57` trỏ `K55`, vùng bảo vệ `I1:M72`, khổ cột `A=75`, `B=230`.
5. Sửa công thức `J57`: baseline cũ lấy cột 4 của `BAO_GIA_TONG_HOP!B2:E26` (WARRANTY)
   nên ô chi tiết hiện `BHF`; đổi sang cột 2 (DESCRIPTION) rồi ghi lại. Ô chi tiết
   giờ hiện đúng mô tả, ô thời hạn/bảo hành/giá vẫn đúng (`J56`).

Hình học hiện tại: vùng chọn `B8:B52` (45 chỗ, 35 sản phẩm, 10 chỗ trống), panel
`J54:M62`, footer `A63:H70`, protected `I1:M72` — đúng bằng baseline dịch 17 dòng,
tức 7 dòng cho 35 sản phẩm và 10 dòng do `MENU_AUTO_EXPAND` chèn thêm.

## 6. Kết quả kiểm tra

| Hạng mục | Trước | Sau |
| --- | --- | --- |
| Vùng gộp | 54 | **62** |
| Dropdown chọn sản phẩm | không có | `K55 = MENU!$B$8:$B$52` |
| Công thức panel | không có | `J56`, `J57` (trỏ `K55`) |
| Ô chi tiết `J57` | (mất) | hiện đúng mô tả sản phẩm |
| Dòng tiêu đề lặp | dòng 8 | không |
| Khổ cột `A` / `B` | 83 / 300 | **75 / 230** |
| Sản phẩm | 35 | **35**, cùng `ProductID`, cùng giá/tồn kho |
| `sync-menu-preflight` | FAIL | **PASS** (`MENU tự mở rộng 17 dòng… khớp đúng phép dịch dòng`) |
| `pytest` | — | **391 passed** (exit 0) |

## 7. Việc cần làm tiếp

1. Commit + push các thay đổi trên để GitHub Actions dùng code đã sửa. Trước khi
   push, preflight trên CI vẫn có thể báo lệch vì CI đang chạy code cũ (không dời
   `K<dòng>`); lỗi này chỉ chặn ghi, không ghi sai.
2. Không bật lại Google Sheets **Table** trên `A:H`; nếu cần lọc, dùng Filter view.
3. Muốn đổi layout có chủ ý: sửa tay → `menu-baseline --out` → commit
   `data/menu-baseline.json`, không sửa baseline để lách preflight.
