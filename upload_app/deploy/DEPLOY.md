# OA-PDF Upload App — Deploy Runbook

Deploys `upload_app` on the existing EC2 host at `ai.spotitearly.com`,
fronted by Cloudflare Access (auth) + Cloudflare Tunnel (ingress).

## Prerequisites

- EC2 host already runs `cloudflared` for the existing backend.
- Postgres 14+ already running on `localhost:5432` with DB `sie-ai`.
- Repo checked out at `/home/ubuntu/sie-ai/acitracker_v1`.
- Python venv at `/home/ubuntu/sie-ai/acitracker_v1/.venv`.

## One-time setup

### 1. Install new dependencies into the venv

Add `flask` and `gunicorn` to `requirements.txt` (or install directly):

```bash
cd /home/ubuntu/sie-ai/acitracker_v1
source .venv/bin/activate
pip install "flask>=3.0,<4" "gunicorn>=21,<23"
```

### 2. Run the migration that adds pdf_store + pending_fetch

Postgres:

```bash
cd /home/ubuntu/sie-ai/acitracker_v1
source .venv/bin/activate
alembic upgrade head
```

(SQLite auto-migrates on first connection via `storage.sqlite_store._init_schema`.)

### 3. Create the PDF storage directory

```bash
sudo mkdir -p /home/ubuntu/sie-ai/pdfs
sudo chown ubuntu:ubuntu /home/ubuntu/sie-ai/pdfs
sudo chmod 750 /home/ubuntu/sie-ai/pdfs
```

### 4. Create the env file

```bash
sudo mkdir -p /etc/sie-ai
sudo tee /etc/sie-ai/upload-app.env >/dev/null <<'EOF'
DATABASE_URL=postgresql://sie-ai:postgres@localhost:5432/sie-ai
PDF_STORE_DIR=/home/ubuntu/sie-ai/pdfs
ALLOWED_UPLOADER_EMAILS=rom@spotitearly.com
UPLOAD_APP_SECRET_KEY=REPLACE_WITH_openssl_rand_hex_32
MAX_UPLOAD_BYTES=52428800
EOF
sudo chown root:ubuntu /etc/sie-ai/upload-app.env
sudo chmod 640 /etc/sie-ai/upload-app.env
```

Generate the secret key:

```bash
openssl rand -hex 32
```

…and paste it into `UPLOAD_APP_SECRET_KEY`.

### 5. Install the systemd unit

```bash
sudo cp upload_app/deploy/upload-app.service /etc/systemd/system/upload-app.service
sudo systemctl daemon-reload
sudo systemctl enable --now upload-app
sudo systemctl status upload-app
```

Smoke-test locally (should return `{"status":"ok"}`):

```bash
curl -s http://127.0.0.1:5005/healthz
```

### 6. Wire up Cloudflare Tunnel

Edit `/etc/cloudflared/config.yml` and add the ingress entry from
`upload_app/deploy/cloudflared-ingress-snippet.yml` **above** the
catch-all 404 rule. Then:

```bash
sudo cloudflared tunnel route dns <your-tunnel-name> ai.spotitearly.com
sudo systemctl restart cloudflared
```

### 7. Configure Cloudflare Access

In the CF dashboard → Zero Trust → Access → Applications:

1. **Add application** → Self-hosted
2. **Application domain:** `ai.spotitearly.com`
3. **Session duration:** 24h
4. **Policies:**
   - *SIE operators* — Action: Allow, Include: Emails =
     `rom@spotitearly.com` (+ any other operator emails)
   - *Health check bypass* — Action: Bypass, Path: `/healthz`,
     Include: Everyone

Verify:

```bash
# From your laptop: should bounce through CF Access login
open https://ai.spotitearly.com/pending
```

## GitHub Actions secrets to add

The Wednesday prep + Thursday reminder workflows expect these repo
secrets (beyond the ones `weekly-digest.yml` already uses):

| Secret                 | Value                                             |
|------------------------|---------------------------------------------------|
| `UNPAYWALL_EMAIL`      | `rom@spotitearly.com` (contact email for Unpaywall API) |
| `UPLOAD_BASE_URL`      | `https://ai.spotitearly.com` (upload app base URL) |

## Updating the app

Code changes land on `main` → pulled on-host by the Wednesday/Thursday
workflows. The upload_app service doesn't auto-reload, so after a code
change:

```bash
ssh ubuntu@ai.spotitearly.com
cd /home/ubuntu/sie-ai/acitracker_v1
git pull
sudo systemctl restart upload-app
```

## Observability

- **Logs:** `journalctl -u upload-app -f`
- **pending_fetch state:**
  ```bash
  psql -U sie-ai -d sie-ai -c "SELECT publication_id, week_start, status, alerted_at
                               FROM pending_fetch ORDER BY created_at DESC LIMIT 20;"
  ```
- **pdf_store state:**
  ```bash
  psql -U sie-ai -d sie-ai -c "SELECT publication_id, source_api, license, bytes_len, fetched_at
                               FROM pdf_store ORDER BY fetched_at DESC LIMIT 20;"
  ```
- **Disk usage:** `du -sh /home/ubuntu/sie-ai/pdfs`

## Rollback

The feature is opt-in on the digest side (`--attach-pdfs` flag), so you
can disable it by editing `.github/workflows/weekly-digest.yml` to drop
the flag. To take the upload app offline entirely:

```bash
sudo systemctl stop upload-app
sudo systemctl disable upload-app
```

(Leaves pdf_store / pending_fetch tables in place — harmless.)
