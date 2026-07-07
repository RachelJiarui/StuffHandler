# stuff-handler

Clothes photo pipeline (`done_input/` → `done_output/`) plus a gallery
website (Flask) for browsing/tagging the results — and an in-browser
upload flow (`/upload`) that runs the same pipeline, so new photos can be
added from a phone. The site is an installable PWA (Add to Home Screen).

## Setup (one-time)

```bash
cd /Users/Rachel/Development/RANDOM/stuff-handler
python3 -m venv .venv                          # already exists, skip if present
.venv/bin/pip install -r requirements.txt      # processing pipeline deps
.venv/bin/pip install -r requirements-web.txt  # gallery website deps
cp .env.example .env                           # then fill in OPENAI_API_KEY
```

`.env` holds `OPENAI_API_KEY` (pipeline enhance step) and optional gallery
overrides (`PHOTOS_DIR`, `MONGO_URI`, `MONGO_DB`, `PORT`).

## Processing pipeline

Turns raw photos in `input/` into background-removed PNGs in `output/`.

```bash
.venv/bin/python3 process.py input output                  # full: OpenAI enhance + bg removal
.venv/bin/python3 process.py input output --skip-enhance   # lite: bg removal only
```

Run `.venv/bin/python3 process.py --help` for brightness/contrast/model flags.

## Gallery website

A Flask app (`gallery/` package) that browses `done_output/` (default) in a
grid and lets you edit each photo's Name/Category/Brand/etc. (saved to
MongoDB). Layout:

```
wsgi.py                  entry point (dev server + gunicorn target)
gallery/
  __init__.py            app factory (create_app), config, Mongo connection
  routes.py              HTTP routes (index/search, edit, photo serving)
  uploads.py             upload/staging routes (/upload, redo, label)
  processing.py          background job runner for the upload pipeline
  db.py                  Mongo-facing data shaping
  search.py              query parsing, filtering, relevance scoring
  notes.py               notes-markup → HTML filter
  templates/             Jinja2 templates (base, index, edit, upload, label…)
  static/                CSS + JS, PWA manifest/service worker/icons
```

**Start MongoDB** (if not already running):

```bash
brew services start mongodb-community
# check: brew services list | grep mongo
```

Note: this is your one shared local `mongod` — it also holds other
projects' databases (`GameNiteLocalDev`, `chat_app_db`, etc.), so leave it
running if other apps need it rather than stopping it after this project.

**Run (development)** — debug mode with auto-reload:

```bash
.venv/bin/python3 wsgi.py                      # http://localhost:8000/
PORT=8080 .venv/bin/python3 wsgi.py            # different port
```

**Run (production-style)** — gunicorn, as you would on a server:

```bash
.venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app
```

Configuration is via environment variables / `.env` (no CLI flags):

| Variable | Default | Meaning |
|---|---|---|
| `PHOTOS_DIR` | `done_output/` | folder of images to display |
| `UPLOADS_DIR` | `uploads/` | staging area for the upload flow (originals + processed) |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `stuff_handler` | database name |
| `PORT` | `8000` | dev server port (`wsgi.py` only; gunicorn uses `-b`) |

### Uploading from the browser (`/upload`)

The "＋ Add items" link on the grid opens the upload page:

1. Pick photos (multiple; HEIC from iPhones is converted automatically,
   EXIF rotation applied). Choose the steps: **Remove background**,
   **Clean up with AI** (optionally with a "helpful message" appended to
   the gpt-image prompt), both, or neither.
2. Submit — processing runs in background threads inside the web process;
   the queue shows a spinner per photo, then ✓ Ready to label (or ⚠ Failed
   with the error; hit Redo to retry). While a photo is processing it's
   untouchable (no select/redo/delete) — its one action is **Stop**, which
   cancels the run and reverts the photo to its pre-run state (a first
   upload becomes failed, a redo goes back to its previous result). The
   underlying API call can't be aborted mid-flight; its result is simply
   discarded, so a stopped AI clean-up still costs the OpenAI call.
3. Select one or more photos → **Redo** to re-run with different options
   (edit the helpful message, start from the original or from the current
   processed result).
4. **Label →** opens the item form; Name is the only required field.
   Saving moves the processed PNG into `PHOTOS_DIR` and creates the Mongo
   item — from then on it's on the grid and searchable.

Staged files live in `uploads/originals/` and `uploads/processed/`
(gitignored). Originals are kept after labeling. Job state is in the
`uploads` Mongo collection; jobs interrupted by a server restart are
marked failed on the next boot so you can redo them.

Note: processing threads live inside the web process, so with
`gunicorn -w 2` each upload is processed by whichever worker accepted it —
fine for one user; keep it in mind before scaling workers.

**Stop:** `Ctrl+C`, or if backgrounded: `pkill -f "wsgi.py"` / `pkill -f gunicorn`.

## Deploying

The app is a standard WSGI app, so any Python host works (Render, Railway,
Fly.io, a VPS behind nginx…). Install `requirements-web.txt` only and run
`gunicorn wsgi:app`. Two things must move with it:

- **MongoDB** — point `MONGO_URI` at a managed instance (e.g. MongoDB Atlas
  free tier) instead of localhost.
- **Photos** — the app serves images straight from `PHOTOS_DIR` on disk, so
  the folder must be uploaded to (and persist on) the server. Hosts with
  ephemeral filesystems need a persistent volume, or the photos baked into
  the deploy.

⚠️ There is no authentication — anyone who can reach the site can view and
edit everything. Keep it on localhost/a private network, or put auth in
front (e.g. Cloudflare Access, an nginx basic-auth block, or a Tailscale
network) before exposing it publicly.

## Data

- Photos live on disk in `done_input/` / `done_output/` — the gallery never
  copies or modifies them, just reads and displays.
- Name/Category tags live in MongoDB: db `stuff_handler`, collection `items`,
  one document per filename (`_id` = filename). Inspect with:

```bash
mongosh stuff_handler --eval "db.items.find().pretty()"
```

## Ports / services in use

| What | Address | Notes |
|---|---|---|
| Gallery website | `localhost:8000` (default) | stop with Ctrl+C / `pkill -f gunicorn` |
| MongoDB | `localhost:27017` | shared across projects, managed via `brew services` |
