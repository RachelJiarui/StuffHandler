"""Query parsing, faceted filtering, and relevance scoring.

Operates purely on normalized item dicts (see db.normalize_item) and Paths
— no Mongo or HTTP knowledge.
"""

import difflib
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FACET_FIELDS = ["category", "brand", "colors_pattern", "size", "occasion", "condition"]
LIST_FACETS = {"brand", "colors_pattern", "size", "condition"}
SCALAR_FACETS = {"category", "occasion"}

WORD_RE = re.compile(r"[a-z0-9]+")

# Weight of a field when a query term matches it — name matches count for
# more than, say, a notes match.
FIELD_WEIGHTS = {
    "name": 5.0,
    "category": 3.0,
    "brand": 3.0,
    "colors_pattern": 2.0,
    "size": 2.0,
    "occasion": 1.5,
    "condition": 1.0,
    "notes": 1.0,
}

FUZZY_MIN_TERM_LEN = 3
FUZZY_THRESHOLD = 0.75


def item_matches_filters(item: dict, filters: dict[str, list[str]]) -> bool:
    """AND across facets, OR within a facet's selected values."""
    for facet, selected in filters.items():
        selected_lower = {v.lower() for v in selected}
        if facet in SCALAR_FACETS:
            if str(item.get(facet, "")).lower() not in selected_lower:
                return False
        else:  # LIST_FACETS
            values_lower = {v.lower() for v in item.get(facet, [])}
            if not (values_lower & selected_lower):
                return False
    return True


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def build_haystacks(item: dict, path: Path) -> dict[str, str]:
    return {
        "name": (item.get("name") or path.stem).lower(),
        "category": (item.get("category") or "").lower(),
        "brand": " ".join(item.get("brand", [])).lower(),
        "colors_pattern": " ".join(item.get("colors_pattern", [])).lower(),
        "size": " ".join(item.get("size", [])).lower(),
        "occasion": (item.get("occasion") or "").lower(),
        "condition": " ".join(item.get("condition", [])).lower(),
        "notes": (item.get("notes") or "").lower(),
    }


def term_field_score(term: str, haystack: str) -> float:
    if term in haystack:
        return 1.0
    if len(term) < FUZZY_MIN_TERM_LEN or term.isdigit():
        # Purely numeric terms (filename fragments like "5374") are prone
        # to spuriously high similarity against unrelated numbers ("5373")
        # — fuzzy matching there is noise, not "smart". Exact substring
        # only for those.
        return 0.0
    best = 0.0
    for word in WORD_RE.findall(haystack):
        ratio = difflib.SequenceMatcher(None, term, word).ratio()
        if ratio > best:
            best = ratio
    return best if best >= FUZZY_THRESHOLD else 0.0


def score_item(query_terms: list[str], haystacks: dict[str, str]) -> float | None:
    total = 0.0
    for term in query_terms:
        term_score = sum(
            FIELD_WEIGHTS[field] * term_field_score(term, haystacks[field])
            for field in FIELD_WEIGHTS
        )
        if term_score == 0.0:
            return None  # every query word must match something
        total += term_score
    return total


def parse_search_params(path: str) -> tuple[str, dict[str, list[str]]]:
    query_params = parse_qs(urlsplit(path).query)
    q = query_params.get("q", [""])[0].strip()
    filters = {
        field: [v.strip() for v in query_params.get(field, []) if v.strip()]
        for field in FACET_FIELDS
    }
    filters = {field: values for field, values in filters.items() if values}
    return q, filters


def search_and_filter(
    images: list[Path],
    items_map: dict[str, dict],
    query: str,
    filters: dict[str, list[str]],
    empty_item: dict,
) -> list[Path]:
    candidates = [
        p for p in images
        if item_matches_filters(items_map.get(p.name, empty_item), filters)
    ]

    terms = tokenize(query) if query else []
    if not terms:
        return candidates  # default alphabetical order, no scoring

    scored = []
    for p in candidates:
        item = items_map.get(p.name, empty_item)
        haystacks = build_haystacks(item, p)
        score = score_item(terms, haystacks)
        if score is not None:
            display_name = (item.get("name") or p.stem).lower()
            scored.append((score, display_name, p))

    scored.sort(key=lambda t: (-t[0], t[1], t[2].name))
    return [p for _, _, p in scored]


def build_count_text(shown: int, total: int) -> str:
    plural = "s" if total != 1 else ""
    if shown == total:
        return f"{total} photo{plural}"
    return f"{shown} of {total} photo{plural}"
