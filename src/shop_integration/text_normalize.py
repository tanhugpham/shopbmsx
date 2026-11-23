"""Chuẩn hóa text từ API. Không dịch và không viết lại nội dung."""

from __future__ import annotations

import html
import re
import unicodedata

# Một lần decode \\uXXXX khi chuỗi còn ký tự escape. Không dùng codec unicode_escape
# vì codec đó cũng nuốt \\n và \\x.
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_BREAK = re.compile(
    r"(?i)<\s*br\s*/?\s*>|</?\s*(?:p|div|li|tr|h[1-6])(?:\s[^<>]*)?>"
)
_TAG = re.compile(r"(?i)</?[a-z][a-z0-9]*\b[^<>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")
FORMULA_PREFIX = "\u200b"
_FORMULA_LEAD = "=+-@\t\r"


def decode_escaped_whitespace(value: object) -> str:
    """Đổi \\r\\n, \\n và \\t khi đó đúng là chuỗi escape. Không dùng codec unicode_escape."""

    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")


def normalize_text(value: object, *, multiline: bool = True) -> str:
    """Giải mã có kiểm soát rồi gom khoảng trắng. Gọi hai lần cho ra cùng một chuỗi."""

    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if _UNICODE_ESCAPE.search(text):
        text = _UNICODE_ESCAPE.sub(_unicode_char, text)
    # Tối đa 4 lớp thực thể. Dừng sớm khi chuỗi không còn đổi để không decode vô hạn.
    for _ in range(4):
        if not _ENTITY.search(text):
            break
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _COMMENT.sub("", text)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    text = _CONTROL.sub("", text)
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    if not multiline:
        text = text.replace("\n", " ")
    lines = [_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    if not multiline:
        return lines[0] if lines else ""
    compact = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return compact


CONTACT_HANDLE = "@tiensky123"
CONTACT_REPLACEMENT = "@admin"
_ZW = r"[\u200b\u200c\u200d\ufeff]*"
_CONTACT_HANDLE = re.compile(
    rf"@{_ZW}t{_ZW}i{_ZW}e{_ZW}n{_ZW}s{_ZW}k{_ZW}y{_ZW}1{_ZW}2{_ZW}3(?![\w])",
    re.IGNORECASE,
)


def sanitize_public_text(value: object) -> object:
    """Đổi @tiensky123 thành @admin. Giữ nguyên phần còn lại, emoji và tiếng Việt."""

    if not isinstance(value, str) or value == "":
        return value
    return _CONTACT_HANDLE.sub(CONTACT_REPLACEMENT, value)


def public_description(value: object) -> str:
    """Chuỗi mô tả public: luôn đi qua sanitize_public_text."""

    text = value if isinstance(value, str) else str(value or "")
    cleaned = sanitize_public_text(text)
    return cleaned if isinstance(cleaned, str) else text


def contains_contact_handle(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return _CONTACT_HANDLE.search(value) is not None


def shorten_text(value: object, limit: int = 180) -> str:
    """Cắt để làm mô tả ngắn. Không thay từ."""

    text = normalize_text(value, multiline=False)
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return cut.rstrip(" .,;:") + "…"


def guard_formula(value: object) -> object:
    """Chặn ô bắt đầu bằng ký tự công thức. RAW vẫn là lớp bảo vệ chính."""

    if not isinstance(value, str) or value == "":
        return value
    body = value[1:] if value.startswith(FORMULA_PREFIX) else value
    if body[:1] in _FORMULA_LEAD:
        return FORMULA_PREFIX + body
    return body


def _unicode_char(match: re.Match[str]) -> str:
    hex_text = match.group(1) or match.group(2) or ""
    code = int(hex_text, 16)
    if code < 0 or code > 0x10FFFF:
        return match.group(0)
    return chr(code)
