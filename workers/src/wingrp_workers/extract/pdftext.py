"""PDF -> layout-preserving text via poppler's `pdftotext -layout`.

Traffic-system PDFs (WideOrbit etc.) are column layouts; `-layout` keeps the
columns aligned so the format parsers can work line by line. Pages are
separated by form feeds (\\f), which parsers use for page references.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PdfTextUnavailable(RuntimeError):
    pass


def pdf_to_text(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise PdfTextUnavailable("pdftotext not found; install poppler (brew install poppler / apt install poppler-utils)")
    out = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")
