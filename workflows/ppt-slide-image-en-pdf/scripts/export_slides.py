#!/usr/bin/env python3
"""Best-effort slide export to PNG pages.

Supports:
  - PDF via pymupdf / pypdfium2 / pdftoppm
  - PPTX via LibreOffice soffice → PDF → PNG
  - ZIP of page images (png/jpg/webp) → normalize to pXX.png
  - Folder of images → normalize to pXX.png
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def run(cmd: list[str], env: dict | None = None, timeout: int | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, env=env, timeout=timeout)


def export_pdf_to_png(pdf: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

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
            "PPTX needs LibreOffice (`soffice`). Install libreoffice-impress, "
            "or export the deck to PDF and upload the PDF."
        )
    env = os.environ.copy()
    env.setdefault("HOME", "/tmp")
    # Profile dir avoids permission issues in containers
    profile = work / "lo_profile"
    profile.mkdir(parents=True, exist_ok=True)
    run(
        [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(pptx),
        ],
        env=env,
        timeout=600,
    )
    pdf = work / (pptx.stem + ".pdf")
    if not pdf.exists():
        # Some LO builds rewrite the stem; pick any new PDF in work
        pdfs = sorted(work.glob("*.pdf"))
        if not pdfs:
            raise SystemExit(f"LibreOffice did not produce a PDF from {pptx.name}")
        pdf = pdfs[0]
    return pdf


def collect_images(root: Path) -> list[Path]:
    imgs: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            # skip macOS junk / hidden
            if "/__MACOSX/" in str(p) or p.name.startswith("."):
                continue
            imgs.append(p)
    return imgs


def normalize_image_list(files: list[Path], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not files:
        raise SystemExit("No page images found (png/jpg/webp)")
    named = [p for p in files if re.match(r"^p\d+\.", p.name, re.I)]
    if named:
        use = sorted(named, key=lambda p: int(re.search(r"\d+", p.name).group()))
    else:
        use = sorted(files, key=lambda p: p.name.lower())
    from PIL import Image

    for i, p in enumerate(use, 1):
        dest = out_dir / f"p{i:02d}.png"
        Image.open(p).convert("RGB").save(dest)
    return len(use)


def normalize_image_folder(src: Path, out_dir: Path) -> int:
    return normalize_image_list(collect_images(src), out_dir)


def export_zip_images(zip_path: Path, out_dir: Path) -> int:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        return normalize_image_folder(work, out_dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="pptx / pdf / zip of images / image folder")
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
    if suffix == ".zip":
        n = export_zip_images(src, out_dir)
        print(f"Exported {n} pages from ZIP → {out_dir}")
        return

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if suffix == ".pptx":
            pdf = pptx_to_pdf(src, work)
        elif suffix == ".pdf":
            pdf = src
        else:
            raise SystemExit(f"Unsupported input: {src} (use .pdf, .pptx, or .zip of page images)")
        n = export_pdf_to_png(pdf, out_dir)
        print(f"Exported {n} pages → {out_dir}")


if __name__ == "__main__":
    main()
