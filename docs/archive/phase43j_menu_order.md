# Phase 4.3J — Gom sản phẩm cùng loại về cạnh nhau trên MENU

Mục tiêu: trên tab `MENU`, các sản phẩm cùng loại (ChatGPT, Canva, Google Drive,
Grok, Capcut, MUSE, ...) nằm liền nhau để người xem dễ chọn.

Ràng buộc đã giữ nguyên:

- **Không** thêm cột, không thêm sheet, không thêm tính năng mới.
- **Không** sửa dữ liệu: ID, tên, mô tả, thời hạn, bảo hành, giá, tồn kho, trạng thái
  đi cùng nhau vì cả khối `A:H` của một dòng được chuyển nguyên vẹn.
- **Không** ghi vào `J:M` (panel bảo hành/chính sách), không đụng merge, dropdown
  `K38`, công thức `J39/J40`, conditional format `H7:H45`, protected range, banding.
- Chỉ hoán đổi vị trí các dòng **trong cùng một danh mục** của MENU; sản phẩm không
  nhảy sang danh mục khác.

## 1. Cách hoạt động

`src/shop_integration/menu_order.py`:

1. `fold_menu_text` bỏ dấu tiếng Việt (NFD, `đ→d`), viết thường, gộp khoảng trắng.
2. `menu_family` dò họ sản phẩm theo `_FAMILY_RULES`/`_FAMILY_TERMS`. Không khớp
   luật nào thì lấy `~` + hai token đầu, nhờ vậy hai tên lạ khác nhau không bị gộp.
3. `cluster_menu_rows` gom dòng theo họ, **giữ thứ tự xuất hiện đầu tiên**, nên thứ
   tự cụm ổn định qua các lượt sync; cụm được dựng lại mỗi lượt nên sản phẩm mới
   cùng họ tự vào đúng cụm.
4. `plan_menu_order` chỉ lấy dòng sản phẩm thật, bỏ dòng có công thức/merge trong
   `A:H`, bỏ dòng bị `MenuCapacity` khóa (`row_allowed`, `selector_covers`,
   `cell_blocked`). Dòng không sắp xếp được chỉ bị bỏ khỏi vùng, không bị sửa.
5. `apply_menu_order` ghi từng ô `A:H` bằng `RAW` (batch), rồi **đọc lại và so**;
   lệch thì raise `MenuLayoutError` kèm hướng dẫn khôi phục từ `backups/`.

`src/shop_integration/menu_sync.py`: `_group_similar_menu_rows` chạy ngay sau khi
làm sạch mô tả, trong cùng lượt sync MENU:

- `MENU_GROUP_SIMILAR=false` thì bỏ qua và ghi chú.
- Không đọc được cấu trúc/MENU thì ghi chú rồi trả `0` — **không chặn sync chính**.
- `DRY_RUN=true` chỉ ghi chú “Chạy thử chưa ghi”.
- Đã đúng cụm thì im lặng, không ghi ô nào (idempotent).

## 2. Kết quả chạy thử (đọc live, chưa ghi)

| Hạng mục | Giá trị |
| --- | --- |
| Dòng trong vùng sắp xếp | 28 |
| Cụm nhiều sản phẩm | 6 |
| Dòng phải đổi vị trí | 27 |
| Ô sẽ ghi | 216 (27 × 8) |

Cụm tìm được: ChatGPT ×3, Canva ×2, Google Drive ×4, Grok ×3, Capcut ×2, MUSE ×3.
Sản phẩm lẻ giữ nguyên: `test api`, Memrise, Meitu, Wink, Duolingo, Nord VPN, Figma,
Microsoft Office, Gemini, Adobe, AI PRO.

## 3. Kết quả ghi thật

Ảnh chụp trước khi ghi: `backups/menu-before-20261126-111724.json` (toàn bộ `A1:M200`).
Báo cáo: `backups/menu-group-run-20261126-111724.txt`. Ảnh chụp sau: `backups/menu-after-20261126-111724.json`.

| Kiểm tra | Kết quả |
| --- | --- |
| Ô đã ghi | 216 (`A:H`, `RAW`) |
| Sản phẩm trước / sau | 29 / 29 (bộ đếm của script tính cả dòng tiêu đề 6; 28 dòng sản phẩm thật trong vùng sắp xếp) |
| Thiếu / thêm theo ID | 0 / 0 |
| Nội dung lệch theo ID | 0 |
| `J:M` giữ nguyên | có (`True`) |
| `menu_order_report` sau khi ghi | `[]` |
| Họ nào cũng nằm liền nhau | có |
| Lượt chạy lại | “đã nằm đúng cụm, không đổi vị trí”, 0 ô ghi |

Thứ tự sau khi gom: ChatGPT (8–10), Canva (11–12), Google Drive (13–16), Grok
(18–20), Capcut (21–22), MUSE (28–30); các sản phẩm lẻ nằm ở 17, 23–27, 31–35.

## 4. Kiểm thử

`tests/test_phase43j_menu_order.py` — 32 test, toàn bộ dùng gateway giả:

- nhận diện họ: tên có dấu, viết hoa, `CHAT GPT`, tên lạ rơi vào `~` + 2 token;
- cụm giữ thứ tự xuất hiện, kích thước cụm, tiêu đề cụm;
- plan: đúng thứ tự mới, `moved`, không mất/không trùng dòng, `identity` khi đã đúng;
- bỏ dòng có công thức và dòng bị `MenuCapacity` khóa;
- `menu_order_cells` chỉ sinh ô `A:H` của dòng đích, bỏ dòng đã đúng chỗ;
- `apply_menu_order`: `J:M` không đổi, payload theo ID không đổi, đọc lại lệch thì
  raise `MenuLayoutError`, đã đúng thứ tự thì không ghi;
- `MENU_GROUP_SIMILAR` bật/tắt; hook sync ở `live=False`, `live=True` và lượt lặp.

## 5. Vận hành

- Mặc định bật. Tắt tạm: `MENU_GROUP_SIMILAR=false`.
- Muốn khôi phục thứ tự cũ: chép lại `A:H` từ `backups/menu-before-<stamp>.json`.
- Mỗi lượt sync vẫn chụp vùng MENU bền (`MENU_REQUIRE_DURABLE_BACKUP=true`) trước khi ghi.
