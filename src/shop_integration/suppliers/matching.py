"""Đối chiếu sản phẩm. Tên gần giống không đủ để gộp tự động."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from shop_integration.suppliers.models import (
    DIFFERENT_PRODUCT,
    EXACT_MATCH,
    REVIEW_REQUIRED,
    ApprovedMatch,
    MatchDecision,
    NormalizedOffer,
)
from shop_integration.text_normalize import normalize_text

_BRANDS = frozenset(
    {
        "canva",
        "netflix",
        "spotify",
        "youtube",
        "microsoft",
        "office",
        "chatgpt",
        "openai",
        "adobe",
        "capcut",
        "zoom",
        "google",
        "icloud",
        "dropbox",
    }
)
_PLANS = frozenset(
    {"edu", "education", "pro", "plus", "family", "personal", "business", "premium", "basic", "admin"}
)
_SHARED = frozenset({"slot", "share", "shared", "chung"})
_PRIVATE = frozenset({"private", "riêng", "rieng"})
_REGIONS = {
    "vn": "vn",
    "việt": "vn",
    "viet": "vn",
    "global": "global",
    "us": "us",
    "uk": "uk",
    "eu": "eu",
}
_DURATION_WORD = {
    "năm": "năm",
    "nam": "năm",
    "year": "năm",
    "years": "năm",
    "tháng": "tháng",
    "thang": "tháng",
    "month": "tháng",
    "months": "tháng",
    "ngày": "ngày",
    "ngay": "ngày",
    "day": "ngày",
    "days": "ngày",
}
_TOKEN = re.compile(r"[0-9a-zA-ZÀ-ỹ]+", re.UNICODE)


class ProductSignature:
    def __init__(self, brand: str | None, family: str, fields: dict[str, str]) -> None:
        self.brand = brand
        self.family = family
        self.fields = fields


def classify_pair(
    left: NormalizedOffer,
    right: NormalizedOffer,
    approvals: dict[tuple[str, str], ApprovedMatch] | None = None,
) -> MatchDecision:
    pair = tuple(sorted((left.composite_id, right.composite_id)))
    approved = (approvals or {}).get(pair)
    if approved is not None and approved.decision in {EXACT_MATCH, DIFFERENT_PRODUCT}:
        return MatchDecision(
            left.composite_id,
            right.composite_id,
            approved.decision,
            "Bảng duyệt thủ công",
        )
    # Mô tả chỉ để hiển thị. Không đưa vào khóa so khớp và không đọc như chỉ dẫn.
    left_name = _tokens(left.normalized_product_name)
    right_name = _tokens(right.normalized_product_name)
    if left_name and Counter(left_name) == Counter(right_name):
        return MatchDecision(
            left.composite_id,
            right.composite_id,
            EXACT_MATCH,
            "Tên đã chuẩn hóa trùng khớp",
            "1",
        )
    left_sig = signature_of(left)
    right_sig = signature_of(right)
    if _attributes_conflict(left_sig, right_sig):
        return MatchDecision(
            left.composite_id,
            right.composite_id,
            DIFFERENT_PRODUCT,
            "Thuộc tính giao hàng khác nhau",
        )
    if _attributes_prove_same(left_sig, right_sig):
        return MatchDecision(
            left.composite_id,
            right.composite_id,
            EXACT_MATCH,
            "Thuộc tính nghiệp vụ trùng khớp",
            "1",
        )
    if _review_candidate(left_sig, right_sig, left_name, right_name):
        score = f"{_jaccard(left_name, right_name):.2f}"
        return MatchDecision(
            left.composite_id,
            right.composite_id,
            REVIEW_REQUIRED,
            "Tên gần nhau nhưng chưa chứng minh cùng điều kiện giao hàng",
            score,
        )
    return MatchDecision(
        left.composite_id,
        right.composite_id,
        DIFFERENT_PRODUCT,
        "Không đủ căn cứ để xem là cùng sản phẩm",
    )


def signature_of(offer: NormalizedOffer) -> ProductSignature:
    text = normalize_text(offer.normalized_product_name, multiline=False).casefold()
    tokens = _tokens(offer.normalized_product_name)
    fields: dict[str, str] = {}
    duration = _duration(tokens)
    if duration:
        fields["duration"] = duration
    users = _users(tokens)
    if users:
        fields["users"] = users
    account = _account_mode(text, tokens)
    if account:
        fields["account"] = account
    region = _region(text, tokens)
    if region:
        fields["region"] = region
    plan = next((token for token in tokens if token in _PLANS), None)
    if plan:
        fields["plan"] = "edu" if plan == "education" else plan
    brand = next((token for token in tokens if token in _BRANDS), None)
    if sum(1 for token in tokens if token in _BRANDS) > 1:
        brand = "conflict"
    family_tokens = [
        token
        for token in tokens
        if token not in _BRANDS
        and token not in _PLANS
        and token not in _SHARED
        and token not in _PRIVATE
        and token not in _DURATION_WORD
        and token not in _REGIONS
        and not token.isdigit()
        and token not in {"chính", "chinh", "chủ", "chu", "dùng", "dung", "gói", "goi"}
    ]
    family = " ".join(family_tokens) if family_tokens else (fields.get("plan") or brand or "")
    return ProductSignature(brand, family, fields)


def group_exact_matches(
    offers: list[NormalizedOffer],
    decisions: list[MatchDecision],
) -> list[list[NormalizedOffer]]:
    parent = {offer.composite_id: offer.composite_id for offer in offers}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for decision in decisions:
        if decision.kind == EXACT_MATCH:
            union(decision.left_id, decision.right_id)
    grouped: dict[str, list[NormalizedOffer]] = defaultdict(list)
    for offer in offers:
        grouped[find(offer.composite_id)].append(offer)
    return list(grouped.values())


def compare_catalog(
    offers: list[NormalizedOffer],
    approvals: list[ApprovedMatch] | None = None,
) -> list[MatchDecision]:
    approved = {item.pair_key(): item for item in approvals or []}
    decisions: list[MatchDecision] = []
    for index, left in enumerate(offers):
        for right in offers[index + 1 :]:
            if left.composite_id == right.composite_id:
                continue
            decision = classify_pair(left, right, approved)
            if decision.kind in {EXACT_MATCH, REVIEW_REQUIRED}:
                decisions.append(decision)
    return decisions


def _tokens(*parts: str) -> list[str]:
    text = normalize_text(" ".join(part for part in parts if part), multiline=False).casefold()
    return [token.casefold() for token in _TOKEN.findall(text)]


def _duration(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        unit = _DURATION_WORD.get(tokens[index + 1])
        if token.isdigit() and unit:
            return f"{int(token)} {unit}"
    return None


def _users(tokens: list[str]) -> str | None:
    units = {"user", "users", "device", "devices", "máy", "may", "người", "nguoi"}
    for index, token in enumerate(tokens[:-1]):
        if token.isdigit() and tokens[index + 1] in units:
            return f"{int(token)} {tokens[index + 1]}"
    if "thiết" in tokens and "bị" in tokens:
        for index, token in enumerate(tokens[:-2]):
            if token.isdigit() and tokens[index + 1] == "thiết" and tokens[index + 2] == "bị":
                return f"{int(token)} thiết bị"
    return None


def _account_mode(text: str, tokens: list[str]) -> str | None:
    shared = "dùng chung" in text or "dung chung" in text or any(token in _SHARED for token in tokens)
    private = (
        "chính chủ" in text
        or "chinh chu" in text
        or any(token in _PRIVATE for token in tokens)
    )
    if shared and private:
        return "conflict"
    if shared:
        return "shared"
    if private:
        return "private"
    return None


def _region(text: str, tokens: list[str]) -> str | None:
    if "việt nam" in text or "viet nam" in text:
        return "vn"
    found = {_REGIONS[token] for token in tokens if token in _REGIONS}
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        return "conflict"
    return None


def _attributes_conflict(left: ProductSignature, right: ProductSignature) -> bool:
    if left.brand and right.brand and left.brand != right.brand:
        return True
    if left.family and right.family and left.family != right.family and left.brand and right.brand:
        return True
    for key in ("duration", "users", "account", "region", "plan"):
        left_value = left.fields.get(key)
        right_value = right.fields.get(key)
        if left_value and right_value and left_value != right_value:
            return True
        if left_value == "conflict" or right_value == "conflict":
            return True
    return False


def _attributes_prove_same(left: ProductSignature, right: ProductSignature) -> bool:
    if not left.brand or left.brand != right.brand or left.brand == "conflict":
        return False
    if not left.family or left.family != right.family:
        return False
    keys = ("duration", "plan", "users", "region", "account")
    if not any(left.fields.get(key) or right.fields.get(key) for key in keys):
        return False
    for key in keys:
        left_value = left.fields.get(key)
        right_value = right.fields.get(key)
        if left_value != right_value:
            return False
    return True


def _review_candidate(
    left: ProductSignature,
    right: ProductSignature,
    left_tokens: list[str],
    right_tokens: list[str],
) -> bool:
    same_brand = bool(left.brand and left.brand == right.brand and left.brand != "conflict")
    same_family = bool(left.family and left.family == right.family)
    if same_brand and same_family:
        return True
    return _jaccard(left_tokens, right_tokens) >= 0.6


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)
