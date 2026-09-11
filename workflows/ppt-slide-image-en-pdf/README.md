# PPT / PDF Slide Localize (zh · en · ko · ja)

Reusable workflow: recreate each slide as a **localized** image with the **same layout**, strip watermarks, assemble a PDF, sync to GitHub.

**Usage (full):** see repo root [README.md](../../README.md).

## Why image-first

In-file text translation often breaks fonts and cards. Reference-image generation (Cursor `GenerateImage` + `reference_image_paths`) keeps structure and rewrites text into **`en` / `zh` / `ko` / `ja`**. Watermarks are removed (not translated).

## Limits (hosted / local API)

- Max **~100 pages** per deck
- Upload up to **150MB**
- **Batch size** 5 / 8 / 10 — Agent processes one batch per Automation turn, then `POST /v1/jobs/{id}/continue`

## Folders

| Path | Purpose |
|------|---------|
| `input/` | Original PPTX/PDF |
| `pages_src/` | Source page PNGs |
| `pages_out/` | Localized page PNGs (canonical) |
| `pages_en/` | Legacy English-only output |
| `output/` | Final PDF + manifest |
| `runs/<run_id>/` | Per-job snapshot + `job.json` |
| `web/` | Colleague HTML + FastAPI job API |
| `scripts/` | Export / checkpoint / collect / assemble / sync |

## Resume a long run

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/run_checkpoint.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out
# only generate missing pages, then:
python3 workflows/ppt-slide-image-en-pdf/scripts/collect_en_pages.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out \
  --from "/path/to/generated/assets"
```

## Agent skill

`.cursor/skills/ppt-slide-image-en-pdf/SKILL.md` (name: **ppt-slide-localize**)

Downloadable pack for other machines: `skills/ppt-slide-localize/`

## Colleague UI

```bash
# API (default http://127.0.0.1:8787)
python3 workflows/ppt-slide-image-en-pdf/web/server.py

# Open the HTML (or visit http://127.0.0.1:8787/)
open workflows/ppt-slide-image-en-pdf/web/index.html
```

Hosted: https://ppt-slide-localize.fly.dev/

Drop a PDF, pick language + batch size, submit. Agent/Automation processes `job.json` in batches.

## Commands

```bash
# 1) Export
python3 workflows/ppt-slide-image-en-pdf/scripts/export_slides.py \
  --input "/path/to/deck.pdf" \
  --out-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src"

# 2) Agent generates pages into pages_out/ via GenerateImage + reference

# 3) Assemble (match source resolution when possible)
python3 workflows/ppt-slide-image-en-pdf/scripts/assemble_pdf.py \
  --pages-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_out" \
  --match-src-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src" \
  --out "workflows/ppt-slide-image-en-pdf/runs/<run_id>/output/<run_id>_en.pdf" \
  --target-lang en

# 4) Sync
python3 workflows/ppt-slide-image-en-pdf/scripts/sync_github.py \
  --run-dir "workflows/ppt-slide-image-en-pdf" \
  --run-id "<run_id>" \
  --repo "YOUR_ORG/YOUR_REPO" \
  --push
```

## Current machine notes

- `pillow` + `pymupdf` for export/assemble; FastAPI/uvicorn for the web API (`pip install fastapi uvicorn python-multipart`).
- `gh` at `~/.local/bin/gh` when brew is unavailable.

## Cursor Automation

Webhook or new `runs/*/job.json` → cloud agent follows the skill (`target_lang` / `batch_size` from job). Large decks: one batch per run → upload pages → `/continue`.
