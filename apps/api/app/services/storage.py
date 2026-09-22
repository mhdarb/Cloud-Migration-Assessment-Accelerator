from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import get_settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB
ALLOWED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".csv",
    ".json",
    ".txt",
    ".zip",
    ".md",
}
SKIP_ZIP_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "bin",
    "obj",
    "target",
    "__pycache__",
    ".venv",
    "venv",
}
MAX_ZIP_FILES = 2000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024  # 200MB uncompressed


async def save_upload(assessment_id: str, upload: UploadFile) -> tuple[str, str]:
    settings = get_settings()
    filename = upload.filename or "file"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type: {suffix or '(none)'}")

    dest_dir = Path(settings.storage_dir) / assessment_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{suffix}"
    dest = dest_dir / stored_name

    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "File exceeds 50MB limit")
            out.write(chunk)
    return str(dest), filename


def save_bytes(assessment_id: str, filename: str, data: bytes) -> str:
    settings = get_settings()
    dest_dir = Path(settings.storage_dir) / assessment_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(data)
    return str(dest)


def safe_extract_zip(zip_path: str, assessment_id: str, document_id: str) -> Path:
    """Extract ZIP under STORAGE_DIR/{assessment_id}/code/{document_id}/ with zip-slip guards."""
    settings = get_settings()
    dest_root = Path(settings.storage_dir) / assessment_id / "code" / document_id
    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    total_uncompressed = 0
    file_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            parts = Path(name).parts
            if any(p in SKIP_ZIP_DIRS for p in parts):
                continue
            if name.startswith("/") or ".." in parts:
                continue
            file_count += 1
            if file_count > MAX_ZIP_FILES:
                raise ValueError("ZIP contains too many files")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED:
                raise ValueError("ZIP uncompressed size exceeds limit")
            target = (dest_root / name).resolve()
            if not str(target).startswith(str(dest_root.resolve())):
                raise ValueError("ZIP path traversal blocked")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    return dest_root
