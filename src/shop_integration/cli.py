"""Lệnh dòng lệnh cho các thao tác đọc, báo giá tường minh và đồng bộ."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from shop_integration.api_client import ShopApiClient
from shop_integration.backup_service import (
    BackupService,
    apply_restore,
    assess_live_sync_readiness,
    latest_backup,
    plan_restore,
    restore_guide,
)
from shop_integration.combined_quote import render_quote_layout
from shop_integration.config import SINGLE_SPREADSHEET_ENV, Settings
from shop_integration.docs_service import DocsService, GoogleDocsGateway
from shop_integration.errors import ApiError, BackupError, ConfigError, GoogleWorkspaceError, IntegrationError
from shop_integration.logging_utils import configure_logging
from shop_integration.pricing import policy_from_settings, quote_cost
from shop_integration.product_service import (
    ProductService,
    price_bounds,
    product_stock_label,
    product_sync_status,
    selling_bounds,
)
from shop_integration.sheet_layout import LEGACY_TAB_NOTES
from shop_integration.sheets_service import GoogleSheetsGateway, SheetsService
from shop_integration.sync_service import SyncService
from shop_integration.suppliers.nastele_sheet import NasteleSheetWriter
from shop_integration.suppliers.source_sync import (
    SourceSheetWriter,
    SourceSyncService,
    collect_suppliers,
)
from shop_integration.time_utils import format_timestamp, now_vietnam
from shop_integration.workspace import open_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shop-xoai-non",
        description="Đồng bộ danh mục Shop Xoài Non sang Google Sheets và Google Docs. Không tạo đơn.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ping = commands.add_parser("ping", help="Kiểm tra kết nối bằng GET /me")
    ping.set_defaults(func=command_ping)

    products = commands.add_parser("products", help="Đếm sản phẩm bằng GET /products")
    products.add_argument("--search", default="", help="Lọc theo tên, đúng tham số search của API")
    products.add_argument("--list", action="store_true", help="In mã và tên, tối đa 50 dòng")
    products.set_defaults(func=command_products)

    product = commands.add_parser("product", help="Xem một sản phẩm bằng GET /products/{id}")
    product.add_argument("product_id")
    product.set_defaults(func=command_product)

    wallet = commands.add_parser("wallet", help="Đọc GET /wallet/balance, schema chưa được tài liệu khóa")
    wallet.set_defaults(func=command_wallet)

    orders = commands.add_parser("orders", help="Liệt kê mã đơn và trạng thái")
    orders.set_defaults(func=command_orders)

    order = commands.add_parser("order", help="Xem mã và trạng thái của một đơn")
    order.add_argument("order_id")
    order.set_defaults(func=command_order)

    quote = commands.add_parser("quote", help="Báo giá một biến thể. Không trừ ví.")
    quote.add_argument("--product-id", required=True)
    quote.add_argument("--variant-id", required=True)
    quote.add_argument("--quantity", type=int, default=1)
    quote.set_defaults(func=command_quote)

    quote_layout = commands.add_parser(
        "quote-layout",
        help="In bảng cột thống nhất của file catalog và file báo giá tổng hợp. Không gọi mạng.",
    )
    quote_layout.set_defaults(func=command_quote_layout)

    sync = commands.add_parser("sync", help="Đồng bộ danh mục sang spreadsheet có sẵn")
    sync.set_defaults(func=command_sync)

    sources = commands.add_parser("sources", help="Đọc Shop Xoài Non và Nastele, in đối chiếu, không ghi Sheets")
    sources.set_defaults(func=command_sources)

    sync_sources = commands.add_parser(
        "sync-sources",
        help="Đồng bộ SO_SANH_NGUON, BANG_GIA_TOI_UU và CAN_GHEP_SAN_PHAM. DRY_RUN không ghi.",
    )
    sync_sources.set_defaults(func=command_sync_sources)

    sync_menu = commands.add_parser(
        "sync-menu",
        help="Ghi BAO_GIA từ API shop, đọc NASTELEID, cập nhật MENU. Không ghi dữ liệu NASTELEID.",
    )
    sync_menu.set_defaults(func=command_sync_menu)

    menu_baseline = commands.add_parser(
        "menu-baseline",
        help="Chụp cấu trúc MENU (merge, công thức, dropdown, khổ cột/dòng). Chỉ đọc.",
    )
    menu_baseline.add_argument(
        "--out",
        default="",
        help="File JSON baseline. Mặc định backups/menu-baseline-<thời gian>.json",
    )
    menu_baseline.add_argument(
        "--compare",
        default="",
        help="So sánh với baseline đã lưu. Lệch thì trả mã 1 và không ghi.",
    )
    menu_baseline.set_defaults(func=command_menu_baseline)

    menu_audit = commands.add_parser(
        "menu-audit",
        help="Đọc MENU và đối chiếu ProductID/VariantID, vùng mở rộng A:H. Chỉ đọc.",
    )
    menu_audit.add_argument(
        "--json",
        default="",
        help="Ghi báo cáo JSON ra file local. Không ghi Google Sheets.",
    )
    menu_audit.set_defaults(func=command_menu_audit)

    menu_migrate = commands.add_parser(
        "menu-migrate",
        help="Lập kế hoạch sửa BAO_GIA/BAO_GIA_TONG_HOP và MENU. Mặc định chỉ chạy thử.",
    )
    menu_migrate.add_argument(
        "--json",
        default="",
        help="Ghi báo cáo migration JSON ra file local. Không ghi Google Sheets.",
    )
    menu_migrate.add_argument(
        "--backup-dir",
        default="",
        help="Thư mục gốc chứa snapshot migration. Mặc định backups/.",
    )
    menu_migrate.add_argument(
        "--baseline",
        default="backups/menu-baseline-phase43b.json",
        help="Baseline MENU chính thức để so sánh trước khi migration.",
    )
    menu_migrate.add_argument(
        "--apply",
        action="store_true",
        help="Ghi Google sau khi backup READY. Bắt buộc đi cùng --confirm.",
    )
    menu_migrate.add_argument(
        "--confirm",
        action="store_true",
        help="Xác nhận ghi thật. Không có cờ này thì luôn dry-run.",
    )
    menu_migrate.set_defaults(func=command_menu_migrate)

    report = commands.add_parser("report", help="In báo cáo và ghi vào Google Doc khi không chạy thử")
    report.set_defaults(func=command_report)

    backup = commands.add_parser("backup", help="Sao lưu Sheets và Docs ra thư mục backups")
    backup.set_defaults(func=command_backup)

    check = commands.add_parser("backup-check", help="Kiểm tra checksum backup và in hướng dẫn khôi phục")
    check.add_argument("--path", default="", help="Thư mục một snapshot. Mặc định là backup mới nhất.")
    check.set_defaults(func=command_backup_check)

    readiness = commands.add_parser(
        "backup-readiness",
        help="Đánh giá backup local có đủ cho live sync trên GitHub Actions hay không. Không ghi Google.",
    )
    readiness.set_defaults(func=command_backup_readiness)

    restore_plan = commands.add_parser("restore-plan", help="Đối chiếu backup với file Google. Chỉ đọc.")
    restore_plan.add_argument("--path", default="")
    restore_plan.set_defaults(func=command_restore_plan)

    restore = commands.add_parser("restore", help="Khôi phục khi không còn xung đột. Cần --confirm mới ghi.")
    restore.add_argument("--path", default="")
    restore.add_argument("--confirm", action="store_true")
    restore.set_defaults(func=command_restore)

    config_check = commands.add_parser(
        "config-check",
        help="In cấu hình hiệu lực, biến cũ bị bỏ qua và tab LEGACY. Không gọi mạng.",
    )
    config_check.set_defaults(func=command_config_check)
    return parser


def _enable_utf8_stdio() -> None:
    """Console Windows cp1252 không in được tiếng Việt. Ép UTF-8 khi có thể."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    _enable_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings()
    except ValidationError:
        print("Lỗi cấu hình: giá trị môi trường không hợp lệ.", file=sys.stderr)
        return 2
    configure_logging(settings.secret_values())
    try:
        return int(args.func(args, settings))
    except ConfigError as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return 2
    except (ApiError, GoogleWorkspaceError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    except IntegrationError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger("shop_integration").exception("Lỗi không xác định")
        print(
            "Đã xảy ra lỗi không xác định. Chi tiết nằm trong nhật ký đã được lọc bí mật.",
            file=sys.stderr,
        )
        return 1


def command_ping(args: argparse.Namespace, settings: Settings) -> int:
    del args
    with ShopApiClient(settings) as client:
        summary = client.get_me()
    print("Kết nối Shop Xoài Non thành công (GET /me, HTTP 200).")
    print("Khóa JSON tầng ngoài: " + _join(summary.top_level_keys))
    print("Khóa trong data: " + _join(summary.data_keys))
    print("Không in giá trị hồ sơ khách.")
    return 0


def command_products(args: argparse.Namespace, settings: Settings) -> int:
    service = ProductService()
    with ShopApiClient(settings) as client:
        raw_items = client.list_all_products(search=args.search or None)
        notices = list(client.notices)
    products, warnings = service.parse_many(raw_items)
    print(f"Số sản phẩm: {len(products)}")
    print(f"Số biến thể: {sum(len(product.variants) for product in products)}")
    for notice in notices:
        print(notice)
    for warning in warnings:
        print(f"Cảnh báo: {warning.message}")
    if args.list:
        for product in products[:50]:
            print(f"{product.id}\t{product.title or ''}")
        if len(products) > 50:
            print(f"Còn {len(products) - 50} sản phẩm. Chỉ in 50 dòng đầu.")
    return 0


def command_product(args: argparse.Namespace, settings: Settings) -> int:
    with ShopApiClient(settings) as client:
        raw = client.get_product(args.product_id)
    products, warnings = ProductService().parse_many([raw])
    if not products:
        print("Không đọc được sản phẩm từ phản hồi.", file=sys.stderr)
        return 1
    product = products[0]

    policy = policy_from_settings(settings)
    low, high = price_bounds(product)
    sell_low, sell_high = selling_bounds(product, policy)
    print(f"Product ID: {product.id}")
    print(f"Tên sản phẩm: {product.title or ''}")
    print(f"Trạng thái còn hàng: {product_stock_label(product)}")
    print(f"Giá gốc thấp nhất (VND): {_money(low)}")
    print(f"Giá gốc cao nhất (VND): {_money(high)}")
    print(f"Giá bán thấp nhất (VND): {_money(sell_low)}")
    print(f"Giá bán cao nhất (VND): {_money(sell_high)}")
    print(f"Số biến thể: {len(product.variants)}")
    print(f"Trạng thái dữ liệu: {product_sync_status(product)}")
    for variant in product.variants:
        quote = quote_cost(variant.price_vnd, policy)
        print(
            "Biến thể: "
            f"{variant.id} | giá gốc={_money(quote.cost)} | giá bán={_money(quote.selling)} | "
            f"còn hàng={_bool_text(variant.in_stock)} | số lượng={_money(variant.available_quantity)}"
        )
    for warning in warnings:
        print(f"Cảnh báo: {warning.message}")
    return 0


def command_wallet(args: argparse.Namespace, settings: Settings) -> int:
    del args
    with ShopApiClient(settings) as client:
        wallet = client.get_wallet()
    print("Schema GET /wallet/balance chưa được OpenAPI mô tả.")
    print("Khóa trong data: " + _join(wallet.data_keys))
    if wallet.amount is None:
        print("Không suy ra số dư vì phản hồi không có đúng một số nguyên.")
    else:
        print(
            "Số nguyên duy nhất tìm thấy "
            f"tại khóa '{wallet.amount_key}': {wallet.amount}. "
            "Giá trị này chưa được tài liệu xác nhận."
        )
    return 0


def command_orders(args: argparse.Namespace, settings: Settings) -> int:
    del args
    with ShopApiClient(settings) as client:
        orders = client.list_orders()
    if not orders:
        print("Không có đơn hàng trong phản hồi.")
        return 0
    for order in orders:
        print(f"{order.order_id or ''}\t{order.status or ''}")
    return 0


def command_order(args: argparse.Namespace, settings: Settings) -> int:
    with ShopApiClient(settings) as client:
        order = client.get_order(args.order_id)
    print(f"Mã đơn: {order.order_id or ''}")
    print(f"Trạng thái: {order.status or ''}")
    return 0


def command_quote_layout(args: argparse.Namespace, settings: Settings) -> int:
    del args, settings
    print(render_quote_layout(), end="")
    return 0


def command_quote(args: argparse.Namespace, settings: Settings) -> int:
    with ShopApiClient(settings) as client:
        quote = client.quote(args.product_id, args.variant_id, args.quantity)
    print(f"Product ID: {quote.product_id or ''}")
    print(f"Số lượng: {_money(quote.quantity)}")
    print(f"Đơn giá (VND): {quote.unit_price}")
    print(f"Thành tiền (VND): {quote.total}")
    print(f"Số lượng khả dụng: {_money(quote.available_quantity)}")
    print(f"Có thể mua: {_bool_text(quote.can_purchase)}")
    print("Lệnh này chỉ báo giá. Không tạo đơn và không trừ ví.")
    return 0


def command_config_check(args: argparse.Namespace, settings: Settings) -> int:
    del args
    print(f"{SINGLE_SPREADSHEET_ENV}: {settings.spreadsheet_id or '(trống)'}")
    print("GOOGLE_DOCUMENT_ID: " + ("có" if settings.google_document_id else "không"))
    print(f"GOOGLE_CREDENTIALS_FILE: {settings.google_credentials_file}")
    print(f"DRY_RUN: {str(settings.dry_run).lower()}")
    print(
        "ENABLE_PURCHASE: "
        f"{str(settings.enable_purchase).lower()} "
        f"(purchase_allowed={str(settings.purchase_allowed).lower()})"
    )
    print(
        "ENABLE_TELEGRAM: "
        f"{str(settings.enable_telegram).lower()} "
        f"(telegram_active={str(settings.telegram_active).lower()})"
    )
    print(f"COMPARISON_QUANTITY: {settings.comparison_quantity}")
    print(f"PREFERRED_SUPPLIER_ID: {settings.preferred_supplier_id}")
    legacy = settings.legacy_spreadsheet_env()
    print("Biến spreadsheet cũ bị bỏ qua: " + (", ".join(legacy) if legacy else "không"))
    unused = settings.unused_env()
    print("Biến đã đổi tên còn trong môi trường: " + (", ".join(unused) if unused else "không"))
    print("Tab LEGACY: " + ", ".join(LEGACY_TAB_NOTES))
    for warning in settings.config_warnings():
        print(f"Cảnh báo: {warning}")
    return 0


def command_backup(args: argparse.Namespace, settings: Settings) -> int:
    del args
    service, sheets, docs = _backup_stack(settings)
    manifest = service.create_and_verify()
    bundle = service.load(Path(manifest["directory"]))
    _print_backup_summary(manifest, bundle["spreadsheet"])
    print("Backup không phải bản sao hoàn chỉnh.")
    return 0


def command_backup_readiness(args: argparse.Namespace, settings: Settings) -> int:
    del args, settings
    readiness = assess_live_sync_readiness()
    print(f"Backup readiness: {readiness['status']}")
    print(f"Scheduled live sync: {readiness['github_actions_live_sync']}")
    print(f"Kho: {readiness['storage']}")
    print("Backup hoàn chỉnh: không")
    print("Tab ngoài vùng quản lý và Trang tính1: được giữ khi khôi phục.")
    print(readiness["proposal"])
    print(restore_guide())
    return 0


def command_backup_check(args: argparse.Namespace, settings: Settings) -> int:
    directory = Path(args.path) if args.path else latest_backup()
    service = BackupService(
        _OfflineSource(),
        _OfflineSource(),
        "spreadsheet",
        "document",
        secrets=settings.secret_values(),
    )
    manifest = service.verify(directory)
    bundle = service.load(directory)
    _print_backup_summary(manifest, bundle["spreadsheet"])
    print(restore_guide())
    return 0


def command_restore_plan(args: argparse.Namespace, settings: Settings) -> int:
    directory = Path(args.path) if args.path else latest_backup()
    service, sheets, docs = _backup_stack(settings)
    bundle = service.load(directory)
    current_sheet = sheets.backup_snapshot(settings.spreadsheet_id)
    current_doc = docs.backup_snapshot(settings.google_document_id)
    plan = plan_restore(bundle, current_sheet, str(current_doc.get("plain_text") or ""))
    _print_plan(plan)
    return 1 if plan["blocking"] else 0


def command_restore(args: argparse.Namespace, settings: Settings) -> int:
    directory = Path(args.path) if args.path else latest_backup()
    service, sheets, docs = _backup_stack(settings)
    bundle = service.load(directory)
    current_sheet = sheets.backup_snapshot(settings.spreadsheet_id)
    current_doc = docs.get_document(settings.google_document_id)
    if not args.confirm:
        plan = plan_restore(bundle, current_sheet, _doc_text(current_doc))
        _print_plan(plan)
        print("Chưa ghi khôi phục vì thiếu --confirm.")
        return 0
    apply_restore(
        bundle,
        current_sheet,
        current_doc,
        confirm=True,
        sheets_gateway=sheets,
        docs_gateway=docs,
        spreadsheet_id=settings.spreadsheet_id,
        document_id=settings.google_document_id,
    )
    print("Đã ghi khôi phục trong phạm vi cho phép. Định dạng và tab người dùng không được phục hồi.")
    return 0


def command_sources(args: argparse.Namespace, settings: Settings) -> int:
    del args
    return _source_command(settings, write_sheets=False)


def command_sync_sources(args: argparse.Namespace, settings: Settings) -> int:
    del args
    return _source_command(settings, write_sheets=True)


def command_sync_menu(args: argparse.Namespace, settings: Settings) -> int:
    del args
    from shop_integration.menu_sync import run_menu_sync

    result = run_menu_sync(settings, live=not settings.dry_run)
    print(result.text)
    return 0 if result.status in {"ok", "locked", "partial"} else 1


def command_menu_baseline(args: argparse.Namespace, settings: Settings) -> int:
    """Chỉ đọc MENU. Không gọi API shop, không ghi Google Sheets."""

    from shop_integration.combined_quote import MENU_HEADERS
    from shop_integration.menu_layout import (
        MENU_TAB,
        load_menu_baseline,
        menu_baseline_from_structure,
        menu_structure_diff,
        save_menu_baseline,
    )

    if not settings.spreadsheet_id:
        raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
    workspace = open_workspace(settings, sheets=True)
    gateway = workspace.sheets
    if gateway is None:
        raise ConfigError("Thiếu kết nối Google Sheets.")
    structure = gateway.get_sheet_structure(settings.spreadsheet_id, MENU_TAB)
    captured_at = format_timestamp(now_vietnam())
    baseline = menu_baseline_from_structure(
        structure,
        MENU_HEADERS,
        captured_at=captured_at,
    )
    print(
        f"MENU: {baseline.data_rows} dòng, {baseline.data_columns} cột, "
        f"header dòng {baseline.header_row}."
    )
    print(
        f"Layout: {baseline.merge_count} vùng gộp, {len(baseline.formulas)} công thức, "
        f"{len(baseline.data_validations)} dropdown, "
        f"{baseline.conditional_format_count} rule điều kiện."
    )
    print(
        f"Khổ cột: {len(baseline.column_widths)} cột; "
        f"chiều cao: {len(baseline.row_heights)} dòng; "
        f"ô khóa (merge/công thức): {len(baseline.protected_cells)}."
    )
    print(f"Fingerprint: {baseline.fingerprint()}")
    if args.compare:
        saved = load_menu_baseline(args.compare)
        differences = menu_structure_diff(saved, baseline)
        if differences:
            print(f"LỆCH baseline '{args.compare}' ({len(differences)} mục):")
            for item in differences:
                print(f"- {item}")
            return 1
        print(f"Khớp baseline '{args.compare}'.")
        return 0
    target = Path(args.out) if args.out else (
        Path("backups") / f"menu-baseline-{now_vietnam().strftime('%Y%m%d-%H%M%S')}.json"
    )
    saved_to = save_menu_baseline(baseline, target)
    print(f"Đã lưu baseline: {saved_to}")
    return 0


def command_menu_audit(args: argparse.Namespace, settings: Settings) -> int:
    """Chỉ đọc MENU + API shop. Không ghi Google Sheets, không tạo đơn."""

    import json

    from shop_integration.combined_quote import MENU_HEADERS, shop_quote_rows
    from shop_integration.menu_audit import read_menu_audit
    from shop_integration.menu_sync import _load_shop_products
    from shop_integration.pricing import policy_from_settings

    if not settings.spreadsheet_id:
        raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
    workspace = open_workspace(settings, sheets=True)
    gateway = workspace.sheets
    if gateway is None:
        raise ConfigError("Thiếu kết nối Google Sheets.")
    products, notes = _load_shop_products(settings, None)
    source_rows = shop_quote_rows(products, policy_from_settings(settings))
    report = read_menu_audit(
        gateway,
        settings.spreadsheet_id,
        MENU_HEADERS,
        source_rows,
    )
    print(report.text())
    for note in notes:
        print(note)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Đã lưu báo cáo audit: {target}")
    return 0


def command_menu_migrate(args: argparse.Namespace, settings: Settings) -> int:
    """Lập kế hoạch sửa nguồn + MENU. DRY_RUN=true thì không ghi Google."""

    import json

    from shop_integration.combined_quote import shop_quote_rows
    from shop_integration.menu_migration import run_migration
    from shop_integration.menu_sync import _load_shop_products
    from shop_integration.pricing import policy_from_settings

    if not settings.spreadsheet_id:
        raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
    workspace = open_workspace(settings, sheets=True)
    gateway = workspace.sheets
    if gateway is None:
        raise ConfigError("Thiếu kết nối Google Sheets.")
    if args.apply and not args.confirm:
        raise ConfigError("--apply phải đi cùng --confirm. Không ghi Google.")
    products, notes = _load_shop_products(settings, None)
    shop_rows = shop_quote_rows(products, policy_from_settings(settings))
    live = bool(args.apply and args.confirm and not settings.dry_run)
    if args.apply and settings.dry_run:
        print("DRY_RUN=true: bỏ qua --apply, chỉ chạy thử.")
    result = run_migration(
        gateway,
        settings.spreadsheet_id,
        shop_rows,
        live=live,
        backup_root=args.backup_dir or None,
        baseline_path=args.baseline,
        secrets=settings.secret_values(),
    )
    print(result.text())
    for note in notes:
        print(note)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Đã lưu báo cáo migration: {target}")
    return 0 if result.status in {"PLANNED", "UP_TO_DATE", "APPLIED"} else 1


def _source_command(settings: Settings, *, write_sheets: bool) -> int:
    stamp = format_timestamp(now_vietnam())
    shop, nastele = collect_suppliers(settings, stamp)
    writer = None
    nastele_writer = None
    docs_service = None
    approvals = None
    approval_warnings: list[str] = []
    backup = None
    if write_sheets and not settings.dry_run:
        if not settings.spreadsheet_id or not settings.google_document_id:
            raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID hoặc GOOGLE_DOCUMENT_ID.")
        if not settings.google_credentials_file:
            raise ConfigError("Thiếu GOOGLE_CREDENTIALS_FILE.")
    if settings.spreadsheet_id and settings.google_credentials_file:
        try:
            write_live = write_sheets and not settings.dry_run
            workspace = open_workspace(settings, sheets=True, docs=write_live)
            gateway = workspace.sheets
            if gateway is None:
                raise ConfigError("Thiếu kết nối Google Sheets.")
            writer = SourceSheetWriter(
                gateway,
                settings.spreadsheet_id,
                dry_run=settings.dry_run or not write_sheets,
                secrets=settings.secret_values(),
            )
            approvals, approval_warnings = writer.read_approvals()
            nastele_writer = NasteleSheetWriter(
                gateway,
                settings.spreadsheet_id,
                dry_run=settings.dry_run or not write_sheets,
                secrets=settings.secret_values(),
            )
            if write_live:
                docs_gateway = workspace.docs
                if docs_gateway is None:
                    raise ConfigError("Thiếu kết nối Google Docs.")
                docs_service = DocsService(
                    docs_gateway,
                    settings.google_document_id,
                    dry_run=False,
                    secret=settings.shop_xoai_non_api_key.get_secret_value(),
                )
                backup = BackupService(
                    gateway,
                    docs_gateway,
                    settings.spreadsheet_id,
                    settings.google_document_id,
                    secrets=settings.secret_values(),
                )
        except IntegrationError as exc:
            if write_sheets and not settings.dry_run:
                raise
            writer = None
            nastele_writer = None
            docs_service = None
            approvals = []
            approval_warnings = [f"Không đọc được bảng duyệt: {exc}"]
    elif write_sheets and not settings.dry_run:
        raise ConfigError("Thiếu thông tin Google để ghi tab nguồn.")
    plan = SourceSyncService(
        settings,
        shop,
        nastele,
        writer=writer,
        nastele_writer=nastele_writer,
        docs=docs_service,
        backup=backup,
        updated_at=stamp,
        approvals=approvals,
        approval_warnings=approval_warnings,
    ).run(write_sheets=write_sheets)
    print(plan.text)
    if (shop.error and not shop.blocked) or (nastele.error and not nastele.blocked):
        return 1
    return 0


def command_sync(args: argparse.Namespace, settings: Settings) -> int:
    del args
    report = _execute(settings, write_sheets=True, write_doc=bool(settings.google_document_id))
    print(report.text)
    return 0


def command_report(args: argparse.Namespace, settings: Settings) -> int:
    del args
    report = _execute(settings, write_sheets=False, write_doc=True)
    print(report.text)
    return 0


def _execute(settings: Settings, *, write_sheets: bool, write_doc: bool) -> object:
    needs_google = (write_sheets and settings.spreadsheet_id and not settings.dry_run) or (
        write_doc and settings.google_document_id and not settings.dry_run
    )
    if write_sheets and not settings.dry_run and not settings.spreadsheet_id:
        raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
    if write_doc and not settings.dry_run and not settings.google_document_id:
        raise ConfigError("Thiếu GOOGLE_DOCUMENT_ID.")
    if needs_google and not settings.google_credentials_file:
        raise ConfigError("Thiếu GOOGLE_CREDENTIALS_FILE.")

    want_sheets = bool(settings.spreadsheet_id and settings.google_credentials_file)
    want_docs = bool(settings.google_document_id and settings.google_credentials_file)
    secret = settings.shop_xoai_non_api_key.get_secret_value()
    sheets_gateway = None
    docs_gateway = None
    if want_sheets or want_docs:
        workspace = open_workspace(settings, sheets=want_sheets, docs=want_docs)
        sheets_gateway = workspace.sheets
        docs_gateway = workspace.docs
    sheets = None
    docs = None
    if want_sheets and sheets_gateway is not None:
        sheets = SheetsService(
            sheets_gateway,
            settings.spreadsheet_id,
            dry_run=settings.dry_run,
            secret=secret,
        )
    if want_docs and docs_gateway is not None:
        docs = DocsService(
            docs_gateway,
            settings.google_document_id,
            dry_run=settings.dry_run,
            secret=secret,
        )
    backup = None
    if not settings.dry_run and (write_sheets or write_doc):
        if sheets_gateway is None or docs_gateway is None:
            raise ConfigError("Thiếu thông tin xác thực Google. Dừng trước khi ghi.")
        backup = BackupService(
            sheets_gateway,
            docs_gateway,
            settings.spreadsheet_id,
            settings.google_document_id,
            secrets=settings.secret_values(),
        )
    with ShopApiClient(settings) as client:
        service = SyncService(
            settings,
            client,
            ProductService(),
            sheets_service=sheets,
            docs_service=docs,
            backup_service=backup,
        )
        return service.run(write_sheets=write_sheets, write_doc=write_doc)


class _OfflineSource:
    def backup_snapshot(self, spreadsheet_id: str) -> dict:
        del spreadsheet_id
        raise BackupError("Lệnh kiểm tra backup không gọi Google.")


def _backup_stack(settings: Settings) -> tuple[BackupService, GoogleSheetsGateway, GoogleDocsGateway]:
    if not settings.spreadsheet_id or not settings.google_document_id:
        raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID hoặc GOOGLE_DOCUMENT_ID.")
    if not settings.google_credentials_file:
        raise ConfigError("Thiếu GOOGLE_CREDENTIALS_FILE.")
    workspace = open_workspace(settings, sheets=True, docs=True)
    sheets = workspace.sheets
    docs = workspace.docs
    if sheets is None or docs is None:
        raise ConfigError("Thiếu kết nối Google Sheets hoặc Google Docs.")
    service = BackupService(
        sheets,
        docs,
        settings.spreadsheet_id,
        settings.google_document_id,
        secrets=settings.secret_values(),
    )
    return service, sheets, docs


def _print_backup_summary(manifest: dict, spreadsheet: dict) -> None:
    print(f"Thư mục: {manifest.get('directory', '')}")
    print(f"Thời điểm: {manifest.get('created_at', '')}")
    print(f"Trạng thái: {manifest.get('status', '')}")
    print(f"Múi giờ: {manifest.get('timezone', '')}")
    print(f"Checksum spreadsheet: {str(manifest.get('spreadsheet_checksum', ''))[:12]}")
    print(f"Checksum document: {str(manifest.get('document_checksum', ''))[:12]}")
    print("Backup hoàn chỉnh: không")
    names = manifest.get("sheet_names") or []
    print("Tab: " + (", ".join(str(name) for name in names) if names else "(không có)"))
    counts = {
        str(sheet.get("title")): max(len(sheet.get("values") or []) - 1, 0)
        for sheet in spreadsheet.get("sheets", [])
        if isinstance(sheet, dict)
    }
    for name, count in counts.items():
        print(f"Số dòng dữ liệu {name}: {count}")
    print("Giới hạn:")
    for item in manifest.get("limits") or []:
        print(f"- {item}")


def _print_plan(plan: dict) -> None:
    print("Kế hoạch khôi phục. Không in nội dung ô.")
    print("Backup hoàn chỉnh: không")
    if plan["blocking"]:
        print("Xung đột chặn:")
        for item in plan["blocking"]:
            print(f"- {item}")
    else:
        print("Không có xung đột chặn.")
    print("Việc có thể làm:")
    for item in plan["actions"]:
        print(f"- {item}")
    if plan["needs_confirm"]:
        print("Cần --confirm trước khi ghi khôi phục.")
    print(restore_guide())


def _doc_text(document: dict) -> str:
    from shop_integration.docs_service import document_plain_text

    return document_plain_text(document)


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "(không có)"


def _money(value: int | None) -> str:
    return "" if value is None else str(value)


def _bool_text(value: bool | None) -> str:
    if value is True:
        return "Có"
    if value is False:
        return "Không"
    return "Không rõ"
