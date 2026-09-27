"""Convert legacy binary Office files (Word/Excel 97-2003) to their modern equivalents.

Enterprise discovery packs still contain `.doc` and `.xls` files. They are OLE2 compound
files that python-docx / openpyxl cannot read, and pure-Python readers for them either
lose all table structure (.doc) or aren't maintained. LibreOffice, run headless, is the
standard server-side converter: it produces a real .docx / .xlsx, so the existing parsers
(document-order tables, merged cells, header detection) apply unchanged.

LibreOffice is an optional dependency: when it isn't installed, conversion raises a clear
error saying how to enable it, and the document surfaces as a visible gap.

Security: conversion runs in a throwaway LibreOffice profile with a hard timeout. Headless
conversion doesn't execute document macros (LibreOffice's default macro security).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
_ENCRYPTED_OFFICE_MARKER = "EncryptedPackage".encode("utf-16-le")


class ConversionUnavailable(ValueError):
    """No converter is installed on this host."""


def libreoffice_binary() -> str | None:
    configured = (get_settings().libreoffice_path or "").strip()
    if configured:
        return configured if Path(configured).exists() else None
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return _MAC_SOFFICE if Path(_MAC_SOFFICE).exists() else None


def is_encrypted_office_file(path: str) -> bool:
    """A password-protected .docx/.xlsx is stored as an OLE2 container holding an
    `EncryptedPackage` stream — the same outer format as a legacy file, so it has to be
    told apart before attempting a (doomed) conversion."""
    try:
        return _ENCRYPTED_OFFICE_MARKER in Path(path).read_bytes()
    except OSError:
        return False


def convert_legacy_office(path: str, target_ext: str, *, kind: str) -> str:
    """Convert `path` to `target_ext` ("docx" | "xlsx") and return the converted file's
    path. The result is cached beside the original, so pipeline re-runs don't reconvert."""
    source = Path(path)
    converted = source.with_name(f"{source.name}.converted.{target_ext}")
    if converted.exists() and converted.stat().st_mtime >= source.stat().st_mtime:
        return str(converted)

    binary = libreoffice_binary()
    if binary is None:
        raise ConversionUnavailable(
            f"legacy binary {kind} format needs LibreOffice to convert it: install LibreOffice on the "
            "API host (or set LIBREOFFICE_PATH), or re-save the file as a modern Office file or PDF "
            "and re-upload"
        )
    timeout = get_settings().office_conversion_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="cmaa-convert-") as tmp:
        tmp_path = Path(tmp)
        # A private profile per conversion: concurrent runs sharing the default profile
        # block on its lock, and nothing a document does can persist into the next one.
        profile = (tmp_path / "profile").as_uri()
        cmd = [
            binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            target_ext,
            "--outdir",
            str(tmp_path),
            str(source),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"could not convert legacy binary {kind}: LibreOffice timed out after {timeout:.0f}s"
            ) from exc
        produced = tmp_path / f"{source.stem}.{target_ext}"
        if proc.returncode != 0 or not produced.exists():
            logger.warning("LibreOffice conversion failed for %s: %s", source.name, proc.stderr[-500:])
            raise ValueError(
                f"could not convert legacy binary {kind}: the file may be damaged — re-save it as a "
                "modern Office file or PDF and re-upload"
            )
        shutil.move(str(produced), converted)
    return str(converted)
