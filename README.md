# PPT Slide Localize

Image-first PPT/PDF localization: recreate each slide as a localized page image (same layout), strip watermarks, assemble a multi-page PDF.

- **Languages:** English · Chinese (Simplified) · Korean · Japanese  
- **Large decks:** up to **~100 pages** (60–70 page BPs supported) via **batch** processing  
- **Colleague UI:** public site or local FastAPI + HTML  
- **Agent Skill:** `ppt-slide-localize` (pack under `skills/ppt-slide-localize/`)

**Live site:** https://ppt-slide-localize.fly.dev/  
**Repo:** https://github.com/MotWang/ppt-slide-image-en-pdf

Secrets (`ACCESS_TOKEN`, `CURSOR_WEBHOOK_URL`, `CURSOR_WEBHOOK_AUTH`, etc.) live only in Fly/Render env vars — never commit them. Do not commit deck PDFs or `runs/` page images.

---

## Usage guide

### 1. Web submit (recommended)

1. Open https://ppt-slide-localize.fly.dev/
2. Drop a **PDF / PPTX** (recommended ≤150MB)
3. Pick target language: `EN` / `zh` / `ko` / `ja`
4. Keep **Remove watermarks** checked (default)
5. Choose **Batch size** (pages per Automation turn): `5` (default) / `8` / `10`
6. Click **Submit job** and note the `run_id`
7. Wait until status is **`assembled`**, then download the PDF from the page

**Limits and conventions**

| Item | Detail |
|------|--------|
| Max pages | **100** (jobs over the limit fail with an error) |
| Upload size | **150MB** |
| Batching | Server splits work by `batch_size`; the agent generates only the current missing pages, uploads them, then `POST …/continue` starts the next batch |
| Resume | Checkpoint-safe; pages already in `pages_out/pXX.png` are skipped |
| Auth | Public site usually needs no token; if the API returns 401, paste the team `ACCESS_TOKEN` under Advanced |

Do not try to finish 60–70 pages in a single Automation turn. Rely on the site’s batch + continue loop.

### 2. Local API + UI

```bash
pip install -r workflows/ppt-slide-image-en-pdf/web/requirements.txt
python3 workflows/ppt-slide-image-en-pdf/web/server.py
# open http://127.0.0.1:8787/
```

Optional environment variables:

| Variable | Meaning | Default |
|----------|---------|---------|
| `MAX_PAGES` | Max pages per deck | `100` |
| `MAX_UPLOAD_MB` | Upload size limit (MB) | `150` |
| `DEFAULT_BATCH_SIZE` | Default batch size | `5` |
| `ACCESS_TOKEN` | Optional shared secret | empty (no auth) |
| `PUBLIC_BASE_URL` | Public URL (written into job callback links) | local origin |
| `CURSOR_WEBHOOK_URL` | Cursor Automation webhook URL | empty (job on disk only) |
| `CURSOR_WEBHOOK_AUTH` | Automation auth (`Bearer …` or raw `crsr_…` key) | empty (webhook calls fail with 401) |
| `WORKFLOW_ROOT` / `RUNS_DIR` | Workflow root / runs directory | see `web/server.py` |

### 3. Agent / Automation rules

Skill name: **`ppt-slide-localize`**  
In-repo path: `.cursor/skills/ppt-slide-image-en-pdf/SKILL.md`  
Distributable pack: `skills/ppt-slide-localize/`

Hard rules (summary):

1. **One page per** `GenerateImage` call, always with that page in `reference_image_paths`
2. Preserve layout / cards / logos / colors; **remove all watermarks** (do not translate and keep them)
3. Translate all visible body text into the target language; do not invent charts or numbers missing from the source
4. Output filenames: `p01.png`, `p02.png`, …
5. **Large BPs:** process only the current `batch_pages`; after upload call `/continue`; assemble the PDF only when the checkpoint is complete
6. Fill missing pages only — never restart the whole deck from p01

Hosted agent bridge APIs (same origin / token as the job):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/jobs/{id}` | Status and checkpoint |
| GET | `/v1/jobs/{id}/input` | Download source file |
| GET | `/v1/jobs/{id}/pages_src.zip` | Download source page zip |
| POST | `/v1/jobs/{id}/pages` | Upload this batch of `pXX.png` |
| POST | `/v1/jobs/{id}/continue` | Fire next-batch webhook when pages remain |
| POST | `/v1/jobs/{id}/result` | Upload final PDF |
| GET | `/v1/jobs/{id}/download` | Download finished PDF |
| GET | `/health` | Health check (`max_pages`, `default_batch_size`, …) |

Webhook payloads include `batch_pages`, `batch_size`, `urls.continue`, etc. Automation instructions should enforce: **this batch only → upload → continue (if incomplete) → when done, assemble + result**.

### 4. CLI pipeline (this repo)

```bash
# Export
python3 workflows/ppt-slide-image-en-pdf/scripts/export_slides.py \
  --input "/path/to/deck.pdf" \
  --out-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src"

# Checkpoint
python3 workflows/ppt-slide-image-en-pdf/scripts/run_checkpoint.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out

# Assemble (after the agent writes pages_out)
python3 workflows/ppt-slide-image-en-pdf/scripts/assemble_pdf.py \
  --pages-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_out" \
  --match-src-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src" \
  --out "workflows/ppt-slide-image-en-pdf/runs/<run_id>/output/<run_id>_en.pdf" \
  --target-lang en

# Optional: sync to GitHub
python3 workflows/ppt-slide-image-en-pdf/scripts/sync_github.py \
  --run-dir "workflows/ppt-slide-image-en-pdf" \
  --run-id "<run_id>" \
  --repo "MotWang/ppt-slide-image-en-pdf" \
  --push
```

Directory flow: `input/` → `pages_src/` → `pages_out/` → `output/`, one job per `runs/<run_id>/`.

### 5. Install the skill in another project

```bash
cp -R skills/ppt-slide-localize /path/to/project/.cursor/skills/ppt-slide-localize
# or user-level
cp -R skills/ppt-slide-localize ~/.cursor/skills/ppt-slide-localize
```

See [skills/ppt-slide-localize/README.md](skills/ppt-slide-localize/README.md).  
Workflow details: [workflows/ppt-slide-image-en-pdf/README.md](workflows/ppt-slide-image-en-pdf/README.md).

### 6. Deploy the hosted site (ops)

```bash
cd workflows/ppt-slide-image-en-pdf
fly apps create ppt-slide-localize          # once
fly volumes create localize_data --region nrt --size 1
fly secrets set PUBLIC_BASE_URL='https://ppt-slide-localize.fly.dev'
fly secrets set CURSOR_WEBHOOK_URL='https://api2.cursor.sh/automations/webhook/...'
fly secrets set CURSOR_WEBHOOK_AUTH='Bearer crsr_...'   # Generate auth header in Automations UI
# optional: fly secrets set ACCESS_TOKEN='...'
fly deploy
```

Webhook calls require **both** URL and auth. Without `CURSOR_WEBHOOK_AUTH`, jobs stay at `exported` and never start.

Or use [Render](https://render.com) with `workflows/ppt-slide-image-en-pdf/render.yaml`.

---

## Repo layout (short)

```text
.cursor/skills/ppt-slide-image-en-pdf/   # in-repo Agent skill
skills/ppt-slide-localize/               # distributable skill pack (scripts + web)
workflows/ppt-slide-image-en-pdf/        # production workflow + Fly/Render deploy
  web/          # index.html + FastAPI
  scripts/      # export / checkpoint / assemble / sync
  runs/         # local jobs (large images usually gitignored)
```
