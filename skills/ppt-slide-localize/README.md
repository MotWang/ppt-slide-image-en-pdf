# ppt-slide-localize (Cursor Agent Skill pack)

Localize PPT/PDF slides to **en / zh / ko / ja** via reference-image generation (Cursor `GenerateImage`), strip watermarks, assemble PDF.

**Full usage:** see repo root [README.md](../../README.md) — website submit, batch limits (~100 pages), API bridge, Automation continue loop.

## Install for local Agent

Copy this folder into a Cursor project:

```bash
cp -R skills/ppt-slide-localize /path/to/your-project/.cursor/skills/ppt-slide-localize
```

Or user skills:

```bash
mkdir -p ~/.cursor/skills
cp -R skills/ppt-slide-localize ~/.cursor/skills/ppt-slide-localize
```

Then in Agent chat: ask to run **ppt-slide-localize** on a PDF with `target_lang=en|zh|ko|ja`.

## Layout in this pack

```text
ppt-slide-localize/
  SKILL.md
  README.md
  scripts/     # export, checkpoint, collect, assemble, sync
  web/         # colleague HTML + FastAPI
```

When using the pack standalone, treat this folder as the workflow root:

```bash
python3 scripts/export_slides.py --input deck.pdf --out-dir runs/demo/pages_src
python3 web/server.py   # http://127.0.0.1:8787
```

The SKILL.md also documents paths under `workflows/ppt-slide-image-en-pdf/` when used inside a monorepo; map those to this pack’s `scripts/` + `runs/` when installed alone.

## Colleague UI

```bash
pip install -r web/requirements.txt
python3 web/server.py
# open http://127.0.0.1:8787/
```

- Max pages / upload / batch: same as hosted (`MAX_PAGES=100`, `MAX_UPLOAD_MB=150`, batch 5–10)
- API: `POST /v1/jobs` → Agent batches via `/pages` + `/continue` → download when `assembled`

## Requirements

- Python 3.10+
- `pillow`, preferably `pymupdf`
- Cursor Agent with image generation (`GenerateImage` + `reference_image_paths`)
