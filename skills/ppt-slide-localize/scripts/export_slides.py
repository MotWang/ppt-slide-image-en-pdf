#!/usr/bin/env python3
"""Best-effort slide export to PNG pages.

Supports:
  - PDF via pypdfium2 or pdf2image (if installed)
  - Folder of images already named / rename to pXX.png
  - PPTX: tries LibreOffice `soffice` headless → PDF → PNG

If tools are missing, exits with clear install hints.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def export_pdf_to_png(pdf: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prefer PyMuPDF (common on this machine); then pypdfium2; then pdftoppm.
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(pdf)
        mat = pymupdf.Matrix(2.0, 2.0)
        for i in range(doc.page_count):
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            pix.save(out_dir / f"p{i+1:02d}.png")
        n = doc.page_count
        doc.close()
        return n
    except Exception as e:
        print(f"pymupdf unavailable/failed: {e}")

    try:
        import pypdfium2 as pdfium  # type: ignore

        doc = pdfium.PdfDocument(str(pdf))
        for i in range(len(doc)):
            page = doc[i]
            # ~150–180 dpi-equivalent for 16:9 slides
            bitmap = page.render(scale=2)
            pil = bitmap.to_pil()
            pil.save(out_dir / f"p{i+1:02d}.png")
        return len(doc)
    except Exception as e:
        print(f"pypdfium2 unavailable/failed: {e}")

    if shutil.which("pdftoppm"):
        run(["pdftoppm", "-png", "-r", "150", str(pdf), str(out_dir / "slide")])
        slides = sorted(out_dir.glob("slide-*.png"))
        for i, p in enumerate(slides, 1):
            dest = out_dir / f"p{i:02d}.png"
            p.rename(dest)
        return len(slides)

    raise SystemExit(
        "Need pymupdf (`pip install pymupdf`), pypdfium2, or poppler `pdftoppm` to export PDF pages."
    )


def pptx_to_pdf(pptx: Path, work: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SystemExit(
            "PPTX needs LibreOffice on the server (soffice), which is not installed on the hosted site. "
            "Please export the deck to PDF in PowerPoint/Keynote and upload the PDF instead."
        )
    run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(pptx),
        ]
    )
    pdf = work / (pptx.stem + ".pdf")
    if not pdf.exists():
        raise SystemExit(f"LibreOffice did not produce {pdf}")
    return pdf


def normalize_image_folder(src: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [
            p
            for p in src.iterdir()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and p.is_file()
        ]
    )
    if not files:
        raise SystemExit(f"No images in {src}")
    # Prefer already pXX named
    named = [p for p in files if re.match(r"^p\d+\.", p.name, re.I)]
    use = sorted(named, key=lambda p: int(re.search(r"\d+", p.name).group())) if named else files
    for i, p in enumerate(use, 1):
        dest = out_dir / f"p{i:02d}.png"
        if p.resolve() == dest.resolve():
            continue
        from PIL import Image

        Image.open(p).convert("RGB").save(dest)
    return len(use)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="pptx / pdf / image folder")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    src = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        n = normalize_image_folder(src, out_dir)
        print(f"Normalized {n} images → {out_dir}")
        return

    suffix = src.suffix.lower()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if suffix == ".pptx":
            pdf = pptx_to_pdf(src, work)
        elif suffix == ".pdf":
            pdf = src
        else:
            raise SystemExit(f"Unsupported input: {src}")
        n = export_pdf_to_png(pdf, out_dir)
        print(f"Exported {n} pages → {out_dir}")


if __name__ == "__main__":
    main()
