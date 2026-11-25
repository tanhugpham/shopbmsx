# Deploy Render Web Service miễn phí (không thêm thẻ)

Cron job trên Render cần gói trả phí. Free chỉ có **Web Service**. Lịch chạy miễn phí dùng GitHub Actions đã có sẵn.

## Không làm

- Không bấm **New → Blueprint** nếu lần trước nó đòi Cron / Starter / thẻ.
- Không chọn instance **Starter**.
- Không tắt `DRY_RUN`.

## A. Tạo Web Service trên Render

1. Mở https://dashboard.render.com
2. Góc phải: **New → Web Service** (đúng dòng *web apps in Node.js, Python, Rails, etc.*)
3. Connect GitHub nếu chưa. Chọn repo `tanhugpham/shopbmsx`
4. Điền form:

| Ô | Giá trị |
| --- | --- |
| Name | `shopxoainon-web` |
| Region | Singapore |
| Branch | `main` |
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `PYTHONPATH=src python scripts/start_web.py` |
| Instance type | **Free** |

Hai ô này khác nhau. **Build** chỉ cài thư viện. **Start** mới chạy web. Nếu Build Command đang là `python scripts/start_web.py` thì deploy sẽ lỗi `No module named 'pydantic'`.

Thêm biến `PYTHON_VERSION` = `3.13.7` (Render mặc định 3.14). Repo có file `.python-version`.

5. **Advanced → Add Environment Variable**, lần lượt:

| Key | Value |
| --- | --- |
| `PYTHONPATH` | `src` |
| `PYTHON_VERSION` | `3.13.7` |
| `DRY_RUN` | `true` |
| `ENABLE_PURCHASE` | `false` |
| `ENABLE_TELEGRAM` | `false` |
| `SHOP_XOAI_NON_BASE_URL` | `https://shopxoainon.online/api/v1` |
| `NASTELE_BASE_URL` | `https://nastele.online` |
| `GOOGLE_CREDENTIALS_FILE` | `./GOOGLE_CREDENTIALS_FILE.json` |
| `SHOP_XOAI_NON_API_KEY` | key shop |
| `NASTELE_API_KEY` | key Nastele |
| `GOOGLE_SPREADSHEET_ID` | ID spreadsheet duy nhất (catalog, nguồn, MENU, báo giá) |
| `GOOGLE_DOCUMENT_ID` | ID Google Doc |
| `GOOGLE_CREDENTIALS_JSON` | nguyên JSON service account |
| `SYNC_TOKEN` | chuỗi bí mật tự đặt, ví dụ mật khẩu dài 20+ ký tự |

6. Bấm **Create Web Service**. Không chọn gói trả phí khi Render hỏi.

7. Đợi build xong. URL dạng `https://shopxoainon-web.onrender.com`

8. Mở `/health` trên trình duyệt. JSON phải có `"status": "ok"` và `"dry_run": true`. Lần đầu có thể chờ ~1 phút vì Free ngủ sau 15 phút.

## B. Gọi đồng bộ thủ công (vẫn dry-run)

PowerShell:

```powershell
$env:TOKEN = "SYNC_TOKEN_BAN_DAT"
Invoke-WebRequest -Uri "https://shopxoainon-web.onrender.com/sync-sources" -Method POST -Headers @{ "X-Sync-Token" = $env:TOKEN }
```

Không có token → HTTP 403. Lệnh này **không ghi Google** (`DRY_RUN=true` bị khóa trong code).

## C. MENU: GitHub Actions ghi, Render không ghi

Render Free không có Cron (Cron là gói trả phí). Không bật scheduler trên Render.

- `ENABLE_SCHEDULED_SYNC=false`: Web Service không chạy thread MENU.
- `ENABLE_SCHEDULED_MENU_LIVE=false`: mọi lần gọi `/sync-menu` trên Render chỉ dry-run.
- `.github/workflows/sync-menu.yml` là scheduler ghi MENU duy nhất. Cron `*/5 * * * *` có thể trễ. Job chạy preflight, backup tab `_MENU_SYNC_BACKUP`, rồi `DRY_RUN=false`.
- `ENABLE_PURCHASE=false` và `ENABLE_TELEGRAM=false` trong workflow.
- Không dùng `setInterval` trình duyệt và không ping liên tục để giả Cron.

Workflow `.github/workflows/sync-sources.yml` vẫn đọc nguồn mỗi ngày với `DRY_RUN=true`; đây là lịch chỉ đọc, không ghi Google.

## C2. Lịch đọc nguồn hàng ngày

Không cần Render Cron. Workflow `.github/workflows/sync-sources.yml` chạy `17 1 * * *` UTC.

1. Repo GitHub → **Settings → Secrets and variables → Actions**
2. Thêm cùng các secret: `SHOP_XOAI_NON_API_KEY`, `NASTELE_API_KEY`, `GOOGLE_SPREADSHEET_ID`, `GOOGLE_DOCUMENT_ID`, `GOOGLE_CREDENTIALS_JSON`
3. **Actions → sync-sources → Run workflow** để thử ngay

## D. Google

Chia sẻ quyền **Người chỉnh sửa** email service account (`client_email` trong JSON) cho spreadsheet catalog, Nastele, file tổng hợp và Google Doc.

## Nếu build lỗi

| Hiện tượng | Việc làm |
| --- | --- |
| Render hỏi thẻ / Starter | Quay lại, chọn **Free** instance. Đừng dùng Blueprint Cron. |
| Health 502 | Xem log: Start Command phải là `PYTHONPATH=src python scripts/start_web.py` |
| `ModuleNotFoundError: shop_integration` | Sửa Start Command thành `PYTHONPATH=src python scripts/start_web.py`. Thêm biến `PYTHONPATH` = `src`. Manual Deploy lại. |
| CI GitHub đỏ | Sửa test trước. `autoDeployTrigger: checksPass` không deploy commit fail |
