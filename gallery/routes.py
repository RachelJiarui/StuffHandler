"""HTTP layer: routing, request parsing, photo serving.

Data shaping lives in db.py, search/filter logic in search.py, and HTML in
templates/ — this module only wires requests to those pieces.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from . import db, search

bp = Blueprint("gallery", __name__)

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}

FACET_LABELS = {
    "category": "Category",
    "brand": "Brand",
    "colors_pattern": "Colors & Pattern",
    "size": "Size",
    "occasion": "Occasion",
    "condition": "Condition",
}


def photos_dir() -> Path:
    return current_app.config["PHOTOS_DIR"]


def items_coll():
    return current_app.extensions["gallery_items"]


def find_images(directory: Path) -> list[Path]:
    # Scans fresh on every request, so adding/removing files is reflected
    # on the next page load — no build step, no restart.
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED
    )


def image_or_404(name: str) -> Path:
    if "/" in name or "\\" in name:
        abort(400)
    candidate = photos_dir() / name
    if candidate not in find_images(photos_dir()):
        abort(404)
    return candidate


@bp.get("/")
def index():
    query, filters = search.parse_search_params(request.args.to_dict(flat=False))
    images = find_images(photos_dir())
    coll = items_coll()
    items_map = db.fetch_items_map(images, coll)
    matched = search.search_and_filter(images, items_map, query, filters, db.EMPTY_ITEM)
    count_text = search.build_count_text(len(matched), len(images))

    if request.headers.get("X-Requested-With") == "fetch":
        # Async search: client swaps the results grid in place instead of
        # navigating, so scroll position and input focus survive keystrokes.
        return {
            "results_html": render_template(
                "_results.html",
                matched=matched, items_map=items_map,
                query=query, empty_item=db.EMPTY_ITEM,
            ),
            "count_text": count_text,
        }

    facet_vocab = {}
    if images:
        facet_vocab = {
            "category": db.category_vocab(coll),
            "brand": db.distinct_values(coll, "brand"),
            "colors_pattern": db.colors_pattern_vocab(coll),
            "size": db.distinct_values(coll, "size"),
            "occasion": db.OCCASIONS,
            "condition": db.CONDITIONS,
        }

    return render_template(
        "index.html",
        directory=photos_dir(),
        images=images,
        items_map=items_map,
        matched=matched,
        query=query,
        filters=filters,
        empty_item=db.EMPTY_ITEM,
        count_text=count_text,
        facet_fields=search.FACET_FIELDS,
        facet_labels=FACET_LABELS,
        facet_vocab=facet_vocab,
        active_count=sum(len(v) for v in filters.values()),
    )


def format_created(created_raw: str | None) -> str:
    if not created_raw:
        return "Not yet saved"
    try:
        return datetime.fromisoformat(created_raw).strftime("%b %-d, %Y %-I:%M %p")
    except ValueError:
        return created_raw


@bp.get("/edit/<name>")
def edit(name: str):
    candidate = image_or_404(name)
    coll = items_coll()
    snap = coll.document(candidate.name).get()
    item = db.normalize_item(snap.to_dict() if snap.exists else {})

    images = find_images(photos_dir())
    items_map = db.fetch_items_map(images, coll)
    mentions = [
        {"file": p.name, "name": items_map.get(p.name, db.EMPTY_ITEM)["name"] or p.stem}
        for p in images if p.name != candidate.name
    ]

    return render_template(
        "edit.html",
        filename=candidate.name,
        stem=candidate.stem,
        item=item,
        name=item["name"] or candidate.stem,
        category_vocab=db.category_vocab(coll),
        brand_vocab=db.distinct_values(coll, "brand"),
        colors_pattern_vocab=db.colors_pattern_vocab(coll),
        size_vocab=db.distinct_values(coll, "size"),
        occasions=db.OCCASIONS,
        conditions=db.CONDITIONS,
        # Defaulting condition to "good" is a form-UX default, not stored data.
        condition_selected=set(item["condition"] or ["good"]),
        created_display=format_created(item["created_at"]),
        mentions_json=json.dumps(mentions).replace("</", "<\\/"),
    )


def parse_item_fields(form) -> dict:
    """Item fields from an edit/label form, ready to $set on the doc.
    Shared by the edit page and the upload label page."""

    def first(key: str) -> str:
        return form.get(key, "").strip()

    occasion = first("occasion")
    if occasion not in db.OCCASIONS:
        occasion = ""

    # Brand and Size are single-select; docs saved before that may hold
    # several values — a resave keeps just the first, the same
    # until-next-resave migration normalize_item does for occasion.
    brand = [b.strip() for b in first("brand").split(",") if b.strip()][:1]
    colors_pattern = [c.strip() for c in first("colors_pattern").split(",") if c.strip()]
    size = [s.strip() for s in first("size").split(",") if s.strip()][:1]
    condition = [v for v in form.getlist("condition") if v in db.CONDITIONS] or ["good"]

    return {
        "name": first("name"),
        "category": first("category"),
        "brand": brand,
        "colors_pattern": colors_pattern,
        "size": size,
        "occasion": occasion,
        "condition": condition,
        "notes": form.get("notes", ""),
    }


@bp.post("/edit/<name>")
def edit_save(name: str):
    candidate = image_or_404(name)

    db.upsert_item(
        items_coll(), candidate.name, parse_item_fields(request.form),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    if request.headers.get("X-Requested-With") == "fetch":
        # Autosave: skip re-rendering/redirecting, nothing to show.
        return "", 204

    return redirect(url_for("gallery.edit", name=candidate.name), code=303)


@bp.get("/photos/<name>")
def photo(name: str):
    candidate = image_or_404(name)
    response = send_file(candidate)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/sw.js")
def service_worker():
    # Served from the root (not /static/) so the service worker's scope
    # covers the whole site, which PWA installation requires.
    return current_app.send_static_file("sw.js")
