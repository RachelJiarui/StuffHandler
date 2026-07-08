# Deployment (Google Cloud)

The live site: **https://104-198-23-54.nip.io**

A dedicated GCP project (`stuff-handler-app`, same billing account as your
other projects) running:

- One `e2-micro` Compute Engine VM (`stuff-handler-vm`, zone `us-central1-a`)
  — covered by GCP's Always Free tier, so this costs **$0/mo**.
- **Firestore** (native mode, same project) for `items`/`uploads` data.
- **Caddy** on the VM, reverse-proxying to gunicorn and handling HTTPS
  automatically via Let's Encrypt.
- A free **nip.io** hostname (`104-198-23-54.nip.io`) that resolves to the
  VM's static IP — no domain purchase needed, and Let's Encrypt can issue
  a real cert against it since it's a genuine public DNS name.

## Why e2-micro is slow, honestly

The rembg model (`isnet-general-use.onnx`, 178MB) plus gunicorn/Flask/
Firestore already resident pushes past the VM's 969MB of RAM, so it leans
on a 2GB swap file rather than crashing. Measured on this VM:

- **First background-removal job after a restart: ~6.7 minutes** (loading
  the model from a cold state while already under memory pressure).
- **A second job right after, model still warm: ~3 minutes.** Swapping
  doesn't go away once the model's loaded — the whole process's resident
  memory is bigger than physical RAM, so it stays swap-bound indefinitely,
  not just on the first job.

Nothing crashes — this is the swap-file mitigation working as intended
(slow instead of OOM-killed) — but "3+ minutes per photo, indefinitely" is
worse than the "fast after the first one" hope from the original estimate.
If that's too slow in practice, resizing is one command:

```bash
gcloud compute instances stop stuff-handler-vm --zone=us-central1-a
gcloud compute instances set-machine-type stuff-handler-vm --zone=us-central1-a --machine-type=e2-small
gcloud compute instances start stuff-handler-vm --zone=us-central1-a
```

`e2-small` (2 vCPU / 2GB RAM, ~$13/mo) gives the resident set room to
breathe without swapping at all — processing should drop to single-digit
seconds per photo. A few minutes of downtime during the resize.

## Layout on the VM

```
/opt/stuff-handler/          app code + venv (deployed via scp, not git clone)
  .venv/                     Python virtualenv (requirements-web.txt only)
  .env                       production secrets (chmod 600) — not in git
  done_output/, done_input/  photo library (came over with the initial deploy)
  uploads/originals/         staged upload originals
  uploads/processed/         staged processed results
/etc/caddy/Caddyfile         reverse proxy + automatic HTTPS config
/etc/systemd/system/stuff-handler.service   gunicorn, single worker
```

`stuff-handler.service` runs `gunicorn -w 1 --timeout 120 -b 127.0.0.1:8000
wsgi:app` — a single worker/thread (`UPLOAD_WORKERS=1` in `.env`) so only
one onnxruntime session is ever resident at a time, deliberately trading
concurrency for staying under the RAM ceiling. `Restart=always` means an
OOM-killed worker comes back on its own; any job that was mid-flight gets
marked failed on the next boot (`gallery/processing.py:mark_stale_jobs`) —
just hit Redo.

## The GCP-on-GCE service account scope gotcha

The VM's service account (`621888320606-compute@developer.gserviceaccount.com`)
has `roles/editor` at the project level, which includes Firestore access —
but that alone wasn't enough. GCE VMs have a *second*, separate permission
layer: OAuth **scopes** baked into the instance at creation time, which
cap what the metadata-server-issued access token can be used for
regardless of IAM role. This VM's default scopes (logging, monitoring,
storage-ro, etc.) didn't include `datastore`, so every Firestore call
failed with `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT` even though IAM said
yes. Fixed by stopping the VM and adding the scope:

```bash
gcloud compute instances stop stuff-handler-vm --zone=us-central1-a
gcloud compute instances set-service-account stuff-handler-vm --zone=us-central1-a \
  --scopes=datastore,storage-ro,logging-write,monitoring-write,pubsub,service-management,service-control,trace
gcloud compute instances start stuff-handler-vm --zone=us-central1-a
```

Worth remembering if you ever add another GCP API (e.g. Cloud Storage) —
IAM role changes alone won't be enough; the VM's scopes need it too.

## Updating the deployed code

There's no CI/CD or git-based deploy — the initial deploy was a `git
archive` tarball copied over with `gcloud compute scp` (the GitHub repo
was still public at the time and pushing to `main` wasn't authorized for
this session, so this sidestepped both issues; it also means the VM is
*not* simply tracking the GitHub repo). To push a code change:

```bash
cd /path/to/stuff-handler
tar -czf /tmp/update.tar.gz gallery/ requirements-web.txt   # add whatever changed
gcloud compute scp /tmp/update.tar.gz stuff-handler-vm:/tmp/ --zone=us-central1-a
gcloud compute ssh stuff-handler-vm --zone=us-central1-a --command='
  cd /opt/stuff-handler && tar -xzf /tmp/update.tar.gz && rm /tmp/update.tar.gz
  .venv/bin/pip install -q -r requirements-web.txt
  sudo systemctl restart stuff-handler
'
```

A restart means the next upload pays the cold-start model-load cost again
(see above).

## Firestore data

Migrated from the local MongoDB instance with a one-off script (not kept
in the repo — it was a straight per-document copy, `items`→`items` and
`uploads`→`uploads`, dropping Mongo's `_id` field and using it as the
Firestore document ID instead). Local MongoDB is untouched and still has
its own copy; Firestore is now the authoritative source for the deployed
site and for local dev (both point at the same `stuff-handler-app`
Firestore database — there's no separate "local" vs "prod" database).

## Access control

`SITE_PASSWORD` is set in the VM's `.env` — see the README's "Access
control" section for how the login gate works. Change the password by
editing `/opt/stuff-handler/.env` on the VM and restarting the service.

## Resource inventory (for cleanup/reference)

| Resource | Name |
|---|---|
| GCP project | `stuff-handler-app` |
| Compute Engine VM | `stuff-handler-vm` (zone `us-central1-a`) |
| Static IP | `stuff-handler-ip` → `104.198.23.54` |
| Firewall rule | `allow-http-https` (tcp:80,443 → tag `stuff-handler`) |
| Firestore database | `(default)`, native mode, `us-central1` |
