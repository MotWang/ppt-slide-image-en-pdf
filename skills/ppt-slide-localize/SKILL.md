---
name: ppt-slide-localize
description: >-
  Recreate PPT/PDF slides as localized page images (zh / en / ko / ja) via
  pluggable image engines (Cursor GenerateImage, Gemini/Imagen, OpenAI Images,
  ByteDance Seedream, Qwen/Wanxiang, fal) and optional LLMs (Cursor, Gemini,
  OpenAI, Claude, DeepSeek, Kimi, Qwen). Strip watermarks, assemble a multi-page
  PDF, sync to GitHub. Alias: ppt-slide-image-en-pdf. Use for slide-by-slide
  visual localization, investor decks, or image-first PPT→PDF — not XML text swap.
---

# PPT / PDF Slide Localize (zh · en · ko · ja)

## Goal

Avoid brittle in-file text translation. Instead:

1. Export each slide as a source image.
2. For **every page**, regenerate **with that page as visual reference**, same layout, **all readable text in `target_lang`**, **no watermarks** — using the **Image engine** selected in `providers`.
3. Use the selected **LLM** only for assistive reasoning (QA, prompt polish, bad-page triage) when helpful.
4. Assemble ordered page PNGs into one PDF; optionally sync to GitHub.

Legacy alias: `ppt-slide-image-en-pdf`.

**Agent products are interchangeable.** Read `job.json` / webhook `providers` + `secrets` first; route Image and LLM independently. Do not hard-code Cursor-only generation if the job asks for another engine.

## When to use

- User says: PPT 英文/中文/韩文/日文版、逐页重绘、去水印、合 PDF、同步 GitHub、换 Seedream / Gemini / Qwen 出图、用 DeepSeek / Kimi 辅助
- Source is `.pptx` / `.pdf` / `.zip` of page images / a folder of `p01.png`…
- Hosted site: https://ppt-slide-localize.fly.dev/ (PPTX→PDF→PNG; ZIP normalize)
- Colleague UI Advanced: **Image engine** + **LLM** (Site connection is hidden; same-origin API)

## Target language

| Code | Output language |
|------|-----------------|
| `en` | English |
| `zh` | Simplified Chinese (简体中文) |
| `ko` | Korean (한국어) |
| `ja` | Japanese (日本語) |

From user request or `job.json` → `target_lang` (default `en`).

---

## Provider switching (core)

### Read config first

On every job / webhook turn:

1. Load `providers` from webhook payload or `job.json`.
2. Load keys from webhook `secrets` (preferred) or env (Fly secrets). **Never print keys.**
3. Process **only** `batch_pages` (size ≤ `batch_size`, default 12, max 15).
4. Image path and LLM path are **independent** — mix freely (e.g. Seedream image + DeepSeek LLM).

Default when missing:

```json
{
  "image": "cursor",
  "image_model": "default",
  "llm": "cursor",
  "llm_model": "default"
}
```

### Image engines

| `providers.image` | Models (typical) | Key in `secrets` / env | How to generate |
|-------------------|------------------|------------------------|-----------------|
| `cursor` (default) | `default` | — | Cursor **`GenerateImage`** + `reference_image_paths` + `aspect_ratio: "16:9"` |
| `gemini` | `gemini-2.0-flash-preview-image-generation`, `imagen-3.0-generate-002`, `imagen-3.0-fast-generate-001` | `secrets.gemini` / `GEMINI_API_KEY` | Gemini / Imagen image API; pass source page as reference / edit input when API supports it |
| `openai` | `gpt-image-1`, `dall-e-3` | `secrets.openai` / `OPENAI_API_KEY` | OpenAI Images; prefer edit/reference when available, else strong layout prompt + ref description |
| `bytedance` | `doubao-seedream-5-0-260128`, `doubao-seedream-4-5-251128`, `doubao-seedream-4-0-250828`, `doubao-seededit-3-0-i2i-250628` | `secrets.bytedance` / `ARK_API_KEY` | Volcengine ModelArk Seedream; **prefer Seededit / i2i** when reference page exists |
| `qwen` | `wanx2.1-t2i-plus`, `wanx2.1-t2i-turbo`, `wanx-v1`, `qwen-image-plus` | `secrets.qwen` / `DASHSCOPE_API_KEY` | DashScope Wanxiang / Qwen-Image; use image-to-image / ref when supported |
| `fal` | `fal-ai/flux/dev`, `fal-ai/flux/schnell` | `secrets.fal` / `FAL_KEY` | fal.ai; pass reference image when endpoint allows |

**Image rules for every engine**

- One page → one API call → `pages_out/pXX.png`.
- Always condition on the source `pages_src/pXX.png` (tool ref path, i2i, or edit).
- Use the prompt templates below (language + shared suffix).
- If the chosen API fails / times out / returns unusable art: **fall back once** to Cursor `GenerateImage` for that page, note it in the job message, continue the batch.
- Missing key for a non-cursor engine: fail the page with a clear error **or** fall back to Cursor if the orchestrator is Cursor Automation — prefer fallback so the deck still progresses.

### LLM engines (assist only)

Orchestration (webhook → batch → upload → continue) may still be Cursor Automation. The **LLM preference** controls reasoning / QA / prompt assist:

| `providers.llm` | Models (typical) | Key | Use for |
|-----------------|------------------|-----|---------|
| `cursor` (default) | `default` | — | Built-in agent reasoning |
| `gemini` | `gemini-2.0-flash`, `gemini-2.5-pro`, … | `secrets.gemini` | Spot-check OCR/text completeness, rewrite prompts |
| `openai` | `gpt-4.1`, `gpt-4o`, `o4-mini` | `secrets.openai` | Same |
| `anthropic` | `claude-sonnet-4`, `claude-opus-4`, … | `secrets.anthropic` | Same |
| `deepseek` | `deepseek-chat`, `deepseek-reasoner`, `deepseek-v3.2` | `secrets.deepseek` | Same — strong for CN/EN QA |
| `kimi` | `kimi-k2.5`, `moonshot-v1-128k`, … | `secrets.kimi` | Same — long-context deck notes |
| `qwen` | `qwen-plus`, `qwen-max`, `qwen-turbo`, … | `secrets.qwen` | Same — shares DashScope key with image |

**Do not** replace the image pipeline with LLM-only text translation. LLM never draws the slide; Image engine does.

### Quality recipes (pick for best results)

| Goal | Suggested Image | Suggested LLM |
|------|-----------------|---------------|
| Fast / zero config | `cursor` | `cursor` |
| Sharp CJK + layout lock | `bytedance` Seedream / Seededit | `deepseek` or `qwen` |
| Strong EN investor polish | `openai` `gpt-image-1` or `gemini` Imagen | `openai` or `anthropic` |
| CN ecosystem stack | `qwen` Wanxiang | `qwen` or `kimi` |
| Experimental / Flux look | `fal` | `cursor` |

User/UI choice **wins** over this table when `providers` is set.

### Webhook contract (hosted)

Payload includes: `providers`, `secrets`, `batch_pages`, `urls.*` (with `agent_key`), `instruction`.

Agent loop:

1. Download `urls.pages_src_zip` (or input).
2. For each `n` in `batch_pages`: generate with **Image** route → upload PNG to `urls.pages_upload`.
3. Optional: **LLM** spot-check 1–2 pages; regenerate failures with Image route.
4. If more pages remain → `POST urls.continue`.
5. When complete → `POST urls.assemble` or `urls.result`.

Never share `agent_key` or `secrets`.

---

## Hard rules

- **One page = one generation call.** Never batch multiple slides into one image.
- Always condition on the source page (reference / i2i / edit).
- Preserve: aspect ratio (usually 16:9), section nav, card structure, logo placement, accent colors, footer stats.
- Translate **all visible UI/body text** into `target_lang`; keep brand names, tickers, proper nouns in common forms.
- **Remove all watermarks** (diagonal/tiled stamps, DRAFT/SAMPLE, agency stamps). Reconstruct as if none existed. Do **not** translate-and-keep stamps.
- Do **not** invent charts/numbers absent from the source.
- Do **not** leave the wrong script in body text.
- Filenames: `p01.png`, `p02.png`, … zero-padded.

## Directory layout (per run)

```text
workflows/ppt-slide-image-en-pdf/
  web/                   # colleague HTML + FastAPI job API
  scripts/
  runs/<run_id>/
    job.json             # target_lang, providers, batch_size, status, …
    input/
    pages_src/
    pages_out/           # canonical localized PNGs
    output/              # <run_id>_<lang>.pdf + manifest
```

### job.json (example)

```json
{
  "run_id": "20260911_example",
  "target_lang": "zh",
  "remove_watermarks": true,
  "batch_size": 12,
  "providers": {
    "image": "bytedance",
    "image_model": "doubao-seededit-3-0-i2i-250628",
    "llm": "deepseek",
    "llm_model": "deepseek-chat"
  },
  "status": "exported",
  "source_file": "input/deck.pdf"
}
```

Statuses: `queued` → `exported` → `generating` → `assembled` → `failed`.

## Pipeline

### 0) Confirm inputs

Ask only if missing: source, `target_lang`, optional GitHub dest. If `job.json` exists, use it (including `providers`).

### 1) Export → `pages_src/`

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/export_slides.py \
  --input "/path/to/deck.pdf" \
  --out-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>/pages_src"
```

PDF: pymupdf → pypdfium2 → pdftoppm. PPTX: soffice → PDF → PNG. ZIP/folder → `pXX.png`.

### 1b) Checkpoint / resume

```bash
python3 workflows/ppt-slide-image-en-pdf/scripts/run_checkpoint.py \
  --run-dir "workflows/ppt-slide-image-en-pdf/runs/<run_id>" \
  --pages-out pages_out
```

Exit `0` = complete; `2` = missing. **Never restart from p01** if `pages_out/` has pages.

### 2) Recreate missing pages in batches

- Default batch **12** (max **15**). Parallelize **within** one batch only, same Image engine.
- Up to ~100 pages; persist after each batch; `POST …/continue` on hosted jobs.
- Skip existing `pages_out/pXX.png`.

Per page in current batch:

1. Route to Image engine (`providers.image` + `image_model`) with source reference.
2. Save `pages_out/pXX.png` (+ upload if hosted).
3. Optional LLM QA on a sample; regenerate bad pages only (same Image engine, or Cursor fallback).

#### Generation prompt templates

**Shared suffix (all languages / all image engines):**

```text
Keep the SAME layout, composition, card structure, spacing, logo placement,
color palette, typography hierarchy, and 16:9 framing as the reference image.
Keep brand names and proper nouns accurate.
REMOVE every watermark / stamp overlay / tiled translucent mark completely;
paint the slide as if no watermark existed. Do not redraw or translate watermarks.
Do not add new sections, charts, or numbers that are not in the reference.
High-resolution, sharp text, presentation-slide quality.
```

**`en`:** Clean **English** version. Translate ALL visible text. No leftover source-language body text.

**`zh`:** 清晰**简体中文**版。全部可见正文译为自然专业简体中文。品牌名用惯例写法。

**`ko`:** 깔끔한 **한국어** 버전. 모든 보이는 본문을 자연스럽고 전문적인 한국어로. 브랜드명 관용 표기.

**`ja`:** きれいな**日本語**版。表示テキストはすべて自然で専門的な日本語に。ブランド名は慣用表記。

### 3) Assemble PDF

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

- Hosted: https://ppt-slide-localize.fly.dev/ (UI: **EN | 한국어** toggle; Korean browser defaults to 한국어)
- Korean quick start: `skills/ppt-slide-localize/QUICKSTART.ko.md`
- Pack: `skills/ppt-slide-localize/` · in-repo skill: `.cursor/skills/ppt-slide-image-en-pdf/SKILL.md`
- Submit: file + language + batch size + **Image engine** + **LLM** (+ keys if not on Fly secrets)
- Local API: `python3 workflows/ppt-slide-image-en-pdf/web/server.py`

## Quality bar

- `pages_out` count == `pages_src` count
- Language matches `target_lang`; **no watermarks**
- PDF order `p01…pN`; manifest includes `target_lang`
- Honored `providers.image` / `providers.llm` (or documented Cursor fallback)

## Automation / any agent host

- Trigger: webhook or new `runs/*/job.json`
- Always honor `providers` + `batch_pages`
- Export if needed → generate missing via Image route → optional LLM QA → assemble → update status

## Do not

- Translate by dumping PPTX XML / replacing runs only
- Merge multiple slides into one generated image
- Keep or translate watermarks
- Ignore `providers` and always use Cursor when another engine was requested (except documented fallback)
- Force-push or commit secrets / echo API keys

## Quick invoke

User: “把这份 BP 做成韩文版，用 Seedream 出图，DeepSeek 辅助质检，去水印合 PDF”

Agent: `target_lang=ko`, `providers.image=bytedance`, `providers.llm=deepseek` → export → batch generate (Seedream/Seededit) → DeepSeek spot-check → assemble.
