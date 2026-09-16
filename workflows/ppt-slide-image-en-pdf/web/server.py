#!/usr/bin/env python3
"""Public job API + UI for PPT/PDF slide localization.

Env:
  PORT                 default 8787
  HOST                 default 0.0.0.0 (cloud) — use 127.0.0.1 locally if desired
  DATA_DIR             optional absolute path for runs/ (persistent volume)
  ACCESS_TOKEN              if set, require header X-Access-Token or ?token=
  CURSOR_WEBHOOK_URL        optional; POST JSON when a job is created/exported
  CURSOR_WEBHOOK_AUTH       Bearer token for Cursor Automation
  PUBLIC_BASE_URL           optional; included in webhook payload for callbacks
  JOB_TTL_HOURS             auto-delete idle jobs (default 6)
  LEAVE_GRACE_SEC           delay after tab leave before purge (default 90)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
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
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
CURSOR_WEBHOOK_URL = os.environ.get("CURSOR_WEBHOOK_URL", "").strip()
CURSOR_WEBHOOK_AUTH = os.environ.get("CURSOR_WEBHOOK_AUTH", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

MAX_PAGES = int(os.environ.get("MAX_PAGES", "100"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "150"))
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
MAX_BATCH_SIZE = int(os.environ.get("MAX_BATCH_SIZE", "15"))
# Rough wall-clock seconds per page for ETA before measured rate exists.
ETA_SEC_PER_PAGE = float(os.environ.get("ETA_SEC_PER_PAGE", "70"))
JOB_TTL_HOURS = int(os.environ.get("JOB_TTL_HOURS", "6"))
LEAVE_GRACE_SEC = int(os.environ.get("LEAVE_GRACE_SEC", "90"))


def webhook_authorization_header() -> str | None:
    raw = CURSOR_WEBHOOK_AUTH
    if not raw:
        return None
    if raw.lower().startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.lower().startswith("bearer "):
        return raw
    return f"Bearer {raw}"


def webhook_ready() -> bool:
    return bool(CURSOR_WEBHOOK_URL and webhook_authorization_header())


app = FastAPI(title="PPT Slide Localize API", version="1.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def require_access(
    x_access_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    if not ACCESS_TOKEN:
        return
    provided = (x_access_token or token or "").strip()
    if provided != ACCESS_TOKEN:
        raise HTTPException(401, "Invalid or missing access token")


def require_session(
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    session: str | None = Query(default=None),
) -> str:
    tok = (x_session_token or session or "").strip()
    if not tok or not SESSION_RE.match(tok):
        raise HTTPException(401, "Missing or invalid X-Session-Token (private tab session)")
    return tok


def optional_session(
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    session: str | None = Query(default=None),
) -> str | None:
    tok = (x_session_token or session or "").strip()
    if tok and SESSION_RE.match(tok):
        return tok
    return None


def optional_agent_key(
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    agent_key: str | None = Query(default=None),
) -> str | None:
    tok = (x_agent_key or agent_key or "").strip()
    return tok or None


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


def public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in {"owner_token", "agent_key"}}


def delete_run_dir(run_dir: Path) -> None:
    if run_dir.exists() and run_dir.is_dir():
        shutil.rmtree(run_dir, ignore_errors=True)


def purge_expired_jobs() -> int:
    """Delete idle / left / legacy-public jobs."""
    RUNS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    removed = 0
    for d in list(RUNS.iterdir()):
        if not (d.is_dir() and (d / "job.json").exists()):
            continue
        try:
            job = json.loads((d / "job.json").read_text(encoding="utf-8"))
        except Exception:
            delete_run_dir(d)
            removed += 1
            continue
        # Legacy jobs without owner are not shareable — purge for privacy.
        if not job.get("owner_token"):
            delete_run_dir(d)
            removed += 1
            continue
        soft = parse_iso(job.get("soft_delete_at"))
        if soft and soft <= now:
            delete_run_dir(d)
            removed += 1
            continue
        updated = parse_iso(job.get("updated_at")) or parse_iso(job.get("created_at"))
        if updated and (now - updated) > timedelta(hours=JOB_TTL_HOURS):
            delete_run_dir(d)
            removed += 1
    return removed


def touch_session_jobs(owner: str) -> None:
    """Cancel pending leave-delete when the same tab returns."""
    RUNS.mkdir(parents=True, exist_ok=True)
    for d in RUNS.iterdir():
        if not (d.is_dir() and (d / "job.json").exists()):
            continue
        try:
            job = read_job(d)
        except Exception:
            continue
        if job.get("owner_token") != owner:
            continue
        if job.pop("soft_delete_at", None) is not None:
            write_job(d, job)


def job_progress(run_dir: Path, job: dict) -> dict:
    out = run_dir / "pages_out"
    done = len(list(out.glob("p*.png"))) if out.exists() else 0
    total = int(job.get("page_count") or 0)
    out_pdfs = sorted(p.name for p in (run_dir / "output").glob("*.pdf")) if (run_dir / "output").exists() else []
    bsz = max(1, int(job.get("batch_size") or DEFAULT_BATCH_SIZE))
    eta = estimate_eta(job, done, total, bsz)
    return {
        "done": done,
        "total": total,
        "label": f"{done}/{total}" if total else f"{done}/?",
        "pct": min(100, round(100 * done / total)) if total else 0,
        "output_pdfs": out_pdfs,
        "eta_seconds": eta.get("seconds"),
        "eta_label": eta.get("label"),
        "eta_at": eta.get("at"),
        "sec_per_page": eta.get("sec_per_page"),
    }


def estimate_eta(job: dict, done: int, total: int, batch_size: int) -> dict:
    """Estimate remaining time from measured rate, else heuristic by batch size."""
    if total <= 0:
        return {"seconds": None, "label": None, "at": None, "sec_per_page": None}
    if done >= total:
        return {"seconds": 0, "label": "Done", "at": utc_now(), "sec_per_page": None}

    now = datetime.now(timezone.utc)
    started = (
        parse_iso(job.get("progress_started_at"))
        or parse_iso(job.get("generating_started_at"))
        or parse_iso(job.get("created_at"))
    )
    sec_per_page = None
    if done > 0 and started:
        elapsed = max((now - started).total_seconds(), 1.0)
        sec_per_page = elapsed / done
        remain = int(max(0, (total - done) * sec_per_page))
    else:
        # Within a batch, pages can run with limited parallelism; across batches they are sequential.
        # Effective wall time ≈ sec_per_page * pages * (0.45 + 0.55/min(batch,8))
        parallel_factor = 0.45 + 0.55 / min(max(batch_size, 1), 8)
        sec_per_page = ETA_SEC_PER_PAGE * parallel_factor
        remain = int(max(0, (total - done) * sec_per_page))

    at = (now + timedelta(seconds=remain)).isoformat()
    if remain < 60:
        label = f"~{remain}s"
    elif remain < 3600:
        mins = max(1, round(remain / 60))
        label = f"~{mins} min"
    else:
        hrs = remain / 3600
        label = f"~{hrs:.1f} h"
    return {
        "seconds": remain,
        "label": label,
        "at": at,
        "sec_per_page": round(sec_per_page, 1) if sec_per_page else None,
    }


def require_owner(run_id: str, owner: str) -> tuple[Path, dict]:
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    stored = job.get("owner_token") or ""
    if not stored or not secrets.compare_digest(stored, owner):
        raise HTTPException(403, "This job belongs to another browser session")
    return run_dir, job


def require_agent_or_owner(
    run_id: str,
    owner: str | None,
    agent_key: str | None,
) -> tuple[Path, dict]:
    run_dir = RUNS / run_id
    job = read_job(run_dir)
    stored_agent = job.get("agent_key") or ""
    stored_owner = job.get("owner_token") or ""
    if agent_key and stored_agent and secrets.compare_digest(stored_agent, agent_key):
        return run_dir, job
    if owner and stored_owner and secrets.compare_digest(stored_owner, owner):
        return run_dir, job
    raise HTTPException(403, "Agent key or owner session required")


def with_agent(url: str | None, agent_key: str | None) -> str | None:
    if not url:
        return None
    if not agent_key:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}agent_key={agent_key}"


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
    return len(list(out_dir.glob("p*.png")))


def try_assemble(run_dir: Path, job: dict) -> Path:
    script = SCRIPTS / "assemble_pdf.py"
    if not script.exists():
        raise RuntimeError("assemble_pdf.py missing")
    lang = (job.get("target_lang") or "en").strip().lower()
    pages = run_dir / "pages_out"
    src = run_dir / "pages_src"
    out_dir = run_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{run_dir.name}_{lang}.pdf"
    cmd = [
        sys.executable,
        str(script),
        "--pages-dir",
        str(pages),
        "--out",
        str(out),
        "--target-lang",
        lang,
    ]
    if src.exists() and any(src.glob("p*.png")):
        cmd.extend(["--match-src-dir", str(src)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(proc.stderr or proc.stdout or "assemble_pdf failed")
    return out


def finalize_if_complete(run_dir: Path, job: dict, cp: dict | None = None) -> dict:
    cp = cp or checkpoint_dict(run_dir)
    if not cp.get("complete"):
        return cp
    try:
        out = try_assemble(run_dir, job)
        job["status"] = "assembled"
        job["message"] = f"Localized PDF ready ({cp.get('progress')})."
        job["output_pdf"] = str(out.relative_to(run_dir))
        job["error"] = None
        write_job(run_dir, job)
    except Exception as e:
        job["status"] = "generating"
        job["message"] = f"All pages present; assemble failed: {e}"
        job["error"] = str(e)
        write_job(run_dir, job)
    return cp


def build_webhook_payload(job: dict, event: str = "job.created") -> dict:
    base = PUBLIC_BASE_URL or ""
    run_id = job.get("run_id")
    agent_key = job.get("agent_key") or ""
    cp = {}
    run_dir = RUNS / str(run_id) if run_id else None
    if run_dir and run_dir.exists():
        cp = checkpoint_dict(run_dir)
    missing = cp.get("missing") or []
    batch_size = int(job.get("batch_size") or DEFAULT_BATCH_SIZE)
    batch_pages = missing[:batch_size]
    urls = {
        "job": with_agent(f"{base}/v1/jobs/{run_id}" if base else None, agent_key),
        "input": with_agent(f"{base}/v1/jobs/{run_id}/input" if base else None, agent_key),
        "pages_src_zip": with_agent(
            f"{base}/v1/jobs/{run_id}/pages_src.zip" if base else None, agent_key
        ),
        "pages_upload": with_agent(f"{base}/v1/jobs/{run_id}/pages" if base else None, agent_key),
        "continue": with_agent(f"{base}/v1/jobs/{run_id}/continue" if base else None, agent_key),
        "download": None,  # owner-only; never expose to agent webhook consumers
        "result": with_agent(f"{base}/v1/jobs/{run_id}/result" if base else None, agent_key),
        "assemble": with_agent(f"{base}/v1/jobs/{run_id}/assemble" if base else None, agent_key),
    }
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
        "agent_key": agent_key or None,
        "urls": urls,
        "instruction": (
            "Large-deck batch mode: process ONLY batch_pages (up to batch_size) this run. "
            "Use urls.* which already include agent_key. Download pages_src.zip or input; "
            "GenerateImage each missing page; POST PNGs to urls.pages_upload. "
            "If more pages remain, POST urls.continue. When complete, POST urls.assemble "
            "or urls.result. Do not share agent_key. Skill: ppt-slide-localize."
        ),
    }


def notify_cursor_webhook(job: dict, event: str = "job.created") -> None:
    if not CURSOR_WEBHOOK_URL:
        job["webhook_status"] = None
        job["webhook_error"] = "CURSOR_WEBHOOK_URL not set"
        return
    auth = webhook_authorization_header()
    if not auth:
        job["webhook_status"] = None
        job["webhook_error"] = (
            "CURSOR_WEBHOOK_AUTH not set. In Cursor Automations → webhook → "
            "Generate auth header, then fly secrets set CURSOR_WEBHOOK_AUTH='…'"
        )
        return
    payload = build_webhook_payload(job, event=event)
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": auth,
        "User-Agent": "ppt-slide-localize/1.5",
    }
    req = urllib.request.Request(
        CURSOR_WEBHOOK_URL,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:500]
            job["webhook_status"] = resp.status
            job["webhook_error"] = None if 200 <= resp.status < 300 else body
            job["last_webhook_event"] = event
            if 200 <= resp.status < 300 and job.get("status") in {"exported", "queued"}:
                job["status"] = "generating"
                if not job.get("generating_started_at"):
                    job["generating_started_at"] = utc_now()
                job["message"] = (
                    f"Automation triggered ({event}). Progress "
                    f"{payload.get('progress') or '?'}; batch={payload.get('batch_pages')}"
                )
    except urllib.error.HTTPError as e:
        job["webhook_status"] = e.code
        job["webhook_error"] = e.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.URLError as e:
        job["webhook_status"] = None
        job["webhook_error"] = str(e)
    except Exception as e:
        job["webhook_status"] = None
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
        "session_privacy": True,
        "webhook_url_configured": bool(CURSOR_WEBHOOK_URL),
        "webhook_auth_configured": bool(webhook_authorization_header()),
        "webhook_configured": webhook_ready(),
        "max_pages": MAX_PAGES,
        "max_upload_mb": MAX_UPLOAD_MB,
        "default_batch_size": DEFAULT_BATCH_SIZE,
        "max_batch_size": MAX_BATCH_SIZE,
        "job_ttl_hours": JOB_TTL_HOURS,
        "leave_grace_sec": LEAVE_GRACE_SEC,
    }


@app.post("/v1/session/hello")
def session_hello(
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> dict:
    purge_expired_jobs()
    touch_session_jobs(owner)
    return {"ok": True, "session": True, "time": utc_now()}


@app.post("/v1/session/leave")
def session_leave(
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> dict:
    """Schedule deletion shortly after the tab closes (refresh can cancel via hello)."""
    RUNS.mkdir(parents=True, exist_ok=True)
    when = (datetime.now(timezone.utc) + timedelta(seconds=LEAVE_GRACE_SEC)).isoformat()
    marked = 0
    for d in RUNS.iterdir():
        if not (d.is_dir() and (d / "job.json").exists()):
            continue
        try:
            job = read_job(d)
        except Exception:
            continue
        if job.get("owner_token") != owner:
            continue
        job["soft_delete_at"] = when
        write_job(d, job)
        marked += 1
    return {"ok": True, "marked": marked, "soft_delete_at": when, "grace_sec": LEAVE_GRACE_SEC}


@app.delete("/v1/session")
def session_delete(
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> dict:
    """Immediately delete all jobs for this browser session."""
    RUNS.mkdir(parents=True, exist_ok=True)
    removed = []
    for d in list(RUNS.iterdir()):
        if not (d.is_dir() and (d / "job.json").exists()):
            continue
        try:
            job = read_job(d)
        except Exception:
            continue
        if job.get("owner_token") != owner:
            continue
        rid = job.get("run_id") or d.name
        delete_run_dir(d)
        removed.append(rid)
    return {"ok": True, "deleted": removed}


@app.post("/v1/jobs")
async def create_job(
    file: UploadFile = File(...),
    target_lang: str = Form("en"),
    remove_watermarks: str = Form("true"),
    auto_export: str = Form("true"),
    batch_size: str = Form(str(DEFAULT_BATCH_SIZE)),
    session_token: str = Form(...),
    _: None = Depends(require_access),
) -> JSONResponse:
    purge_expired_jobs()
    owner = (session_token or "").strip()
    if not SESSION_RE.match(owner):
        raise HTTPException(400, "session_token required (private tab session)")
    touch_session_jobs(owner)

    lang = (target_lang or "en").strip().lower()
    if lang not in ALLOWED_LANGS:
        raise HTTPException(400, f"target_lang must be one of {sorted(ALLOWED_LANGS)}")

    try:
        bsz = int(str(batch_size).strip() or DEFAULT_BATCH_SIZE)
    except ValueError:
        bsz = DEFAULT_BATCH_SIZE
    bsz = max(1, min(bsz, MAX_BATCH_SIZE))

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
    agent_key = secrets.token_urlsafe(24)

    job = {
        "run_id": run_id,
        "owner_token": owner,
        "agent_key": agent_key,
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
                raise HTTPException(413, detail=public_job(job))
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
            raise HTTPException(500, detail=public_job(job))

    notify_cursor_webhook(job, event="job.created")
    write_job(run_dir, job)

    body = {
        **public_job(job),
        "checkpoint": checkpoint_dict(run_dir),
        "progress": job_progress(run_dir, job),
    }
    body["output_pdfs"] = body["progress"].pop("output_pdfs")
    return JSONResponse(body, status_code=201)


@app.get("/v1/jobs")
def list_jobs(
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> dict:
    purge_expired_jobs()
    touch_session_jobs(owner)
    RUNS.mkdir(parents=True, exist_ok=True)
    items = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        if not (d.is_dir() and (d / "job.json").exists()):
            continue
        try:
            job = read_job(d)
        except Exception:
            continue
        if job.get("owner_token") != owner:
            continue
        prog = job_progress(d, job)
        out_pdfs = prog.pop("output_pdfs")
        if prog["total"] and prog["done"] >= prog["total"] and out_pdfs and job.get("status") not in {
            "assembled",
            "failed",
        }:
            job["status"] = "assembled"
            job["message"] = "Localized PDF ready."
            write_job(d, job)
        pub = public_job(job)
        pub["progress"] = prog
        pub["output_pdfs"] = out_pdfs
        items.append(pub)
    return {"jobs": items[:50], "time": utc_now(), "private": True}


@app.get("/v1/jobs/{run_id}")
def get_job(
    run_id: str,
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
    if owner and job.get("owner_token") == owner:
        touch_session_jobs(owner)
    cp = checkpoint_dict(run_dir)
    out_pdfs = list((run_dir / "output").glob("*.pdf")) if (run_dir / "output").exists() else []
    if cp.get("complete") and not out_pdfs and job.get("status") not in {"failed"}:
        finalize_if_complete(run_dir, job, cp)
        out_pdfs = list((run_dir / "output").glob("*.pdf")) if (run_dir / "output").exists() else []
    elif cp.get("complete") and out_pdfs and job.get("status") not in {"assembled", "failed"}:
        job["status"] = "assembled"
        job["message"] = "Localized PDF ready."
        job["output_pdf"] = str(out_pdfs[0].relative_to(run_dir))
        write_job(run_dir, job)
    pub = public_job(job)
    pub["checkpoint"] = cp
    pub["output_pdfs"] = [p.name for p in out_pdfs]
    pub["progress"] = job_progress(run_dir, job)
    pub["progress"].pop("output_pdfs", None)
    return pub


@app.delete("/v1/jobs/{run_id}")
def delete_job(
    run_id: str,
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> dict:
    run_dir, _job = require_owner(run_id, owner)
    delete_run_dir(run_dir)
    return {"ok": True, "deleted": run_id}


@app.get("/v1/jobs/{run_id}/download")
def download_job(
    run_id: str,
    owner: str = Depends(require_session),
    _: None = Depends(require_access),
) -> FileResponse:
    """Owner-only download — not available via agent_key."""
    run_dir, job = require_owner(run_id, owner)
    out_dir = run_dir / "output"
    pdfs = sorted(out_dir.glob("*.pdf")) if out_dir.exists() else []
    if not pdfs:
        raise HTTPException(404, "PDF not ready yet.")
    lang = job.get("target_lang") or ""
    preferred = [p for p in pdfs if f"_{lang}." in p.name or p.name.endswith(f"_{lang}.pdf")]
    path = preferred[0] if preferred else pdfs[0]
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/pdf",
        content_disposition_type="attachment",
    )


@app.get("/v1/jobs/{run_id}/input")
def download_input(
    run_id: str,
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> FileResponse:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
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
def download_pages_src_zip(
    run_id: str,
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> FileResponse:
    import tempfile
    import zipfile

    run_dir, _job = require_agent_or_owner(run_id, owner, agent_key)
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
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
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
        m = re.match(r"^p(\d+)\.png$", name, re.I)
        dest = out / (f"p{int(m.group(1)):02d}.png" if m else name.lower())
        dest.write_bytes(data)
        saved.append(dest.name)
    job["status"] = "generating"
    if not job.get("generating_started_at"):
        job["generating_started_at"] = utc_now()
    cp = checkpoint_dict(run_dir)
    if int(cp.get("out_count") or 0) > 0 and not job.get("progress_started_at"):
        job["progress_started_at"] = utc_now()
    job["message"] = f"Received {len(saved)} page(s). Progress {cp.get('progress')}."
    write_job(run_dir, job)
    continued = False
    if saved and webhook_ready() and not cp.get("complete"):
        notify_cursor_webhook(job, event="job.batch")
        write_job(run_dir, job)
        continued = True
        job["message"] = (
            f"Received {len(saved)} page(s). Progress {cp.get('progress')}. "
            f"Next batch webhook fired (next p{cp.get('next_page'):02d})."
            if cp.get("next_page")
            else f"Received {len(saved)} page(s). Progress {cp.get('progress')}."
        )
        write_job(run_dir, job)
    elif saved and cp.get("complete"):
        finalize_if_complete(run_dir, job, cp)
        continued = job.get("status") == "assembled"
    return {
        "saved": saved,
        "checkpoint": cp,
        "continued": continued,
        **{
            k: public_job(job).get(k)
            for k in ("run_id", "status", "message", "webhook_status", "webhook_error")
        },
    }


@app.post("/v1/jobs/{run_id}/continue")
def continue_job(
    run_id: str,
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
    cp = checkpoint_dict(run_dir)
    if cp.get("complete"):
        finalize_if_complete(run_dir, job, cp)
        return {**public_job(job), "checkpoint": cp, "continued": False, "reason": "complete"}
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
    return {**public_job(job), "checkpoint": cp, "continued": True}


@app.post("/v1/jobs/{run_id}/assemble")
def assemble_job(
    run_id: str,
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
    cp = finalize_if_complete(run_dir, job)
    if job.get("status") != "assembled":
        raise HTTPException(409, detail={**public_job(job), "checkpoint": cp})
    return {**public_job(job), "checkpoint": cp}


@app.post("/v1/jobs/{run_id}/result")
async def upload_result(
    run_id: str,
    file: UploadFile = File(...),
    status: str = Form("assembled"),
    message: str = Form(""),
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
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
    return {**public_job(job), "checkpoint": checkpoint_dict(run_dir)}


@app.patch("/v1/jobs/{run_id}/status")
async def patch_status(
    run_id: str,
    status: str = Form(...),
    message: str = Form(""),
    owner: str | None = Depends(optional_session),
    agent_key: str | None = Depends(optional_agent_key),
    _: None = Depends(require_access),
) -> dict:
    _run_dir, job = require_agent_or_owner(run_id, owner, agent_key)
    st = status.strip().lower()
    if st not in {"queued", "exported", "generating", "assembled", "failed"}:
        raise HTTPException(400, "invalid status")
    job["status"] = st
    if message:
        job["message"] = message
    write_job(_run_dir, job)
    return public_job(job)


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8787"))
    uvicorn.run("server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
