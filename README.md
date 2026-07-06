# stuff-handler

Clothes photo pipeline (`done_input/` → `done_output/`) plus a local gallery
website for browsing/tagging the results.

## Setup (one-time)

```bash
cd /Users/Rachel/Development/RANDOM/stuff-handler
python3 -m venv .venv          # already exists, skip if present
.venv/bin/pip install -r requirements.txt
```

`.env` holds `OPENAI_API_KEY`, used only by the processing pipeline's enhance
step.

## Processing pipeline

Turns raw photos in `input/` into background-removed PNGs in
`output/`.

```bash
.venv/bin/python3 process.py input output          # full: OpenAI enhance + bg removal
.venv/bin/python3 process.py input output --skip-enhance  # lite: bg removal only
```

Run `.venv/bin/python3 process.py --help` for brightness/contrast/model flags.

## Gallery website

Browses `done_output/` (default) in a grid, and lets you edit each photo's
Name/Category (saved to MongoDB).

**Start MongoDB** (if not already running):

```bash
brew services start mongodb-community
# check: brew services list | grep mongo
```

Note: this is your one shared local `mongod` — it also holds other
projects' databases (`GameNiteLocalDev`, `chat_app_db`, etc.), so leave it
running if other apps need it rather than stopping it after this project.

**Start the gallery:**

```bash
.venv/bin/python3 gallery/server.py
```

Then open **http://localhost:8000/**. Options:

```bash
.venv/bin/python3 gallery/server.py --port 8000 --dir done_output --mongo-uri mongodb://localhost:27017
```

**Stop the gallery:** press `Ctrl+C` in the terminal it's running in. If it
was started in the background, find and kill it:

```bash
pgrep -fl "gallery/server.py"     # find the PID
kill <PID>                        # or: pkill -f "gallery/server.py"
```

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
| Gallery website | `localhost:8000` (default) | stop with Ctrl+C / `pkill -f "gallery/server.py"` |
| MongoDB | `localhost:27017` | shared across projects, managed via `brew services` |
