---
name: ppt-slide-localize
description: >-
  Recreate PPT/PDF slides as localized page images (zh / en / ko / ja) via
  reference-image generation (Cursor GenerateImage), strip watermarks, assemble
  a multi-page PDF, then sync to GitHub. Also known as ppt-slide-image-en-pdf.
  Use for slide-by-slide visual localization, investor-style decks, or image-first
  PPT→PDF workflows instead of text extraction/translation.
---

# PPT / PDF Slide Localize (zh · en · ko · ja)

## Goal

Avoid brittle in-file text translation. Instead:

1. Export each slide as a source image.
2. For **every page**, call image generation **with that page as reference**, same layout, **all readable text in `target_lang`**, **no watermarks**.
3. Assemble ordered page PNGs into one PDF.
4. Commit and push the run folder to GitHub (optional).

Legacy alias: `ppt-slide-image-en-pdf` (English-only wording in old chats still maps here).

## When to use

- User says: PPT 英文/中文/韩文/日文版、逐页重绘、去水印、合 PDF、同步 GitHub
- Source is `.pptx` / `.pdf` / `.zip` of page images / a folder of `p01.png`… page images
- Hosted site: PPTX → LibreOffice PDF → PNG; ZIP of PNGs normalized to `pXX.png`
- Colleagues submit via `workflows/.../web/` HTML + job API

## Target language

`target_lang` must be one of:

| Code | Output language |
|------|-----------------|
| `en` | English |
| `zh` | Simplified Chinese (简体中文) |
| `ko` | Korean (한국어) |
| `ja` | Japanese (日本語) |

Read from user request or `runs/<run_id>/job.json` → `target_lang` (default `en`).

## Providers (Advanced UI)

`job.json` → `providers` (and webhook `providers` / `secrets`):

| Field | Options | Notes |
|-------|---------|--------|
| `image` | `cursor` (default) · `gemini` · `openai` · `fal` | How each page is redrawn |
| `image_model` | depends on provider | e.g. Imagen / gpt-image-1 |
| `llm` | `cursor` (default) · `gemini` · `openai` · `anthropic` | Preferred reasoning API |
| `llm_model` | depends on provider | |

- **`cursor` image** → use Cursor `GenerateImage` + `reference_image_paths` (no external key).
- **External image** → call that provider’s image API with `providers.image_model`; keys arrive in webhook `secrets` (or Fly `GEMINI_API_KEY` / `OPENAI_API_KEY` / `FAL_KEY`). Fall back to `GenerateImage` only if the API fails.
- **LLM ≠ cursor** → prefer that model for any text/reasoning assist; orchestration webhook still comes from Cursor Automation.
- Never log or echo `secrets` / `provider_keys` / `agent_key`.

Site **Access token** / **API base URL** in Advanced are for the *job API host* only — not AI vendor keys.

## Hard rules

- **One page = one generation call.** Never batch multiple slides into one image.
- Always pass the source page path in `reference_image_paths`.
- Preserve: aspect ratio (usually 16:9), section nav, card structure, logo placement, accent colors, footer stats.
- Translate **all visible UI/body text** into `target_lang`; keep brand names, tickers, proper nouns in commonly used forms.
- **Remove all watermarks** (diagonal/tiled stamps, DRAFT/SAMPLE, agency stamps, translucent repeated logos). Reconstruct as if no watermark existed. Do **not** translate and keep watermarks. Small legitimate chrome footers (e.g. single-line “Company Confidential (5)”) may stay, translated into `target_lang`, unless the user asks to strip footers; stamp-style overlays always go.
- Do **not** invent charts/numbers not on the source slide.
- Do **not** leave the *wrong* script in the output (e.g. leftover source-language body text when targeting another language). Brand logos that are graphical may stay.
- Filename convention: `p01.png`, `p02.png`, … zero-padded.

## Directory layout (per run)

**Always isolate each deck under `runs/<run_id>/`.**

```text
workflows/ppt-slide-image-en-pdf/
  web/                   # colleague HTML + FastAPI job API
  scripts/
  runs/<run_id>/
    job.json             # target_lang, remove_watermarks, status, …
    input/               # original upload
    pages_src/           # exported source page images
    pages_out/           # localized page images (canonical)
    pages_en/            # legacy alias only when target_lang=en (optional mirror)
    output/              # <run_id>_<lang>.pdf + manifest
```

Suggested `run_id`: `YYYYMMDD_<deck_slug>` (e.g. `20260911_example_bp`).

### job.json (minimum)

```json
{
  "run_id": "20260911_example",
  "target_lang": "en",
  "remove_watermarks": true,
  "status": "exported",
  "source_file": "input/deck.pdf"
}
```

Statuses: `queued` → `exported` → `generating` → `assembled` → `failed`.

## Pipeline

### 0) Confirm inputs

Ask only if missing:

- Source: pptx / pdf / `pages_src/`
- `target_lang`: `en` | `zh` | `ko` | `ja` (default `en`)
- GitHub destination if syncing

If `job.json` exists, use it.

### 1) Export → `runs/<run_id>/pages_src/`

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/export_slides.py \
  --input "/path/to/deck.pdf" \
  --out-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src"
```

PDF: **pymupdf → pypdfium2 → pdftoppm**. PPTX: **soffice → PDF → PNG**. ZIP/folder: normalize images to `pXX.png`.

### 1b) Checkpoint / resume

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/run_checkpoint.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out
```

- Exit `0` = complete; exit `2` = missing pages.
- **Never restart from p01** if `pages_out/` already has pages — only generate **missing** pages.
- After each `GenerateImage`, copy into `pages_out/pXX.png` immediately (`collect_en_pages.py` still works; prefer `--pages-out pages_out`).
- Session timeout → re-run checkpoint and resume. That is recovery, not a bug.

### 2) Recreate missing pages **in batches** (large BP)

**Speed / scale rules:**

- Work only under `runs/<run_id>/` (or Fly job workspace via website APIs).
- **Batch size** default **12** (from `job.json` → `batch_size`, max **15**). Parallel `GenerateImage` within one batch only. Prefer **10–12** for speed; drop to **8** if timeouts; **15** is the hard ceiling for one Automation turn.
- Supported decks: up to **~100 pages** (60–70 page BPs are normal). Never try to finish 70 pages in one Automation turn if time is tight — finish one batch, persist pages, continue.
- Long decks: do **not** full-`Read` every page first; use `reference_image_paths` + language prompt.
- Skip pages that already exist in `pages_out/`.
- After each batch: upload PNGs to the job API (`POST …/pages`) when running against the hosted site; then `POST …/continue` to re-fire webhook for the next batch.
- When checkpoint is complete: assemble PDF and `POST …/result`.

For each missing page in the **current batch only**:

1. `GenerateImage` with reference path, `aspect_ratio: "16:9"`, filename `pXX.png`, description = language template below.
2. Save to `pages_out/pXX.png` (and upload to server if hosted).
3. Spot-check a sample; regenerate bad pages only.

#### Generation prompt templates

**Shared suffix (all languages):**

```text
Keep the SAME layout, composition, card structure, spacing, logo placement,
color palette, typography hierarchy, and 16:9 framing as the reference image.
Keep brand names and proper nouns accurate.
REMOVE every watermark / stamp overlay / tiled translucent mark completely;
paint the slide as if no watermark existed. Do not redraw or translate watermarks.
Do not add new sections, charts, or numbers that are not in the reference.
High-resolution, sharp text, presentation-slide quality.
```

**`en`:** Recreate as a clean **English** version. Translate ALL visible text into natural professional English. Do not leave source-language body text.

**`zh`:** 将此页重绘为清晰的**简体中文**版本。把所有可见正文译为自然、专业的简体中文。保留品牌名惯例写法。

**`ko`:** 이 슬라이드를 깔끔한 **한국어** 버전으로 재구성하세요. 모든 보이는 본문을 자연스럽고 전문적인 한국어로 번역하세요. 브랜드명은 관용 표기를 유지하세요.

**`ja`:** このスライドをきれいな**日本語**版として再構成してください。表示テキストはすべて自然で専門的な日本語に翻訳。ブランド名は慣用表記を維持。

### 3) Assemble PDF (checkpoint complete)

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/run_checkpoint.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out

python3 workflows/ppt-slide-image-en-pdf/scripts/assemble_pdf.py \
  --pages-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_out" \
  --match-src-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src" \
  --out "workflows/ppt-slide-image-en-pdf/runs/<run_id>/output/<run_id>_<lang>.pdf" \
  --target-lang "<lang>"
```

### 4) Sync to GitHub (optional)

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/sync_github.py \
  --run-dir "workflows/ppt-slide-image-en-pdf" \
  --run-id "<run_id>" \
  --repo "owner/repo" \
  --branch "main" \
  --message "Add localized slide deck <run_id> (<lang>)" \
  --push
```

## Colleague web UI

- Open `workflows/ppt-slide-image-en-pdf/web/index.html` (or serve via the local API).
- Drop PDF, pick language, submit → API writes `runs/<run_id>/` + `job.json`.
- Agent / Automation picks up jobs with `status=exported` or `queued` and runs this skill.
- Start API: `python3 workflows/ppt-slide-image-en-pdf/web/server.py`

## Quality bar

- `pages_out` count == `pages_src` count
- Output language matches `target_lang`
- **No watermarks**
- PDF page order `p01…pN`
- Manifest includes `target_lang`

## Cursor Automation

- Trigger: webhook (or watch new `runs/*/job.json`)
- Read `target_lang` from `job.json`
- Follow this skill end-to-end (export if needed → generate missing → assemble → update job status)

## Do not

- Translate by dumping PPTX XML / replacing runs only
- Merge multiple slides into one generated image
- Keep, translate, or lightly fade watermarks
- Force-push or commit secrets
---

## Quick invoke

User: “把这份 BP 做成韩文版，去水印，合 PDF”

Agent: set `target_lang=ko`, export → generate → assemble (checkpoint resume if needed).
