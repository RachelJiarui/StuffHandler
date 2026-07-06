"""HTML rendering. Every function here takes data in and returns an HTML
string — no Mongo or HTTP knowledge."""

import html
import json
import re
from datetime import datetime
from pathlib import Path

import db
import search

SITE_TITLE = "Stuff Handler"

MENTION_RE = re.compile(r"@\[([^\]]+)\]\(([^)]+)\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

FACET_LABELS = {
    "category": "Category",
    "brand": "Brand",
    "colors_pattern": "Colors & Pattern",
    "size": "Size",
    "occasion": "Occasion",
    "condition": "Condition",
}

HEAD = f'''  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Chivo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">'''


def notes_to_html(text: str) -> str:
    """Render stored notes markup (`@[Name](file)`, `[text](url)`) as HTML."""
    escaped = html.escape(text)

    def mention_sub(m):
        name, file = m.group(1), m.group(2)
        return (f'<span class="mention" contenteditable="false" '
                f'data-file="{file}" data-name="{name}">@{name}</span>')

    def link_sub(m):
        text_, url = m.group(1), m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener">{text_}</a>'

    out = MENTION_RE.sub(mention_sub, escaped)
    out = LINK_RE.sub(link_sub, out)
    return out


def datalist_html(list_id: str, values: list[str]) -> str:
    options = "".join(f'<option value="{html.escape(v)}">' for v in values)
    return f'<datalist id="{list_id}">{options}</datalist>'


def combobox_html(
    name: str,
    label: str,
    values: list[str],
    options: list[str],
    placeholder: str,
    single: bool,
) -> str:
    """Strict label picker (see edit.js initTagInputs): a dropdown of the
    known vocabulary, filtered by typing — new labels only via the explicit
    "Make this a new label" row, never by freeform typing."""
    list_id = f"{name.replace('_', '-')}-options"
    single_attr = " data-single" if single else ""
    return f'''<label>
        <span class="field-label">{html.escape(label)}</span>
        <div class="tag-input" data-name="{name}"{single_attr}>
          <div class="tags"></div>
          <input type="text" class="tag-entry" data-suggestions="{list_id}" placeholder="{html.escape(placeholder)}">
          <input type="hidden" name="{name}" class="tag-value" value="{html.escape(",".join(values))}">
        </div>
        {datalist_html(list_id, options)}
      </label>'''


def checkbox_group(field: str, choices: list[str], selected: set[str]) -> str:
    boxes = []
    for choice in choices:
        checked = " checked" if choice in selected else ""
        label = choice.replace("-", " ").capitalize()
        boxes.append(
            f'<label class="checkbox"><input type="checkbox" name="{field}" '
            f'value="{choice}"{checked}> {html.escape(label)}</label>'
        )
    return f'<div class="checkbox-group">{"".join(boxes)}</div>'


def build_mentions(images: list[Path], items, exclude: str) -> list[dict]:
    mentions = []
    for p in images:
        if p.name == exclude:
            continue
        item = items.find_one({"_id": p.name}) or {}
        mentions.append({"file": p.name, "name": item.get("name") or p.stem})
    return mentions


def build_results_html(matched: list[Path], items_map: dict, query: str) -> str:
    if not matched:
        message = "No items match your search." if query else "No items match the selected filters."
        return f'<p class="empty">{html.escape(message)} <a href="/">Clear search</a></p>'

    rows = []
    for i, path in enumerate(matched, start=1):
        item = items_map.get(path.name, db.EMPTY_ITEM)
        display_name = item.get("name") or path.stem
        rows.append(f'''
        <div class="item">
          <a class="thumb" href="/edit/{path.name}">
            <img src="/photos/{path.name}" alt="{html.escape(display_name)}" loading="lazy">
          </a>
          <div class="caption">
            <span class="idx">{i:02d}</span>
            <span class="name">{html.escape(display_name)}</span>
          </div>
        </div>''')
    return f'<div class="grid">{"".join(rows)}</div>'


def build_search_bar_html(items, query: str, filters: dict[str, list[str]]) -> str:
    facet_vocab = {
        "category": db.category_vocab(items),
        "brand": db.distinct_values(items, "brand"),
        "colors_pattern": db.colors_pattern_vocab(items),
        "size": db.distinct_values(items, "size"),
        "occasion": db.OCCASIONS,
        "condition": db.CONDITIONS,
    }

    groups = []
    for facet in search.FACET_FIELDS:
        choices = facet_vocab[facet]
        if not choices:
            continue
        selected = set(filters.get(facet, []))
        scroll_class = " scrollable" if len(choices) > 8 else ""
        groups.append(f'''
          <div class="filter-group field-block{scroll_class}">
            <span class="field-label">{html.escape(FACET_LABELS[facet])}</span>
            {checkbox_group(facet, choices, selected)}
          </div>''')

    active_count = sum(len(v) for v in filters.values())
    count_badge = f'<span class="filter-count"{"" if active_count else " hidden"}>({active_count})</span>'
    clear_hidden = "" if (query or filters) else " hidden"

    return f'''
  <form method="get" action="/" class="search-form" id="search-form">
    <input type="search" name="q" value="{html.escape(query)}" placeholder="Search name, brand, notes…" class="search-input">
    <details class="more-details filters-panel"{" open" if filters else ""}>
      <summary>Filters {count_badge}</summary>
      <div class="filters-body">{"".join(groups)}</div>
    </details>
    <a href="/" class="clear-filters"{clear_hidden}>Clear</a>
  </form>'''


def render_index_page(
    directory: Path,
    images: list[Path],
    items_map: dict,
    matched: list[Path],
    query: str,
    filters: dict[str, list[str]],
    items,
) -> str:
    if not images:
        search_bar = ""
        body = f'<p class="empty">No images found in {html.escape(str(directory))}.</p>'
    else:
        search_bar = build_search_bar_html(items, query, filters)
        body = build_results_html(matched, items_map, query)

    count_text = search.build_count_text(len(matched), len(images))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <title>{SITE_TITLE}</title>
{HEAD}
</head>
<body>
  <header>
    <span class="title">{SITE_TITLE}</span>
    {search_bar}
    <span class="count">{count_text}</span>
  </header>
  <main id="results">{body}</main>
  <script src="/static/search.js" defer></script>
</body>
</html>'''


def render_edit(path: Path, item: dict, items, images: list[Path]) -> str:
    normalized = db.normalize_item(item)
    name = normalized["name"] or path.stem
    category = normalized["category"]
    brand = normalized["brand"]
    colors_pattern = normalized["colors_pattern"]
    size = normalized["size"]
    occasion = normalized["occasion"]
    condition = set(normalized["condition"] or ["good"])  # form-UX default, not stored data
    notes = normalized["notes"]

    created_raw = normalized["created_at"]
    if created_raw:
        try:
            created_display = (
                datetime.fromisoformat(created_raw)
                .strftime("%b %-d, %Y %-I:%M %p")
            )
        except ValueError:
            created_display = created_raw
    else:
        created_display = "Not yet saved"

    occasion_options = ['<option value="">—</option>']
    for o in db.OCCASIONS:
        selected = " selected" if o == occasion else ""
        label = o.replace("-", " ").capitalize()
        occasion_options.append(f'<option value="{o}"{selected}>{html.escape(label)}</option>')

    mentions = build_mentions(images, items, exclude=path.name)
    mentions_json = json.dumps(mentions).replace("</", "<\\/")

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <title>{html.escape(name)} — {SITE_TITLE}</title>
{HEAD}
</head>
<body>
  <header>
    <a class="title" href="/">← {SITE_TITLE}</a>
    <span class="save-status"></span>
  </header>
  <div class="edit-layout">
    <div class="edit-image">
      <img src="/photos/{path.name}" alt="{html.escape(name)}">
    </div>
    <form class="edit-form" method="post" action="/edit/{path.name}">
      <label>
        <span class="field-label">Name</span>
        <textarea name="name" class="name-input" rows="1" placeholder="{html.escape(path.stem)}">{html.escape(name)}</textarea>
      </label>
      {combobox_html("category", "Category", [category] if category else [],
                     db.category_vocab(items), "Select a category", single=True)}
      {combobox_html("brand", "Brand", brand,
                     db.distinct_values(items, "brand"), "Select a brand", single=True)}
      {combobox_html("colors_pattern", "Colors & Pattern", colors_pattern,
                     db.colors_pattern_vocab(items), "Add a color or pattern", single=False)}
      {combobox_html("size", "Size", size,
                     db.distinct_values(items, "size"), "Select a size", single=True)}
      <label>
        <span class="field-label">Occasion</span>
        <select name="occasion">
          {"".join(occasion_options)}
        </select>
      </label>

      <details class="more-details">
        <summary>More details</summary>
        <div class="more-details-body">
          <div class="field-block">
            <span class="field-label">Condition</span>
            {checkbox_group("condition", db.CONDITIONS, condition)}
          </div>
          <div class="field-block">
            <span class="field-label">Created</span>
            <span class="readonly-value">{html.escape(created_display)}</span>
          </div>
          <div class="notes-field">
            <span class="field-label">Notes</span>
            <div class="notes-editor" contenteditable="true" data-placeholder="Type @ to mention a closet item, ⌘K to link…">{notes_to_html(notes)}</div>
            <textarea name="notes" class="notes-raw" hidden>{html.escape(notes)}</textarea>
          </div>
        </div>
      </details>
    </form>
  </div>
  <script type="application/json" id="mention-data">{mentions_json}</script>
  <script src="/static/edit.js" defer></script>
</body>
</html>'''
