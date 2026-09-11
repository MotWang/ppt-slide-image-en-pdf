#!/usr/bin/env python3
"""Copy generated page images into a run's pages_out/ (resume-friendly).

Looks for files named pXX.png or <prefix>pXX.png / <prefix>_pXX.png in --from dirs
and writes canonical pXX.png into --run-dir/<pages-out> without overwriting unless --force.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

NUM_RE = re.compile(r"(?:^|[_-])p(\d+)\.(png|jpg|jpeg|webp)$", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument(
        "--pages-out",
        default="pages_out",
        help="Destination folder name under run-dir (default pages_out; use pages_en for legacy)",
    )
    ap.add_argument(
        "--from",
        dest="sources",
        action="append",
        default=[],
        help="Directory containing generated images (repeatable)",
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    out_dir = run_dir / args.pages_out
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = [Path(s).expanduser().resolve() for s in args.sources]
    if not sources:
        raise SystemExit("Pass at least one --from directory")

    copied = 0
    skipped = 0
    for src_dir in sources:
        if not src_dir.is_dir():
            print(f"skip missing dir: {src_dir}")
            continue
        for p in sorted(src_dir.iterdir()):
            if not p.is_file():
                continue
            m = NUM_RE.search(p.name)
            if not m:
                continue
            n = int(m.group(1))
            dest = out_dir / f"p{n:02d}.png"
            if dest.exists() and not args.force:
                skipped += 1
                continue
            shutil.copy2(p, dest)
            print(f"{p.name} → {dest.name}")
            copied += 1
    print(f"copied={copied} skipped_existing={skipped} → {out_dir}")


if __name__ == "__main__":
    main()
