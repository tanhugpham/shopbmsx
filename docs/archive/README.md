# Tài liệu lịch sử

Các file trong thư mục này là hồ sơ của từng phase đã hoàn thành. Chúng **không**
phải hướng dẫn vận hành và có thể đã cũ.

Nguồn hiện hành:

- [../setup_vi.md](../setup_vi.md) — cài đặt, cấu hình, triển khai
- [../architecture.md](../architecture.md) — kiến trúc, hợp đồng cột, khóa layout
- [../operations.md](../operations.md) — lịch chạy, backup, khôi phục, xử lý lỗi

| File | Nội dung | Thay thế bởi |
| --- | --- | --- |
| `phase43b_single_spreadsheet.md` | chuyển sang một spreadsheet, baseline layout MENU | `architecture.md` §3–§6 |
| `phase43d_menu_capacity.md` | audit vùng mở rộng A31:H35, lỗi lệch cột `BAO_GIA` | `architecture.md` §5, `operations.md` §7 |
| `phase43e_migration.md` | kế hoạch/áp migration 8 thao tác (đã hoàn tất) | `architecture.md` §4, `operations.md` §6 |
| `phase43f_multi_supplier.md` | catalog hai tầng, adapter nhiều nguồn | `architecture.md` §10 |
| `phase43h_final_migration_readiness.md` | checklist sẵn sàng trước khi bật auto-sync | `operations.md` §1–§3 |
| `phase43j_menu_order.md` | gom sản phẩm cùng họ trên MENU | `architecture.md` §7, `operations.md` §6.2 |
| `bao_gia_tong_hop.md` | bảng cột chi tiết của một spreadsheet dùng chung | `architecture.md` §3–§4 |
| `render_free.md` | từng bước tạo Web Service miễn phí trên Render | `setup_vi.md` §10 |
| `test_report.md` | báo cáo kiểm thử tại thời điểm 2026-11-25 (309 test) | `operations.md` §8 |
| `bao_gia_ui.html`, `bao_gia_ui.png` | mẫu giao diện báo giá | không còn dùng trong vận hành |

Quy ước: thông tin còn hiệu lực đã được chuyển sang ba tài liệu hiện hành. Nếu một
file ở đây khác tài liệu hiện hành, **tài liệu hiện hành thắng**.
