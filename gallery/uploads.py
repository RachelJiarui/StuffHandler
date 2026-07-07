"""Upload & staging flow: add photos, run them through the processing
pipeline in the background, then label the results into the closet.

Staged files live under UPLOADS_DIR (originals/ and processed/); a photo
only moves into PHOTOS_DIR — and appears on the main grid, searchable —
once it's been labeled with a name. Originals are kept after labeling.
"""

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from PIL import Image, ImageOps

from . import db
from .processing import now_iso
from .routes import parse_item_fields

try:
    # iPhone photos default to HEIC; registering the opener makes
    # PIL.Image.open handle them like any other format.
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

bp = Blueprint("uploads", __name__, url_prefix="/upload")

ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_MESSAGE_LEN = 500


def coll():
    return current_app.extensions["gallery_uploads"]


def processor():
    return current_app.extensions["gallery_processor"]


def originals_dir() -> Path:
    return current_app.config["UPLOADS_DIR"] / "originals"


def processed_dir() -> Path:
    return current_app.config["UPLOADS_DIR"] / "processed"


def doc_or_404(upload_id: str) -> dict:
    if not ID_RE.fullmatch(upload_id):
        abort(400)
    doc = coll().find_one({"_id": upload_id})
    if not doc:
        abort(404)
    return doc


def save_original(file_storage) -> tuple[str, str]:
    """Normalize an upload into a browser- and pipeline-friendly file:
    EXIF orientation applied, HEIC converted. Returns (id, disk filename)."""
    img = Image.open(file_storage.stream)
    img = ImageOps.exif_transpose(img)
    upload_id = uuid.uuid4().hex
    if "A" in img.getbands():
        name = f"{upload_id}.png"
        img.save(originals_dir() / name, "PNG")
    else:
        name = f"{upload_id}.jpg"
        img.convert("RGB").save(originals_dir() / name, "JPEG", quality=92)
    return upload_id, name


def run_summary(doc: dict) -> str:
    steps = []
    if doc["enhance"]:
        steps.append("AI clean-up")
    if doc["remove_bg"]:
        steps.append("background removal")
    text = " + ".join(steps) or "no processing (kept as-is)"
    if doc["source"] == "processed":
        text += " · rerun from processed result"
    if doc.get("message"):
        text += f" · note: “{doc['message']}”"
    return text


def staged_docs() -> list[dict]:
    docs = list(
        coll().find({"status": {"$ne": "finalized"}}).sort("created_at", -1)
    )
    for d in docs:
        d["id"] = d["_id"]
        d["has_processed"] = (processed_dir() / f"{d['_id']}.png").exists()
        d["run_summary"] = run_summary(d)
    return docs


def queue_payload() -> dict:
    docs = staged_docs()
    return {
        "queue_html": render_template("_queue.html", uploads=docs),
        "processing": sum(1 for d in docs if d["status"] == "processing"),
    }


def safe_stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._-")
    return stem or "item"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.get("")
def page():
    docs = staged_docs()
    return render_template(
        "upload.html",
        uploads=docs,
        processing=sum(1 for d in docs if d["status"] == "processing"),
    )


@bp.post("")
def upload():
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return jsonify({"error": "No photos selected."}), 400

    enhance = request.form.get("enhance") == "on"
    remove_bg = request.form.get("remove_bg") == "on"
    message = request.form.get("message", "").strip()[:MAX_MESSAGE_LEN] if enhance else ""

    created, errors = [], []
    for f in files:
        try:
            upload_id, disk_name = save_original(f)
        except Exception as e:
            errors.append({"filename": f.filename, "error": f"couldn't read image ({e})"})
            continue
        coll().insert_one({
            "_id": upload_id,
            "original_filename": f.filename,
            "original_file": disk_name,
            "status": "processing",
            "error": None,
            "enhance": enhance,
            "remove_bg": remove_bg,
            "message": message,
            "source": "original",
            "run_token": uuid.uuid4().hex,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
        processor().enqueue(upload_id)
        created.append(upload_id)

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"created": created, "errors": errors, **queue_payload()})
    return redirect(url_for("uploads.page"), code=303)


@bp.get("/status")
def status():
    return jsonify(queue_payload())


@bp.get("/photo/<upload_id>")
def photo(upload_id: str):
    doc = doc_or_404(upload_id)
    if request.args.get("v") == "processed":
        path = processed_dir() / f"{upload_id}.png"
    else:
        path = originals_dir() / doc["original_file"]
    if not path.exists():
        abort(404)
    response = send_file(path)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.post("/redo")
def redo():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    source = "processed" if data.get("source") == "processed" else "original"
    enhance = bool(data.get("enhance"))
    remove_bg = bool(data.get("remove_bg"))
    message = str(data.get("message") or "").strip()[:MAX_MESSAGE_LEN] if enhance else ""

    errors = []
    for upload_id in ids:
        upload_id = str(upload_id)
        if not ID_RE.fullmatch(upload_id):
            continue
        doc = coll().find_one({"_id": upload_id})
        if not doc or doc["status"] in ("finalized", "processing"):
            continue
        if source == "processed" and not (processed_dir() / f"{upload_id}.png").exists():
            errors.append({"id": upload_id, "error": "no processed version to redo from"})
            continue
        coll().update_one(
            {"_id": upload_id},
            {"$set": {
                "status": "processing",
                "error": None,
                "enhance": enhance,
                "remove_bg": remove_bg,
                "message": message,
                "source": source,
                "run_token": uuid.uuid4().hex,
                # Snapshot of the pre-run state so Stop can revert to it.
                "prev": {
                    "status": doc["status"],
                    "error": doc.get("error"),
                    "enhance": doc["enhance"],
                    "remove_bg": doc["remove_bg"],
                    "message": doc.get("message", ""),
                    "source": doc.get("source", "original"),
                },
                "updated_at": now_iso(),
            }},
        )
        processor().enqueue(upload_id)

    return jsonify({"errors": errors, **queue_payload()})


@bp.post("/stop")
def stop():
    """Stop an in-flight run: revert the doc to its pre-run state (first
    runs, which have nothing to revert to, become failed). The worker
    thread can't abort a pipeline call mid-flight, but the run token no
    longer matches so its result is discarded when it finishes."""
    data = request.get_json(silent=True) or {}
    for upload_id in data.get("ids") or []:
        upload_id = str(upload_id)
        if not ID_RE.fullmatch(upload_id):
            continue
        doc = coll().find_one({"_id": upload_id})
        if not doc or doc["status"] != "processing":
            continue
        restored = doc.get("prev") or {
            "status": "failed",
            "error": "Stopped — redo to retry.",
        }
        coll().update_one(
            {"_id": upload_id, "status": "processing"},
            {
                "$set": {**restored, "updated_at": now_iso()},
                "$unset": {"prev": "", "run_token": ""},
            },
        )
    return jsonify(queue_payload())


@bp.post("/delete")
def delete():
    data = request.get_json(silent=True) or {}
    for upload_id in data.get("ids") or []:
        upload_id = str(upload_id)
        if not ID_RE.fullmatch(upload_id):
            continue
        doc = coll().find_one({"_id": upload_id})
        # Processing photos are untouchable — they must be stopped first.
        if not doc or doc["status"] in ("finalized", "processing"):
            continue
        coll().delete_one({"_id": upload_id})
        (originals_dir() / doc["original_file"]).unlink(missing_ok=True)
        (processed_dir() / f"{upload_id}.png").unlink(missing_ok=True)
    return jsonify(queue_payload())


@bp.get("/label/<upload_id>")
def label(upload_id: str):
    doc = doc_or_404(upload_id)
    if doc["status"] != "done":
        return redirect(url_for("uploads.page"), code=303)
    items = current_app.extensions["gallery_items"]
    return render_template(
        "label.html",
        upload=doc,
        upload_id=upload_id,
        category_vocab=db.category_vocab(items),
        brand_vocab=db.distinct_values(items, "brand"),
        colors_pattern_vocab=db.colors_pattern_vocab(items),
        size_vocab=db.distinct_values(items, "size"),
        occasions=db.OCCASIONS,
        conditions=db.CONDITIONS,
    )


@bp.post("/label/<upload_id>")
def label_save(upload_id: str):
    doc = doc_or_404(upload_id)
    processed = processed_dir() / f"{upload_id}.png"
    if doc["status"] != "done" or not processed.exists():
        abort(409)

    fields = parse_item_fields(request.form)
    if not fields["name"]:
        abort(400, "A name is required.")

    photos = current_app.config["PHOTOS_DIR"]
    stem = safe_stem(doc["original_filename"])
    final = photos / f"{stem}.png"
    n = 2
    while final.exists():
        final = photos / f"{stem}-{n}.png"
        n += 1
    shutil.move(processed, final)

    current_app.extensions["gallery_items"].update_one(
        {"_id": final.name},
        {
            "$set": fields,
            "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()},
        },
        upsert=True,
    )
    coll().update_one(
        {"_id": upload_id},
        {"$set": {"status": "finalized", "final_name": final.name, "updated_at": now_iso()}},
    )
    return redirect(url_for("uploads.page"), code=303)
