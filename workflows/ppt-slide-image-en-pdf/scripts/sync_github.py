#!/usr/bin/env python3
"""Copy a completed run into runs/<run_id>/ and optionally git commit + push."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="workflow root containing pages_en/output")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--repo-root", default="", help="git repo root; default: cwd walk-up")
    ap.add_argument("--repo", default="", help="owner/repo for gh; optional if remote exists")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="")
    ap.add_argument("--push", action="store_true", help="git push after commit")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = (
        Path(args.repo_root).expanduser().resolve()
        if args.repo_root
        else run_dir
    )
    # walk up for .git
    cur = repo_root
    while cur != cur.parent and not (cur / ".git").exists():
        cur = cur.parent
    if not (cur / ".git").exists():
        raise SystemExit(f"No git repo found from {repo_root}")
    repo_root = cur

    dest = run_dir / "runs" / args.run_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for name in ("pages_out", "pages_en", "output", "pages_src", "input"):
        src = run_dir / name
        if src.exists():
            target = dest / name
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)

    job_src = run_dir / "job.json"
    if job_src.exists():
        shutil.copy2(job_src, dest / "job.json")

    readme = dest / "README.md"
    readme.write_text(
        f"# Run `{args.run_id}`\n\n"
        f"- Localized pages: `pages_out/` (or legacy `pages_en/`)\n"
        f"- PDF: `output/`\n"
        f"- Source pages: `pages_src/` (if present)\n"
        f"- Job: `job.json` (if present)\n",
        encoding="utf-8",
    )
    print(f"Prepared {dest}")

    if args.no_commit:
        return

    rel = dest.relative_to(repo_root)
    run(["git", "add", str(rel)], cwd=repo_root)
    msg = args.message or f"Add English slide deck run {args.run_id}"
    commit = run(["git", "commit", "-m", msg], cwd=repo_root, check=False)
    if commit.returncode != 0:
        print("Nothing to commit or commit failed; check git status.")
        return

    remotes = subprocess.check_output(["git", "remote"], cwd=str(repo_root), text=True).strip()
    if not remotes:
        print("No git remote configured.")
        if args.repo:
            print(f"Suggested:\n  gh repo create {args.repo} --private --source . --remote origin --push")
            print(f"  git remote add origin git@github.com:{args.repo}.git")
            print(f"  git push -u origin {args.branch}")
        else:
            print("Add a GitHub remote, then: git push -u origin", args.branch)
        return

    if args.push:
        run(["git", "push", "-u", "origin", args.branch], cwd=repo_root)
        print("Pushed.")
    else:
        print(f"Committed. Push with: git push -u origin {args.branch}")


if __name__ == "__main__":
    main()
