#!/usr/bin/env python3
"""Public job API + UI for PPT/PDF slide localization.

Env:
  PORT                 default 8787
  HOST                 default 0.0.0.0 (cloud) — use 127.0.0.1 locally if desired
  DATA_DIR             optional absolute path for runs/ (persistent volume)
  ACCESS_TOKEN         if set, require header X-Access-Token or ?token=
  CURSOR_WEBHOOK_URL   optional; POST JSON when a job is created/exported
  PUBLIC_BASE_URL      optional; included in webhook payload for callbacks
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
except ImportError:
    print("Install deps: pip install fastapi uvicorn python-multipart pymupdf pillow", file=sys.stderr)
    raise

WEB_DIR = Path(__file__).resolve().parent
WORKFLOW_ROOT = WEB_DIR.parent
SCRIPTS = WORKFLOW_ROOT / "scripts"

_data = os.environ.get("DATA_DIR", "").strip()
RUNS = Path(_data).expanduser().resolve() / "runs" if _data else (WORKFLOW_ROOT / "runs")

ALLOWED_LANGS = {"en", "zh", "ko", "ja"}
SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
CURSOR_WEBHOOK_URL = os.environ.get("CURSOR_WEBHOOK_URL", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Large BP support (60–70+ pages); hard cap keeps runaway jobs bounded.
MAX_PAGES = int(os.environ.get("MAX_PAGES", "100"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "150"))
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))

app = FastAPI(title="PPT Slide Localize API", version="1.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_access(
    x_access_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    if not ACCESS_TOKEN:
        return
    provided = (x_access_token or token or "").strip()
    if provided != ACCESS_TOKEN:
        raise HTTPException(401, "Invalid or missing access token")


def make_run_id(filename: str) -> str:
    stem = Path(filename).stem
    slug = SLUG_RE.sub("_", stem).strip("_").lower()[:40] or "deck"
    day = datetime.now().strftime("%Y%m%d")
    base = f"{day}_{slug}"
    run_id = base
    n = 2
    while (RUNS / run_id).exists():
        run_id = f"{base}_{n}"
        n += 1
    return run_id


def read_job(run_dir: Path) -> dict:
    path = run_dir / "job.json"
    if not path.exists():
        raise HTTPException(404, f"Job not found: {run_dir.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(run_dir: Path, job: dict) -> None:
    job["updated_at"] = utc_now()
    (run_dir / "job.json").write_text(
        json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def checkpoint_dict(run_dir: Path) -> dict:
    script = SCRIPTS / "run_checkpoint.py"
    if not script.exists():
        return {"error": "run_checkpoint.py missing"}
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run-dir",
            str(run_dir),
            "--pages-out",
            "pages_out",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {"raw": proc.stdout, "stderr": proc.stderr}
    data["exit_code"] = proc.returncode
    return data


def try_export(run_dir: Path, source: Path) -> int:
    script = SCRIPTS / "export_slides.py"
    out_dir = run_dir / "pages_src"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(script), "--input", str(source), "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "export_slides failed")
    n = len(list(out_dir.glob("p*.png")))
    return n


def build_webhook_payload(job: dict, event: str = "job.created") -> dict:
    base = PUBLIC_BASE_URL or ""
    run_id = job.get("run_id")
    cp = {}
    run_dir = RUNS / str(run_id) if run_id else None
    if run_dir and run_dir.exists():
        cp = checkpoint_dict(run_dir)
    missing = cp.get("missing") or []
    batch_size = int(job.get("batch_size") or DEFAULT_BATCH_SIZE)
    batch_pages = missing[:batch_size]
    return {
        "event": event,
        "run_id": run_id,
        "target_lang": job.get("target_lang"),
        "remove_watermarks": job.get("remove_watermarks", True),
        "status": job.get("status"),
        "page_count": job.get("page_count"),
        "batch_size": batch_size,
        "batch_pages": batch_pages,
        "next_page": batch_pages[0] if batch_pages else None,
        "missing_count": len(missing),
        "progress": cp.get("progress"),
        "public_base_url": base or None,
        "urls": {
            "job": f"{base}/v1/jobs/{run_id}" if base else None,
            "input": f"{base}/v1/jobs/{run_id}/input" if base else None,
            "pages_src_zip": f"{base}/v1/jobs/{run_id}/pages_src.zip" if base else None,
            "pages_upload": f"{base}/v1/jobs/{run_id}/pages" if base else None,
            "continue": f"{base}/v1/jobs/{run_id}/continue" if base else None,
            "download": f"{base}/v1/jobs/{run_id}/download" if base else None,
            "result": f"{base}/v1/jobs/{run_id}/result" if base else None,
        },
        "instruction": (
            "Large-deck batch mode: process ONLY batch_pages (up to batch_size) this run. "
            "Download pages_src.zip or input; GenerateImage each missing page in the batch; "
            "POST PNGs to urls.pages_upload. If more pages remain, POST urls.continue to "
            "re-fire webhook. When complete, assemble PDF and POST urls.result. "
            "Skill: ppt-slide-localize."
        ),
    }


def notify_cursor_webhook(job: dict, event: str = "job.created") -> None:
    if not CURSOR_WEBHOOK_URL:
        return
    payload = build_webhook_payload(job, event=event)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CURSOR_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ppt-slide-localize/1.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            job["webhook_status"] = resp.status
            job["webhook_error"] = None
            job["last_webhook_event"] = event
    except urllib.error.HTTPError as e:
        job["webhook_status"] = e.code
        job["webhook_error"] = e.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.URLError as e:
        job["webhook_error"] = str(e)
    except Exception as e:
        job["webhook_error"] = str(e)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = WEB_DIR / "index.html"
    if not html.exists():
        raise HTTPException(404, "index.html missing")
    return HTMLResponse(html.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "workflow_root": str(WORKFLOW_ROOT),
        "runs": str(RUNS),
        "time": utc_now(),
        "auth_required": bool(ACCESS_TOKEN),
        "webhook_configured": bool(CURSOR_WEBHOOK_URL),
        "max_pages": MAX_PAGES,
        "max_upload_mb": MAX_UPLOAD_MB,
        "default_batch_size": DEFAULT_BATCH_SIZE,
    }


@app.post("/v1/jobs")
async def create_job(
    file: UploadFile = File(...),
    target_lang: str = Form("en"),
    remove_watermarks: str = Form("true"),
    auto_export: str = Form("true"),
    batch_size: str = Form(str(DEFAULT_BATCH_SIZE)),
    _: None = Depends(require_access),
) -> JSONResponse:
    lang = (target_lang or "en").strip().lower()
    if lang not in ALLOWED_LANGS:
        raise HTTPException(400, f"target_lang must be one of {sorted(ALLOWED_LANGS)}")

    try:
        bsz = int(str(batch_size).strip() or DEFAULT_BATCH_SIZE)
    except ValueError:
        bsz = DEFAULT_BATCH_SIZE
    bsz = max(1, min(bsz, 10))

    if not file.filename:
        raise HTTPException(400, "file required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".pptx", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(400, "Supported: .pdf, .pptx, .png, .jpg")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB}MB)")

    RUNS.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id(file.filename)
    run_dir = RUNS / run_id
    input_dir = run_dir / "input"
    input_dir.mkdir(parents=True)
    (run_dir / "pages_out").mkdir(parents=True, exist_ok=True)
    (run_dir / "output").mkdir(parents=True, exist_ok=True)

    dest_name = SLUG_RE.sub("_", Path(file.filename).name)
    dest = input_dir / dest_name
    dest.write_bytes(data)

    remove_wm = str(remove_watermarks).lower() in {"1", "true", "yes", "on"}
    do_export = str(auto_export).lower() in {"1", "true", "yes", "on"}

    job = {
        "run_id": run_id,
        "target_lang": lang,
        "remove_watermarks": remove_wm,
        "pages_out_dir": "pages_out",
        "batch_size": bsz,
        "max_pages": MAX_PAGES,
        "page_count": None,
        "status": "queued",
        "source_file": f"input/{dest_name}",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "error": None,
        "message": "Queued. Waiting for Cursor Agent / Automation (batch mode for large decks).",
    }
    write_job(run_dir, job)

    if do_export and suffix in {".pdf", ".pptx"}:
        try:
            n = try_export(run_dir, dest)
            job["page_count"] = n
            if n > MAX_PAGES:
                job["status"] = "failed"
                job["error"] = f"Too many pages ({n}). Max supported is {MAX_PAGES}."
                job["message"] = job["error"]
                write_job(run_dir, job)
                raise HTTPException(413, detail=job)
            job["status"] = "exported"
            job["message"] = (
                f"Exported {n} pages. Agent will process in batches of {bsz}."
            )
            write_job(run_dir, job)
        except HTTPException:
            raise
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            job["message"] = "Export failed. Fix tools (pymupdf) or upload page PNGs."
            job["traceback"] = traceback.format_exc()[-2000:]
            write_job(run_dir, job)
            raise HTTPException(500, detail=job)

    notify_cursor_webhook(job, event="job.created")
    write_job(run_dir, job)

    body = {**job, "checkpoint": checkpoint_dict(run_dir)}
    return JSONResponse(body, status_code=201)


@app.get("/v1/jobs")
def list_jobs(_: None = Depends(require_access)) -> dict:
    RUNS.mkdir(parents=True, exist_ok=True)
    items = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        if d.is_dir() and (d / "job.json").exists():
            try:
                items.append(read_job(d))
            except Exception:
                continue
    return {"jobs": items[:50]}


@app.get("/v1/jobs/{run_id}")
def get_job(run_id: str, _: None = Depends(require_access)) -> dict:
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    cp = checkpoint_dict(run_dir)
    out_pdfs = list((run_dir / "output").glob("*.pdf")) if (run_dir / "output").exists() else []
    if cp.get("complete") and out_pdfs and job.get("status") not in {"assembled", "failed"}:
        job["status"] = "assembled"
        job["message"] = "Localized PDF ready."
        job["output_pdf"] = str(out_pdfs[0].relative_to(run_dir))
        write_job(run_dir, job)
    return {**job, "checkpoint": cp, "output_pdfs": [p.name for p in out_pdfs]}


@app.get("/v1/jobs/{run_id}/download")
def download_job(run_id: str, _: None = Depends(require_access)) -> FileResponse:
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    out_dir = run_dir / "output"
    pdfs = sorted(out_dir.glob("*.pdf")) if out_dir.exists() else []
    if not pdfs:
        raise HTTPException(
            404, "PDF not ready yet. Ask the Agent to finish generate + assemble."
        )
    lang = job.get("target_lang") or ""
    preferred = [p for p in pdfs if f"_{lang}." in p.name or p.name.endswith(f"_{lang}.pdf")]
    path = preferred[0] if preferred else pdfs[0]
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.get("/v1/jobs/{run_id}/input")
def download_input(run_id: str, _: None = Depends(require_access)) -> FileResponse:
    """Source file for Cursor Automation / Agent to download."""
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    rel = job.get("source_file") or ""
    path = run_dir / rel
    if not path.is_file():
        input_dir = run_dir / "input"
        files = sorted(input_dir.glob("*")) if input_dir.exists() else []
        files = [p for p in files if p.is_file()]
        if not files:
            raise HTTPException(404, "Input file not found")
        path = files[0]
    return FileResponse(path, filename=path.name)


@app.get("/v1/jobs/{run_id}/pages_src.zip")
def download_pages_src_zip(run_id: str, _: None = Depends(require_access)) -> FileResponse:
    import tempfile
    import zipfile

    run_dir = RUNS / run_id
    read_job(run_dir)
    src = run_dir / "pages_src"
    pages = sorted(src.glob("p*.png")) if src.exists() else []
    if not pages:
        raise HTTPException(404, "pages_src empty — export first")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in pages:
            zf.write(p, arcname=p.name)
    return FileResponse(tmp.name, filename=f"{run_id}_pages_src.zip", media_type="application/zip")


@app.post("/v1/jobs/{run_id}/pages")
async def upload_pages(
    run_id: str,
    files: list[UploadFile] = File(...),
    _: None = Depends(require_access),
) -> dict:
    """Upload one batch of localized page PNGs (pXX.png)."""
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    out = run_dir / "pages_out"
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "").name
        if not re.match(r"^p\d+\.png$", name, re.I):
            continue
        data = await f.read()
        if not data:
            continue
        dest = out / name.lower().replace(".PNG", ".png")
        # normalize to pXX.png zero-pad via regex
        m = re.match(r"^p(\d+)\.png$", name, re.I)
        if m:
            dest = out / f"p{int(m.group(1)):02d}.png"
        dest.write_bytes(data)
        saved.append(dest.name)
    job["status"] = "generating"
    cp = checkpoint_dict(run_dir)
    job["message"] = f"Received {len(saved)} page(s). Progress {cp.get('progress')}."
    write_job(run_dir, job)
    return {"saved": saved, "checkpoint": cp, **{k: job[k] for k in ("run_id", "status", "message")}}


@app.post("/v1/jobs/{run_id}/continue")
def continue_job(run_id: str, _: None = Depends(require_access)) -> dict:
    """Re-fire webhook for the next batch when pages remain."""
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    cp = checkpoint_dict(run_dir)
    if cp.get("complete"):
        job["status"] = "generating"
        job["message"] = "All pages present locally on server; assemble PDF and POST /result."
        write_job(run_dir, job)
        notify_cursor_webhook(job, event="job.assemble")
        write_job(run_dir, job)
        return {**job, "checkpoint": cp, "continued": False, "reason": "complete"}
    job["status"] = "generating"
    job["message"] = (
        f"Continuing batch. Progress {cp.get('progress')}; "
        f"next p{cp.get('next_page'):02d}."
        if cp.get("next_page")
        else f"Continuing. Progress {cp.get('progress')}."
    )
    write_job(run_dir, job)
    notify_cursor_webhook(job, event="job.batch")
    write_job(run_dir, job)
    return {**job, "checkpoint": cp, "continued": True}


@app.post("/v1/jobs/{run_id}/result")
async def upload_result(
    run_id: str,
    file: UploadFile = File(...),
    status: str = Form("assembled"),
    message: str = Form(""),
    _: None = Depends(require_access),
) -> dict:
    """Agent uploads the localized PDF when finished."""
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    if not file.filename:
        raise HTTPException(400, "file required")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    out_dir = run_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    lang = job.get("target_lang") or "en"
    canonical = out_dir / f"{run_id}_{lang}.pdf"
    canonical.write_bytes(data)

    st = (status or "assembled").strip().lower()
    if st not in {"assembled", "failed", "generating"}:
        st = "assembled"
    job["status"] = st
    job["output_pdf"] = str(canonical.relative_to(run_dir))
    job["message"] = message or (
        "Localized PDF ready." if st == "assembled" else job.get("message")
    )
    job["error"] = None if st != "failed" else (message or "failed")
    write_job(run_dir, job)
    return {**job, "checkpoint": checkpoint_dict(run_dir)}


@app.patch("/v1/jobs/{run_id}/status")
async def patch_status(
    run_id: str,
    status: str = Form(...),
    message: str = Form(""),
    _: None = Depends(require_access),
) -> dict:
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    st = status.strip().lower()
    if st not in {"queued", "exported", "generating", "assembled", "failed"}:
        raise HTTPException(400, "invalid status")
    job["status"] = st
    if message:
        job["message"] = message
    write_job(run_dir, job)
    return job


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("pip install uvicorn fastapi python-multipart", file=sys.stderr)
        sys.exit(1)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8787"))
    RUNS.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
