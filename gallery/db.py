"""Firestore-facing data shaping.

Every function here takes the `items` collection reference as a plain
argument rather than owning a client — the client's lifecycle belongs to
__init__.py.

Firestore has no distinct()/aggregation query worth using for a personal
closet's worth of data (dozens to a few hundred docs), so the vocab
helpers below just fetch the whole collection and compute in Python —
simpler than replicating Mongo's distinct() and plenty fast at this scale.
"""

from pathlib import Path

CATEGORIES = ["Jacket", "Hoodie"]
OCCASIONS = ["casual", "formal", "athletic", "going-out"]
CONDITIONS = ["good", "needs-repair"]


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def distinct_values(items, field: str) -> list[str]:
    values = {
        v.strip()
        for doc in items.stream()
        for v in _as_list(doc.to_dict().get(field))
        if isinstance(v, str) and v.strip()
    }
    return sorted(values, key=str.lower)


def colors_pattern_vocab(items) -> list[str]:
    """Union of the current field plus the two legacy fields it replaced."""
    return sorted(
        set(distinct_values(items, "colors_pattern"))
        | set(distinct_values(items, "colors"))
        | set(distinct_values(items, "pattern")),
        key=str.lower,
    )


def category_vocab(items) -> list[str]:
    """Built-in categories plus any user-created ones already stored."""
    return sorted(
        set(CATEGORIES) | set(distinct_values(items, "category")),
        key=str.lower,
    )


def normalize_item(item: dict) -> dict:
    """Canonicalize a raw Firestore doc (or {} for an untagged item) into
    the shape search/render code can rely on, without each caller
    re-deriving legacy-field quirks.

    Note: this does NOT default `condition` to ["good"] — that's a form-UX
    default for the edit page, not real data, so an untagged item's
    condition normalizes to [] here (correct for search/filter purposes).
    """
    brand = item.get("brand") or []
    if isinstance(brand, str):
        brand = [brand] if brand else []

    colors_pattern = item.get("colors_pattern")
    if colors_pattern is None:
        # Legacy items saved before Colors and Pattern were merged — show
        # their old values until the item is next resaved.
        colors_pattern = list(item.get("colors") or [])
        if item.get("pattern"):
            colors_pattern.append(item["pattern"])

    size = item.get("size") or []
    if isinstance(size, str):
        size = [size] if size else []

    occasion_raw = item.get("occasion")
    if isinstance(occasion_raw, list):
        # Legacy items saved when Occasion allowed multiple checkboxes —
        # keep whichever one is still valid until the item is resaved.
        occasion = next((v for v in occasion_raw if v in OCCASIONS), "")
    else:
        occasion = occasion_raw or ""

    return {
        "name": item.get("name") or "",
        "category": item.get("category") or "",
        "brand": brand,
        "colors_pattern": colors_pattern,
        "size": size,
        "occasion": occasion,
        "condition": list(item.get("condition") or []),
        "notes": item.get("notes") or "",
        "created_at": item.get("created_at"),
    }


EMPTY_ITEM = normalize_item({})


def fetch_items_map(images: list[Path], items) -> dict[str, dict]:
    """One collection scan for every image's item doc, normalized. Images
    with no doc simply have no key — use EMPTY_ITEM as the .get() fallback."""
    names = {p.name for p in images}
    return {
        doc.id: normalize_item(doc.to_dict())
        for doc in items.stream()
        if doc.id in names
    }


def upsert_item(items, name: str, fields: dict, created_at: str) -> None:
    """$set + $setOnInsert-style upsert: overwrite the given fields, but
    only stamp created_at if the doc doesn't already exist (Firestore's
    merge=True doesn't distinguish "new doc" from "existing doc" itself)."""
    doc_ref = items.document(name)
    if not doc_ref.get().exists:
        fields = {**fields, "created_at": created_at}
    doc_ref.set(fields, merge=True)
