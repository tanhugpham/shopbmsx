# Phase 4.3H — Audit cuối, plan migration mới và readiness auto-sync

Ngày chạy: 2026-11-25. Lượt này **chỉ đọc Google Sheets và API shop**, tạo snapshot
local mới, lập plan mới và chạy dry-run. Không ghi Google Sheets/Docs, không tạo đơn,
không bật scheduler, không apply migration, không đổi baseline chính thức.

Spreadsheet duy nhất: `196goR1lDZN6zJfoC3a7vOVyo5u9o-pvxXpc3mnGrGSY`.
`DRY_RUN=true`; `ENABLE_PURCHASE`/`ENABLE_TELEGRAM` không có hiệu lực trong code.

## 1. Audit live

### 1.1 Nguồn API

| Hạng mục | Kết quả |
| --- | --- |
| Số sản phẩm | **24** |
| Số biến thể | **24** |
| Số dòng báo giá | **24** |
| Identity duy nhất | **24** |
| Lỗi thiếu/trùng ProductID–VariantID | **0** |
| `local_41/local_41` | Có; giá vốn 69.000; giá bán **90.000**; tồn **7**; `in_stock=true` |
| Giá/tồn | Tất cả 24 dòng đều có PRICE và STOCK; giá đã thay đổi so với plan cũ (ví dụ `local_1` hiện 194.000) |

Không dùng số 24 làm hằng số quyết định. Nếu lần đọc sau có số sản phẩm hoặc identity
khác, migration phải lập plan mới và dừng plan cũ.

### 1.2 Schema và layout 4 tab

Chỉ có 4 tab: `MENU`, `NASTELEID`, `BAO_GIA`, `BAO_GIA_TONG_HOP`.

| Tab | Dữ liệu thật | Schema / layout |
| --- | --- | --- |
| `MENU` | 40 dòng, header dòng 6; 23 sản phẩm hợp lệ dòng 8–30; A31:H35 trống; dòng 36 là bản sao lỗi `SXN-24` của `SXN-01` | 62 merge; 3 công thức `G5`, `J39`, `J40`; 1 dropdown `K38`; 2 protected range; 8 conditional format; 13 độ rộng cột; 0 chiều cao dòng tùy chỉnh |
| `BAO_GIA` | 24 dòng (header + 23); cột B là `Shop Xoài Non`, tên sản phẩm bị đẩy sang C/D | Header 8 cột, cột H lặp `STOCK`; 23 dòng lệch cột |
| `BAO_GIA_TONG_HOP` | 25 dòng (header + 1 AI PRO + 23 shop); cùng lỗi lệch cột | Header 7 cột đúng; dòng AI PRO nhập tay đang đúng |
| `NASTELEID` | 2 dòng (header + 1 AI PRO nhập tay); không bị migration ghi | Header 7 cột đúng; 0 công thức/validation/merge/protected |

`MENU` hiện tại:

- `K38` = `=MENU!$B$8:$B$30`
- `J39` dùng `VLOOKUP(... B8:H30 ...)`
- `J40` dùng `BAO_GIA_TONG_HOP!C2:E24` và `B8:C30`
- Baseline `backups/menu-baseline-phase43b.json` **khớp**, fingerprint
  `f4e9a3afe510bf353ad64f6b71d2e3ee9b26f9a560605b276d5fdf830678dd01`.
- Không có `@tanhung929` trong cả 4 tab (0 lần xuất hiện).

Bản sao dòng 36 khớp `local_1/local_1`, trùng `SXN-01`, giá cũ 203.000; không phải
sản phẩm thứ 24. Sản phẩm còn thiếu là `local_41/local_41`, gợi ý dòng 31.

## 2. Snapshot nguồn mới và plan v3

Không dùng lại payload 11:56 hoặc 12:35. Snapshot mới được đọc trong lượt này:

- `backups/phase43h/migration-snapshots/migration-20261125T135441+0700/`
- `manifest.status=READY`, `reused_snapshot=false`, `complete_fidelity=false`
- 4 tab: `MENU`, `NASTELEID`, `BAO_GIA`, `BAO_GIA_TONG_HOP`
- snapshot checksum `9a10621cea9b5372b99fced8abe308997dc91c3844c43ca8687d4462d3ab4170`
- structures checksum `435d984a56b2d5136cb24bb716410290ee376630319d5541fe7baaeb143ddae5`
- restore plan 9 dòng (8 thao tác + 1 giới hạn), không chạm `NASTELEID`

Hai lần đọc API cách nhau 2 giây cho cùng SHA-256:

`7f5cb6f81436cde22025fafbb8da747468cd0e6633a4684f2c9cd166de0efd4a`

Plan mới: `PLANNED`, `dry_run=true`, `applied=false`, **8 thao tác**, **135 ô lệch**
(plan cũ 136 ô không còn dùng).

| Tab / vùng | Kết quả plan |
| --- | --- |
| `BAO_GIA!A1:G25` | 24 sản phẩm từ API, 7 cột đúng contract |
| `BAO_GIA!H1:H25` | Xóa giá trị cột H trùng; không xóa cột |
| `BAO_GIA_TONG_HOP!A1:G26` | 1 dòng AI PRO nhập tay + 24 dòng shop |
| `MENU!A31:H31` | `SXN-24` cho `local_41/local_41`; giá 90.000; tồn 7 |
| `MENU!A36:H36` | Xóa giá trị bản sao legacy; không xóa dòng, không đụng I:M |
| `MENU!K38` | `ONE_OF_RANGE` = `=MENU!$B$8:$B$35` |
| `MENU!J39` | Mở `B8:H35` |
| `MENU!J40` | `BAO_GIA_TONG_HOP!B2:E26` và `B8:C35` |

Sau migration dự kiến: **24 sản phẩm, 24 ID duy nhất**; `local_41` nằm ở dòng 12 của
`BAO_GIA` và dòng 13 của `BAO_GIA_TONG_HOP`; AI PRO vẫn ở dòng 2 của bảng tổng hợp.
Không có thao tác insert/delete row, không đổi merge, protected range, chiều cao/rộng.

Plan **chưa được apply**. Không có lần ghi Google nào trong Phase 4.3H.

## 3. Content ownership và liên hệ `@tanhung929`

Hiện trạng:

- `StoreProduct.contact` mặc định `@tanhung929`; `customer_profile()["Contact"]` có thể
  hiển thị liên hệ này.
- `customer_row()` chỉ trả đúng 8 cột MENU hiện tại và **không có cột Contact**.
  Vì vậy `@tanhung929` không xuất hiện trong MENU, không làm thay đổi layout.
- Quét live 4 tab: **0 lần** xuất hiện `@tanhung929`.
- Muốn hiển thị liên hệ trên MENU phải thêm cột/dòng hoặc đổi layout; đây là thay đổi
  giao diện chưa được duyệt, không thực hiện trong migration 8 thao tác.

`ContentOverride` hỗ trợ khóa `display_name`, `display_description`, `duration`,
`sales_content`, `contact`. `warranty` **không được khóa** vì là điều kiện bắt buộc
của nhà cung cấp; nguồn nêu bảo hành thì giữ bảo hành nguồn, nếu không thì giữ giá trị cũ.

Giới hạn quan trọng: logic `ContentOverride` mới nằm trong `CatalogSyncService` chạy
**read-only**. Đường live hiện tại `sync-menu -> CombinedQuoteWriter` chưa đọc một kho
override bền vững và chưa áp locked fields. Vì vậy **chưa thể cam kết** trường chỉnh tay
không bị ghi đè khi bật live sync. Không bật `DRY_RUN=false` cho tới khi nối phần này.

`AI PRO` nhập tay trên `NASTELEID` được đọc theo hợp đồng, không bị tool ghi; migration
chỉ dựng lại `BAO_GIA_TONG_HOP` với dòng AI PRO hiện tại. Không thay đổi trường kích hoạt
hoặc bảo hành bắt buộc từ nhà cung cấp.

## 4. Quy trình kiểm tra sau migration

Chỉ chạy sau khi plan v3 được phê duyệt và apply thành công. Tất cả bước dưới đây đọc
lại dữ liệu thật; không dựa vào giá trị đã tính trước.

1. **Product count / ID**: `BAO_GIA` có 24 dòng dữ liệu; `MENU` có 24 dòng sản phẩm và
   24 ID duy nhất; `A36:H36` trống; `A31` là `SXN-24`.
2. **Identity**: 24 cặp ProductID/VariantID trên API khớp 24 dòng nguồn; không trùng tên
   hoặc ID; `local_41/local_41` nằm ở dòng 12 `BAO_GIA`, dòng 13 bảng tổng hợp.
3. **Giá bán / tồn kho**: đọc lại `BAO_GIA` và `BAO_GIA_TONG_HOP`; so từng dòng với
   snapshot API vừa đọc. Cho phép giá/tồn đã đổi so với lúc lập plan, nhưng phải khớp
   snapshot tại thời điểm apply, không dùng payload cũ.
4. **AI PRO**: dòng 2 `BAO_GIA_TONG_HOP` vẫn là
   `[1, "AI PRO", "test", "TESST", "HELLO", 100000, 23]`; không bị ghi từ API.
5. **Dropdown K38**: `ONE_OF_RANGE` = `=MENU!$B$8:$B$35`.
6. **Công thức**: `J39` dùng `B8:H35`; `J40` dùng `BAO_GIA_TONG_HOP!B2:E26` và
   `B8:C35`; không đổi công thức nào khác ngoài `G5=NOW()` động.
7. **Baseline**: baseline phase43b sẽ **lệch có chủ đích** ở K38/J39/J40. Không chốt
   baseline mới cho tới khi toàn bộ read-back PASS; sau đó mới tạo
   `backups/menu-baseline-phase43h.json`.
8. **Policy / layout**: J:M ngoài J39/J40 không đổi; merge, protected range, conditional
   format, độ rộng cột, chiều cao dòng giữ nguyên; không chèn/xóa dòng.
9. **Sync dry-run**: chạy `sync-menu` với `DRY_RUN=true`; kỳ vọng không còn cảnh báo
   lệch cột `BAO_GIA`, `menu=24`, không ghi ô. Sau đó chạy `menu-migrate` lần hai;
   kỳ vọng `UP_TO_DATE`, 0 thao tác.

Lệnh kiểm tra dự kiến:

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:DRY_RUN = "true"
python -m shop_integration menu-audit --json backups/menu-audit-phase43h-post.json
python -m shop_integration menu-baseline --compare backups/menu-baseline-phase43b.json
python -m shop_integration menu-migrate --json backups/menu-migration-phase43h-post.json
python -m shop_integration sync-menu
```

`menu-baseline --compare` được phép trả mã 1 ở K38/J39/J40 trước khi chốt baseline mới;
mọi khác biệt ngoài 3 ô này là blocker.

## 5. Auto-sync readiness

| Hạng mục | Trạng thái | Ghi chú |
| --- | --- | --- |
| `sync-menu` dry-run | **FAIL có kiểm soát** | Dừng ở `BAO_GIA` lệch cột; `menu=0`, `cells=0`, exit 1. Đây là hành vi đúng trước migration |
| Scheduler | **OFF** | Local env và Render runtime guard đặt `ENABLE_SCHEDULED_SYNC=false`, `ENABLE_SCHEDULED_MENU_LIVE=false`; `/health` báo `enabled=false`, `started=false`, `live=false` |
| Render `/health` | **OK** | `status=ok`, `dry_run=true`, `enable_purchase=false`, `scheduled_sync.enabled=false`, `last_status=idle` |
| Render env dashboard | **Chưa xác minh** | Không có Render API token; `render.yaml` đặt false nhưng không đọc được dashboard |
| GitHub Actions | **CI OK** | Commit `83ab4b7` có CI success; `sync-menu` chỉ chạy tay, lần gần nhất thuộc commit cũ `b909194`; không có cron |
| Backup migration | **READY** | Snapshot/structures có checksum và restore plan; chỉ tồn tại local, không bền vững |
| `backup-readiness` | **FAIL / NOT_READY** | Không có kho backup bền vững cho GitHub Actions; không đủ điều kiện scheduled live sync |
| Source freshness | **Ổn định trong 2 lần đọc** | Cùng SHA-256; nhưng không đảm bảo nguồn không đổi sau đó |
| Failure recovery | **Có plan, chưa có tự động** | 9 dòng restore; không tự khôi phục nếu Google lỗi giữa các thao tác; không chèn/xóa dòng |

Kết luận: **chưa đủ điều kiện bật auto-sync/live**. Cần migration PASS, baseline mới,
backup bền vững và cơ chế content override trước.

### Đề xuất cập nhật giá/tồn định kỳ (giai đoạn sau)

1. Dùng một catalog chuẩn `StoreProduct` với `store_product_id`/`menu_code` ổn định;
   không dùng số thứ tự dòng làm khóa. Mapping nguồn chỉ nằm nội bộ.
2. Lưu content override và locked fields bền vững; API chỉ cập nhật trường không khóa.
   Bảo hành vẫn do nguồn quyết định.
3. Ghi giá/tồn theo khóa ID vào vùng dữ liệu riêng (hoặc overlay), không ghi lại
   `PRODUCT`/`DESCRIPTION` mỗi lần sync. Chỉ cập nhật `PRICE`, `STOCK`, `STATUS`.
4. Khi số sản phẩm tăng, mở rộng vùng theo số dòng thực tế thay vì slot cố định; tách
   panel chính sách J:M khỏi vùng A:H để không giới hạn bởi dòng 37. Việc này cần một
   phase layout riêng, không làm trong migration 4.3H.
5. Chỉ bật scheduler sau khi có backup bền vững, dry-run ổn định, cảnh báo lỗi nguồn
   và quy trình rollback đã kiểm thử.

## 6. Kiểm thử và bằng chứng local

| Kiểm tra | Kết quả |
| --- | --- |
| Full pytest | **309 passed, 0 failed, 0 error, 0 skipped**, 6.553s; JUnit `backups/phase43h/pytest.xml` |
| `compileall src scripts tests` | exit 0 |
| `config-check` | đúng `GOOGLE_SPREADSHEET_ID`; không có biến spreadsheet cũ; `DRY_RUN=true` |
| `menu-baseline --compare` | khớp baseline phase43b, exit 0 |
| `menu-audit` | 24 dòng sản phẩm; 1 legacy dòng 36; thiếu `local_41`; cảnh báo K38/J39/J40 cần mở |
| `menu-migrate` dry-run | `PLANNED`, 8 thao tác, backup READY, 135 ô lệch, exit 0 |
| `sync-menu` dry-run | exit 1, `menu=0`, `cells=0`, dừng ở lỗi lệch cột `BAO_GIA`; không ghi |
| `backup-readiness` | `FAIL`, scheduled live `NOT_READY` |
| `sources` | Shop 24 nguồn; Nastele catalog rỗng `NASTELE_CATALOG_EMPTY`; không tạo sản phẩm giả |
| Render `/health` | `status=ok`, `dry_run=true`, scheduler off, live false |
| URL cũ `shopxoainon-web.onrender.com` | HTTP 404 |

Các artifact local (không commit vì chứa snapshot/dữ liệu vận hành):

- `backups/phase43h/live-audit.json`
- `backups/phase43h/migration-plan.json`
- `backups/phase43h/source-freshness-and-target.json`
- `backups/phase43h/migration-snapshots/migration-20261125T135441+0700/`
- `backups/phase43h/pytest.xml`

## 7. Cần phê duyệt trước khi apply

1. Duyệt đúng 8 vùng: `BAO_GIA!A1:G25`, `BAO_GIA!H1:H25`,
   `BAO_GIA_TONG_HOP!A1:G26`, `MENU!A31:H31`, `MENU!A36:H36`, `MENU!K38`,
   `MENU!J39`, `MENU!J40`.
2. Duyệt `SXN-24` cho `local_41/local_41` và xóa giá trị dòng 36 sau khi xác minh trùng
   `SXN-01`; không xóa dòng, không đụng I:M.
3. Duyệt đổi `J40` sang `BAO_GIA_TONG_HOP!B2:E26` / `B8:C35` vì schema đổi.
4. Chốt baseline mới **sau** khi read-back PASS; không đổi baseline trước.
5. Chưa bật live sync/scheduler. Cần xử lý backup bền vững và content override trước.
6. Nếu số sản phẩm/identity/schema/MENU khác plan này, dừng và lập plan mới; không dùng
   payload 11:56/12:35 hoặc plan cũ.

Trạng thái cuối: **READY TO REVIEW — NOT APPLIED**. Không có Google write, không có
order, không có scheduler/live sync trong lượt này.
