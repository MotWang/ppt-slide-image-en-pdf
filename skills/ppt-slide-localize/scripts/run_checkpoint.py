#!/usr/bin/env python3
"""Report run progress and list missing localized pages (for resume)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PAGE_RE = re.compile(r"^p(\d+)\.(png|jpg|jpeg|webp)$", re.I)


def page_nums(d: Path) -> list[int]:
    if not d.is_dir():
        return []
    nums: list[int] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        m = PAGE_RE.match(p.name)
        if m:
            nums.append(int(m.group(1)))
    return sorted(set(nums))


def resolve_out_dir(run_dir: Path, pages_out: str) -> Path:
    """Prefer explicit --pages-out; else job.json; else pages_out then pages_en."""
    if pages_out:
        return run_dir / pages_out
    job_path = run_dir / "job.json"
    if job_path.exists():
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            name = job.get("pages_out_dir") or "pages_out"
            cand = run_dir / name
            if cand.is_dir() or not (run_dir / "pages_en").is_dir():
                return cand
        except Exception:
            pass
    if (run_dir / "pages_out").is_dir():
        return run_dir / "pages_out"
    return run_dir / "pages_en"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="runs/<run_id> directory")
    ap.add_argument(
        "--pages-out",
        default="",
        help="Output pages folder name under run-dir (default: pages_out or pages_en)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    src = run_dir / "pages_src"
    out = resolve_out_dir(run_dir, args.pages_out)
    src_n = page_nums(src)
    out_n = page_nums(out)
    missing = [n for n in src_n if n not in out_n]
    extra = [n for n in out_n if n not in src_n]
    target_lang = None
    job_path = run_dir / "job.json"
    if job_path.exists():
        try:
            target_lang = json.loads(job_path.read_text(encoding="utf-8")).get("target_lang")
        except Exception:
            pass
    status = {
        "run_dir": str(run_dir),
        "pages_out_dir": str(out),
        "target_lang": target_lang,
        "src_count": len(src_n),
        "out_count": len(out_n),
        "en_count": len(out_n),  # backward-compatible key
        "complete": bool(src_n) and not missing and not extra,
        "missing": missing,
        "extra": extra,
        "next_page": missing[0] if missing else None,
        "progress": f"{len(out_n)}/{len(src_n)}" if src_n else "0/0",
    }
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"run: {run_dir}")
        print(f"pages_out: {out}")
        if target_lang:
            print(f"target_lang: {target_lang}")
        print(f"progress: {status['progress']}  complete={status['complete']}")
        if missing:
            print("missing:", ", ".join(f"p{n:02d}" for n in missing))
            print("resume_from:", f"p{missing[0]:02d}")
        if extra:
            print("extra:", ", ".join(f"p{n:02d}" for n in extra))
    sys.exit(0 if status["complete"] else 2)


if __name__ == "__main__":
    main()
