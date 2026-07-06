#!/usr/bin/env python3
"""
Local image gallery.

Serves a Cargo-style grid view of every image in a directory (default:
../done_output), with search and faceted filters. Scans the directory
fresh on every request, so adding or removing files there is reflected on
the next page refresh — no build step.

Clicking a photo opens an edit page where you can set Name, Category, Brand,
Colors & Pattern, Size, Occasion, and (under "More details") Condition and
Notes for it — all persisted in MongoDB (db "stuff_handler", collection
"items", keyed by filename) so they survive restarts. Brand, Colors &
Pattern, and Size are freeform text but also offer autocomplete suggestions
drawn from every value already used across other items, so a value typed
once becomes selectable everywhere else.

This module is just the HTTP transport layer (routing, static/photo
serving, CLI, Mongo connection lifecycle) — data shaping lives in db.py,
search/filter logic in search.py, HTML generation in render.py.

Usage:
    python3 gallery/server.py [--port 8000] [--dir path/to/photos] [--mongo-uri URI]
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote
from datetime import datetime, timezone

from pymongo import MongoClient

import db
import render
import search

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
STATIC_DIR = Path(__file__).parent / "static"


def find_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED
    )


def make_handler(directory: Path, items):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep stdout quiet

        def do_GET(self):
            path = unquote(self.path.split("?", 1)[0])

            if path == "/":
                self._get_index()
            elif path.startswith("/edit/"):
                self._get_edit(path[len("/edit/"):])
            elif path.startswith("/photos/"):
                self._send_photo(path[len("/photos/"):])
            elif path.startswith("/static/"):
                self._send_static(path[len("/static/"):])
            else:
                self.send_error(404)

        def do_POST(self):
            path = unquote(self.path.split("?", 1)[0])
            if path.startswith("/edit/"):
                self._post_edit(path[len("/edit/"):])
            else:
                self.send_error(404)

        def _image_or_404(self, name: str) -> Path | None:
            if "/" in name or "\\" in name:
                self.send_error(400)
                return None
            candidate = directory / name
            if candidate not in find_images(directory):
                self.send_error(404)
                return None
            return candidate

        def _get_index(self):
            query, filters = search.parse_search_params(self.path)
            images = find_images(directory)
            items_map = db.fetch_items_map(images, items)
            matched = search.search_and_filter(images, items_map, query, filters, db.EMPTY_ITEM)

            if self.headers.get("X-Requested-With") == "fetch":
                # Async search: client swaps the results grid in place
                # instead of navigating, so scroll position and input focus
                # survive every keystroke.
                self._send_json({
                    "results_html": render.build_results_html(matched, items_map, query),
                    "count_text": search.build_count_text(len(matched), len(images)),
                })
                return

            self._send_html(
                render.render_index_page(directory, images, items_map, matched, query, filters, items)
            )

        def _get_edit(self, name: str):
            candidate = self._image_or_404(name)
            if candidate is None:
                return
            item = items.find_one({"_id": candidate.name}) or {}
            images = find_images(directory)
            self._send_html(render.render_edit(candidate, item, items, images))

        def _post_edit(self, name: str):
            candidate = self._image_or_404(name)
            if candidate is None:
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            fields = parse_qs(body)

            def first(key: str) -> str:
                return fields.get(key, [""])[0].strip()

            new_category = first("category")

            occasion = first("occasion")
            if occasion not in db.OCCASIONS:
                occasion = ""

            # Brand and Size are single-select; docs saved before that may
            # hold several values — a resave keeps just the first, the same
            # until-next-resave migration normalize_item does for occasion.
            brand = [b.strip() for b in first("brand").split(",") if b.strip()][:1]
            colors_pattern = [c.strip() for c in first("colors_pattern").split(",") if c.strip()]
            size = [s.strip() for s in first("size").split(",") if s.strip()][:1]
            condition = [v for v in fields.get("condition", []) if v in db.CONDITIONS] or ["good"]
            notes = fields.get("notes", [""])[0]

            items.update_one(
                {"_id": candidate.name},
                {
                    "$set": {
                        "name": first("name"),
                        "category": new_category,
                        "brand": brand,
                        "colors_pattern": colors_pattern,
                        "size": size,
                        "occasion": occasion,
                        "condition": condition,
                        "notes": notes,
                    },
                    "$setOnInsert": {
                        "created_at": datetime.now(timezone.utc).isoformat()
                    },
                },
                upsert=True,
            )

            if self.headers.get("X-Requested-With") == "fetch":
                # Autosave: skip re-rendering/redirecting, nothing to show.
                self.send_response(204)
                self.end_headers()
                return

            self.send_response(303)
            self.send_header("Location", f"/edit/{candidate.name}")
            self.end_headers()

        def _send_html(self, html_body: str):
            body = html_body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data: dict):
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_photo(self, name: str):
            candidate = self._image_or_404(name)
            if candidate is None:
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _send_static(self, name: str):
            if "/" in name or "\\" in name:
                self.send_error(400)
                return
            candidate = STATIC_DIR / name
            if not candidate.is_file() or candidate.parent != STATIC_DIR:
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--dir", type=Path, default=Path(__file__).parent.parent / "done_output",
        help="Folder of images to display (default: ../done_output)",
    )
    parser.add_argument(
        "--mongo-uri", default="mongodb://localhost:27017",
        help="MongoDB connection URI (default: mongodb://localhost:27017)",
    )
    args = parser.parse_args()

    directory = args.dir.resolve()
    if not directory.is_dir():
        raise SystemExit(f"Error: '{directory}' is not a directory.")

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except Exception as e:
        raise SystemExit(
            f"Error: couldn't reach MongoDB at {args.mongo_uri} ({e}).\n"
            "Start it with: brew services start mongodb-community"
        )
    items = client["stuff_handler"]["items"]

    server = ThreadingHTTPServer(("localhost", args.port), make_handler(directory, items))
    print(f"Serving images from {directory}")
    print(f"Open http://localhost:{args.port}/ in your browser (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
