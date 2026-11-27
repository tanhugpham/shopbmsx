# Vận hành

Tài liệu này là nguồn duy nhất cho các việc chạy hằng ngày: lịch đồng bộ, backup,
khôi phục, xử lý lỗi và dọn dẹp. Cài đặt lần đầu nằm ở [setup_vi.md](setup_vi.md),
thiết kế nằm ở [architecture.md](architecture.md).

## 1. Nhịp chạy hiện tại

| Việc | Ở đâu | Nhịp | Ghi Google? |
| --- | --- | --- | --- |
| `sync-menu` (ghi MENU) | GitHub Actions `.github/workflows/sync-menu.yml` | cron `*/5 * * * *`, thực tế thường trễ (khoảng 25–30 phút/lượt) | Có, `DRY_RUN=false` |
| `sync-sources` (đọc nguồn) | GitHub Actions `.github/workflows/sync-sources.yml` | `17 1 * * *` UTC | Không, luôn `DRY_RUN=true` |
| `ci` (test) | GitHub Actions `.github/workflows/ci.yml` | mỗi push / pull request | Không |
| Web Service | Render `shopxoainon-web` | luôn chạy, `/health` | Không, `DRY_RUN=true` |
| Scheduler trong tiến trình | Render / máy local | tắt | Không |

Quy ước bất biến:

- **Chỉ GitHub Actions được ghi MENU.** Render giữ `ENABLE_SCHEDULED_SYNC=false`,
  `ENABLE_SCHEDULED_MENU_LIVE=false`, `DRY_RUN=true`.
- `ENABLE_PURCHASE` và `ENABLE_TELEGRAM` luôn `false`; mã không có đường tạo đơn.
- Cron GitHub có thể trễ. Không dùng `setInterval`, ping giữ ấm hay Cron Render
  miễn phí để thay thế.

## 2. Bí mật và biến bắt buộc

GitHub → **Settings → Secrets and variables → Actions**:

| Secret | Dùng cho | Thiếu thì |
| --- | --- | --- |
| `SHOP_XOAI_NON_API_KEY` | đọc catalog shop | preflight FAIL |
| `GOOGLE_SPREADSHEET_ID` | spreadsheet duy nhất | preflight FAIL |
| `GOOGLE_CREDENTIALS_JSON` | ghi file `GOOGLE_CREDENTIALS_FILE.json` trên runner | preflight FAIL |
| `NASTELE_API_KEY` | `sync-sources` (tùy chọn) | Nastele ở trạng thái `BLOCKED`, không phải lỗi |
| `GOOGLE_DOCUMENT_ID` | báo cáo Docs (tùy chọn) | bỏ qua phần Docs |

`sync-menu.yml` kiểm tra tên ba secret bắt buộc trước khi chạy. Giá trị bí mật chỉ
nằm trong secret và `.env`; nhật ký đã được lọc chuỗi dài từ 8 ký tự trùng key và
cụm `BEGIN PRIVATE KEY`.

## 3. Một lượt `sync-menu`

Workflow chạy tuần tự, dừng ngay khi một bước trả mã khác 0:

1. **Kiểm tra secret** — đủ ba tên bắt buộc.
2. **Ghi credentials** — từ secret ra file, không in ra log.
3. **Preflight** `sync-menu-preflight --verify-backup`:
   - `ENABLE_PURCHASE`, `ENABLE_TELEGRAM`, `ENABLE_SCHEDULED_SYNC`,
     `ENABLE_SCHEDULED_MENU_LIVE` phải là `false`;
   - có `SHOP_XOAI_NON_API_KEY`, `GOOGLE_SPREADSHEET_ID`, file credentials;
   - không còn `NASTELE_SPREADSHEET_ID` / `LAST_SPREADSHEET_ID`;
   - `GOOGLE_SPREADSHEET_ID` khớp spreadsheet chính thức;
   - đọc được cấu trúc MENU và khớp `data/menu-baseline.json`;
   - ghi rồi đọc lại tab backup bền `_MENU_SYNC_BACKUP`, checksum phải khớp;
   - Render `/health` báo scheduler tắt.
4. **Ghi MENU** `DRY_RUN=false` với `MENU_REQUIRE_DURABLE_BACKUP=true`:
   - đọc catalog Shop Xoài Non, ghi `BAO_GIA`;
   - đọc `NASTELEID` (không ghi dữ liệu vào tab này);
   - cập nhật `MENU` cột A–H, ghi `BAO_GIA_TONG_HOP`;
   - tự mở rộng vùng MENU nếu thiếu dòng (`MENU_AUTO_EXPAND`);
   - gom sản phẩm cùng họ về cạnh nhau (`MENU_GROUP_SIMILAR`).

Lượt ghi nào cũng chụp vùng sắp đổi (`MENU!A1:H250`, `BAO_GIA!A1:H400`,
`BAO_GIA_TONG_HOP!A1:H400`) vào tab `_MENU_SYNC_BACKUP` **trước** khi ghi, giữ 8
snapshot gần nhất, mỗi snapshot có checksum và restore plan.

## 4. Kiểm tra sức khỏe

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration config-check          # biến hiệu lực, biến cũ bị bỏ qua
python -m shop_integration menu-audit            # chỉ đọc: ProductID/VariantID, dòng lạ
python -m shop_integration menu-baseline --compare data/menu-baseline.json
python -m shop_integration backup-readiness      # backup local có đủ cho live?
python -m shop_integration sync-menu             # DRY_RUN=true: chỉ lập kế hoạch
```

| Khi nào | Chạy |
| --- | --- |
| Sau mỗi lần đổi layout/công thức/dropdown MENU | `menu-baseline --out` rồi commit `data/menu-baseline.json` |
| Nghi ngờ MENU lệch | `menu-audit`, `menu-baseline --compare` |
| Trước khi đổi `.env`, secret hoặc workflow | `config-check`, `sync-menu` dry-run |
| Sự cố ghi MENU | log GitHub Actions, tab `_MENU_SYNC_BACKUP`, mục 6 |
| Định kỳ mỗi tuần | xem 3 lượt `sync-menu` gần nhất có xanh, `menu-audit` sạch |

## 5. Ba tầng backup

| Tầng | Vị trí | Nội dung | Ai dùng |
| --- | --- | --- | --- |
| Bền, sống cùng file Google | tab `_MENU_SYNC_BACKUP` | `MENU!A1:H250`, `BAO_GIA!A1:H400`, `BAO_GIA_TONG_HOP!A1:H400`, checksum, `restore_plan`; giữ 8 snapshot | khôi phục sau một lượt ghi MENU hỏng |
| Snapshot máy | `backups/` (đã gitignore) | `migration-*` (mỗi lượt 3 thư mục cho 3 tab), `menu-before-*` / `menu-after-*`, báo cáo chạy | so sánh, khôi phục thứ tự MENU, điều tra sự cố |
| Snapshot catalog/Docs | `backups/` do lệnh `backup` tạo | Sheets + Docs đã che bí mật | `backup-check`, `restore-plan`, `restore --confirm` |

Tab `_MENU_SYNC_BACKUP` là tầng duy nhất sống ngoài máy chạy. Không xóa tab này,
không đổi tên, không sửa tay nội dung của nó.

### Chính sách giữ bản local

Giữ tối thiểu:

- 3 lượt `migration-*` mới nhất (mỗi lượt 3 thư mục: `MENU`, `BAO_GIA`,
  `BAO_GIA_TONG_HOP`);
- `backups/phase43-final/` (baseline + journal chốt Phase 4.3);
- `menu-before-*`, `menu-after-*`, `menu-group-run-*` của lần gom 4.3J;
- mọi snapshot `backup` mới nhất mà `backup-check` báo `verified`.

Được phép xóa:

- `migration-*` cũ hơn 3 lượt gần nhất — chỉ là bản sao vùng Google đã đổi, không
  phải nguồn sự thật;
- `__pycache__`, `*.pyc`, `.pytest_cache`, `backups/__pycache__`;
- báo cáo tạm `backups/_*.txt`, file probe một lần ở thư mục gốc.

Không được xóa: tab `_MENU_SYNC_BACKUP`, `data/menu-baseline.json`,
`GOOGLE_CREDENTIALS_FILE.json` (nếu có), `.env`, snapshot mới nhất vừa dùng để ghi.

## 6. Khôi phục

### 6.1 Khôi phục vùng MENU từ tab bền

`restore_plan` trong tab mô tả đúng thao tác; làm theo thứ tự:

1. Mở tab `_MENU_SYNC_BACKUP`, chọn `SNAPSHOT_ID` mới nhất trước lượt ghi hỏng.
2. Ghép các `PART` theo thứ tự, giải nén JSON trong cột `BODY`.
3. Đối chiếu `CHECKSUM`; lệch thì không dùng snapshot đó.
4. Ghi lại **đúng ba vùng** đã chụp. Ô bắt đầu bằng `=` ghi `USER_ENTERED`
   (giữ công thức), ô còn lại ghi `RAW`.
5. Không ghi `NASTELEID`, `Trang tính1`, cột `I:M` của MENU, merge, dropdown
   `K38`, công thức `J39/J40`, conditional format `H7:H45`, protected range.
6. Chạy `menu-audit` và `menu-baseline --compare data/menu-baseline.json`.

### 6.2 Khôi phục thứ tự MENU sau khi gom họ sản phẩm

Lần gom 4.3J đã lưu `backups/menu-before-20261126-111724.json` (toàn bộ `A1:M200`):

1. Đọc file, lấy `A:H` của từng dòng sản phẩm.
2. Ghi `RAW` theo đúng thứ tự cũ vào `MENU!A:H` của các dòng sản phẩm.
3. So `A:H` theo `ProductID`/`VariantID` và xác nhận `J:M` không đổi.
4. Muốn dừng hẳn việc gom: đặt `MENU_GROUP_SIMILAR=false` trong `.env` và trong
   workflow, rồi chạy lại `sync-menu`.

Bản local giữ lại `backups/migration-20261126T111432+0700*/` (chụp 11:14, trước lượt
ghi 11:17) là điểm đối chiếu thứ hai cho vùng `MENU` trước khi gom.

### 6.3 Khôi phục catalog/Docs

```powershell
python -m shop_integration backup-check        # checksum + hướng dẫn
python -m shop_integration restore-plan        # chỉ đọc, liệt kê xung đột
python -m shop_integration restore --confirm   # chỉ ghi khi không còn xung đột
```

`restore` không ghi `Trang tính1`, không xóa dòng, không tạo file Google, không đổi
quyền chia sẻ.

### 6.4 Khôi phục layout MENU sau khi bị sửa tay

Dùng khi `sync-menu-preflight` báo `MENU lệch baseline` vì ai đó chỉnh trực tiếp
trên Google Sheets: dòng tiêu đề bị nhân bản, panel chính sách `J:M` bị cắt, mất
dropdown `K`, mất công thức `J:M`, hoặc khổ cột bị auto-fit. Lệnh này dựng lại
layout từ `data/menu-baseline.json` + `data/menu-policy-panel.json`, **không** đổi
giá bán và **không** đổi nội dung sản phẩm `A:H`.

```powershell
$env:PYTHONPATH = "src"
python -m shop_integration sync-menu-restore                     # chỉ đọc, in kế hoạch
python -m shop_integration sync-menu-restore --apply --confirm   # backup rồi mới ghi
python -m shop_integration sync-menu-preflight                   # phải PASS sau khi ghi
```

Trình tự lệnh ghi:

1. chụp `_MENU_SYNC_BACKUP` và `backups/menu-restore-<thời gian>.json`;
2. bỏ gộp vùng `J:M` và footer, ghi lại panel `J:M` + khối footer từ bản lưu;
3. gộp lại đúng 62 vùng, đặt dropdown `K`, hai công thức `J:M`, vùng bảo vệ, khổ cột;
4. đọc lại và tự kiểm tra: đủ 62 vùng gộp, dropdown đúng vùng sản phẩm, hai công thức
   trỏ đúng ô chọn `K`, danh sách `ProductID` và giá/tồn kho không đổi.

Nếu Google Sheets đang bật **Table** (Bảng) trên vùng `A:H` thì phải xóa Table trước
(`deleteTable`), vì Google chặn xóa dòng tiêu đề của Table và Table cũng tự thêm
dòng tiêu đề + auto-fit khổ cột. Sau khi xóa Table, chạy lại `sync-menu-restore`.

### 6.5 Màu STATUS và baseline MENU

Màu trạng thái sản phẩm là **định dạng điều kiện trên cột `H`** (STATUS), không phải
giá trị ô, nên được giữ nguyên và không bị `sync-menu` ghi đè. Chủ shop có thể thêm
rule cho các nhãn trạng thái đang dùng (`IN STOCK`, `LOW STOCK`, `SOLD OUT`, `CONTACT`)
theo cách thủ công; đó là thay đổi giao diện hợp lệ.

`data/menu-baseline.json` là **mốc hình học trước lần tự mở rộng đầu tiên**
(vùng chọn `B8:B35`, panel `J37:M45`, footer `A46:H53`). Không được đổi mốc này sang
hình học đã mở rộng: `sync-menu-restore` và phép dịch dòng đều tính từ mốc gốc.

Vì vậy khi Google đã tự mở rộng và số rule định dạng điều kiện cũng đổi, preflight
báo `mất 18/thêm 18` vùng gộp (đúng là baseline dịch xuống) và không nhận ra phép
dịch, do phần đếm rule không khớp. Trình tự xử lý:

1. `menu-audit` + đọc cấu trúc: xác nhận mọi vùng gộp/footer dịch **cùng một số dòng**,
   dropdown `K` và hai công thức `J:M` trỏ đúng ô chọn mới, vùng bảo vệ `I:M` phủ hết
   panel mới, và mọi rule định dạng điều kiện chỉ nằm trên cột `H`.
2. Chỉ cập nhật `conditional_format_count` trong `data/menu-baseline.json` cho bằng
   số rule đã xác minh; **không** đổi merge/công thức/dropdown/protected của baseline.
3. Chạy lại `sync-menu-preflight`; phải PASS kèm ghi chú `MENU tự mở rộng <n> dòng`.

Không xóa rule định dạng điều kiện mới và không đổi baseline chỉ để preflight im lặng:
mỗi lần số rule đổi tiếp vẫn phải FAIL và được xác minh lại như trên.

## 7. Sự cố thường gặp

| Hiện tượng | Nguyên nhân thường gặp | Việc cần làm |
| --- | --- | --- |
| `Preflight: FAIL` + tên biến | thiếu secret, còn biến cũ, cờ chưa `false` | sửa secret/`.env`, chạy lại `config-check` |
| `MENU lệch baseline 'data/menu-baseline.json'` | ai đó đổi merge/công thức/dropdown/header | `sync-menu-restore` (mục 6.4), không sửa tay từng ô |
| `Số rule định dạng điều kiện đổi từ 8 sang 12` | chủ shop thêm rule màu STATUS trên cột `H` | xác minh rule chỉ nằm trên `H` rồi cập nhật đúng số rule trong baseline (mục 6.5), giữ nguyên rule |
| `Dòng tiêu đề MENU bị nhân bản ở dòng 8` | ai đó chuyển vùng `A:H` thành Table hoặc copy dòng tiêu đề | xóa Table nếu có, rồi `sync-menu-restore --apply --confirm` |
| `Cannot delete a table header row` | Google Sheets Table đang bật trên `A:H` | `deleteTable` rồi chạy lại `sync-menu-restore` |
| `Dòng tiêu đề MENU không đúng` | header A–H đổi tên hoặc đổi thứ tự | sửa header, không đổi baseline để lách |
| `MenuCapacity` / hết hàng trống | vùng A:H không còn dòng trống trong danh mục | để `MENU_AUTO_EXPAND=true` chèn thêm, hoặc xử lý tay rồi chạy lại |
| `MENU_GROUP_SIMILAR` báo lệch sau khi ghi | đọc lại không khớp payload | dừng sync, khôi phục từ `_MENU_SYNC_BACKUP`, báo lỗi kèm đường dẫn `backups/` |
| `BACKUP … NOT_READY` | không tạo được/không đọc lại được snapshot bền | kiểm tra quyền Sửa, tab `_MENU_SYNC_BACKUP`, chạy `backup-readiness` |
| Cron không chạy đúng 5 phút | giới hạn của GitHub Actions | coi như bình thường, không thêm scheduler khác |
| `HTTP 401/403` từ API shop | key sai, thiếu scope | tạo key mới, cập nhật secret |
| `HTTP 429` | vượt 60 req/phút (Nastele) | đợi, client đã tôn trọng `Retry-After` |
| Render `/health` 502 | Start Command sai | `PYTHONPATH=src python scripts/start_web.py` |
| CI đỏ | test fail | sửa test trước; Render không deploy commit fail (`autoDeployTrigger: checksPass`) |

Không bao giờ “sửa nhanh” bằng cách: tắt baseline, nới whitelist A:H, ghi vào
`J:M`, bật scheduler trên Render, hay chạy `restore` khi còn xung đột.

## 8. Kiểm thử và CI

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

- 382 test, toàn bộ dùng HTTP giả lập và gateway Google trong bộ nhớ; không cần
  key thật, không gọi mạng.
- `ci.yml` chạy `python -m pytest` trên mỗi push/PR.
- Trước khi push thay đổi ảnh hưởng MENU: chạy `pytest` và `sync-menu` dry-run.

## 9. Dọn dẹp định kỳ

1. `git status` phải sạch; không để file probe, `_audit_*`, `_tmp*` ở thư mục gốc.
2. `backups/` theo chính sách mục 5; xóa bằng `Remove-Item -LiteralPath` với đường
   dẫn tuyệt đối, kiểm tra lại danh sách trước khi xóa.
3. Cache Python (`__pycache__`, `.pytest_cache`) chỉ xóa khi không chạy test.
4. Tài liệu lịch sử để trong `docs/archive/`, không đặt ở `docs/`.
5. Không xóa: `data/`, `docs/` hiện hành, `scripts/start_web.py`,
   `.github/workflows/`, `render.yaml`, `requirements.txt`, `pytest.ini`,
   `.python-version`, `.env.example`.

## 10. Việc không được làm

- Không thêm cột/sheet mới cho MENU, không đổi giao diện MENU trong lúc vận hành.
- Không ghi `J:M`, không sửa merge, dropdown `K38`, công thức `J39/J40`.
- Không mở đường tạo đơn, không lưu tài khoản đã giao, không bật Telegram.
- Không tạo file Google Drive mới, không đổi quyền chia sẻ.
- Không commit `.env`, JSON service account, API key hay snapshot dữ liệu thật.

