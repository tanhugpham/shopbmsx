from __future__ import annotations

from shop_integration.text_normalize import (
    FORMULA_PREFIX,
    decode_escaped_whitespace,
    guard_formula,
    normalize_text,
    sanitize_public_text,
    shorten_text,
)


def test_unicode_html_emoji_newlines_and_controls() -> None:
    assert normalize_text("c\\u00e0 ph\\u00ea") == "cà phê"
    assert normalize_text("A &amp; B &quot;x&quot;") == 'A & B "x"'
    assert normalize_text("OK 😀") == "OK 😀"
    assert normalize_text("&#128512;") == "😀"
    assert normalize_text("a\n\n\nb") == "a\n\nb"
    assert normalize_text("a\r\nb") == "a\nb"
    assert normalize_text("<p>Xin <b>chào</b></p><br>dòng") == "Xin chào\n\ndòng"
    assert normalize_text("a\x00b\x7f") == "ab"
    assert normalize_text("a\tb") == "a b"
    assert normalize_text("  nhiều   khoảng   trắng  ") == "nhiều khoảng trắng"


def test_empty_already_decoded_and_idempotent() -> None:
    assert normalize_text(None) == ""
    assert normalize_text("") == ""
    assert normalize_text("   \n\n  ") == ""
    original = "Tiếng Việt — Office 365, C++ và 100%"
    assert normalize_text(original) == original
    assert normalize_text(normalize_text(original)) == original
    layered = "&amp;amp; &amp; sữa"
    assert normalize_text(layered) == "& & sữa"
    assert normalize_text(normalize_text(layered)) == normalize_text(layered)


def test_does_not_rewrite_codes_or_double_decode_plain_text() -> None:
    code = "SKU-365"
    assert normalize_text(code) == code
    assert normalize_text("local_81") == "local_81"
    already = "cá phê"
    assert normalize_text(already) == already
    path = "đường dẫn C:\\users\\shop"
    assert normalize_text(path) == path


def test_escaped_newlines_are_opt_in() -> None:
    assert normalize_text("A\\nB") == "A\\nB"
    assert decode_escaped_whitespace("A\\nB\\tC") == "A\nB C"
    assert decode_escaped_whitespace("A\\r\\nB") == "A\nB"
    assert decode_escaped_whitespace(None) == ""


def test_shorten_keeps_words_and_formula_guard_is_stable() -> None:
    long_text = "một hai ba " * 30
    short = shorten_text(long_text, 20)
    assert short.endswith("…")
    assert len(short) <= 21
    assert not short.endswith(" ")
    guarded = guard_formula("=1+1")
    assert guarded == FORMULA_PREFIX + "=1+1"
    assert guard_formula(guarded) == guarded
    assert guard_formula("- mô tả") == FORMULA_PREFIX + "- mô tả"
    assert guard_formula("Cà phê 😀") == "Cà phê 😀"
    assert guard_formula(9500) == 9500


def test_sanitize_replaces_contact_handle_only() -> None:
    text = (
        "Định dạng: Email | Mật khẩu | 2FA\n"
        "Inbox admin @tiensky123 để nhận mã yêu cầu. Nhắc @tiensky123 nếu chậm."
    )
    cleaned = sanitize_public_text(text)
    assert cleaned == (
        "Định dạng: Email | Mật khẩu | 2FA\n"
        "Inbox admin @admin để nhận mã yêu cầu. Nhắc @admin nếu chậm."
    )
    assert sanitize_public_text("Inbox @tiensky1234 và @tiensky123.") == "Inbox @tiensky1234 và @admin."
    assert sanitize_public_text("Inbox @Tiensky123 ngay.") == "Inbox @admin ngay."
    assert sanitize_public_text("Inbox @tien\u200bsky123 ngay.") == "Inbox @admin ngay."
    assert sanitize_public_text("Giá 203000 — bảo hành BHF 😀") == "Giá 203000 — bảo hành BHF 😀"
    assert sanitize_public_text(203000) == 203000
    assert sanitize_public_text("") == ""
