#!/usr/bin/env python3
"""Assemble zero-padded page PNGs (p01.png…) into a single multi-page PDF."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


PAGE_RE = re.compile(r"^p(\d+)\.(png|jpg|jpeg|webp)$", re.I)


def list_pages(pages_dir: Path) -> list[Path]:
    pages: list[tuple[int, Path]] = []
    for p in pages_dir.iterdir():
        if not p.is_file():
            continue
        m = PAGE_RE.match(p.name)
        if not m:
            continue
        pages.append((int(m.group(1)), p))
    pages.sort(key=lambda x: x[0])
    if not pages:
        raise SystemExit(f"No pXX.png pages found in {pages_dir}")
    expected = list(range(1, pages[-1][0] + 1))
    got = [n for n, _ in pages]
    missing = [n for n in expected if n not in got]
    if missing:
        raise SystemExit(f"Missing page numbers: {missing}")
    return [p for _, p in pages]


def to_rgb(im: Image.Image) -> Image.Image:
    if im.mode == "RGB":
        return im
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default="")
    ap.add_argument(
        "--match-src-dir",
        default="",
        help="If set, resize each EN page to match the corresponding pages_src/pXX.png size before PDF assemble.",
    )
    ap.add_argument("--pdf-dpi", type=float, default=150.0)
    ap.add_argument(
        "--target-lang",
        default="",
        help="Optional language code written into the manifest (en/zh/ko/ja).",
    )
    args = ap.parse_args()

    pages_dir = Path(args.pages_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    match_src = Path(args.match_src_dir).expanduser().resolve() if args.match_src_dir else None

    paths = list_pages(pages_dir)
    images: list[Image.Image] = []
    for p in paths:
        im = to_rgb(Image.open(p))
        if match_src is not None:
            m = PAGE_RE.match(p.name)
            src_path = match_src / f"p{int(m.group(1)):02d}.png"
            if src_path.exists():
                sw, sh = Image.open(src_path).size
                if im.size != (sw, sh):
                    im = im.resize((sw, sh), Image.Resampling.LANCZOS)
        images.append(im)
    first, rest = images[0], images[1:]
    # Higher JPEG quality inside PDF reduces mushy text from Pillow's default encode.
    first.save(
        out,
        "PDF",
        save_all=True,
        append_images=rest,
        resolution=float(args.pdf_dpi),
        quality=95,
    )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(paths),
        "pages": [p.name for p in paths],
        "pdf": str(out),
        "pages_dir": str(pages_dir),
        "target_lang": args.target_lang or None,
    }
    man_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else out.with_name(out.stem + "_manifest.json")
    )
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({len(paths)} pages)")
    print(f"Wrote {man_path}")


if __name__ == "__main__":
    main()
