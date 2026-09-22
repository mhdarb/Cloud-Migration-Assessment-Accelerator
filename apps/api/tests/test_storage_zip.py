import zipfile
from pathlib import Path

from app.services.storage import safe_extract_zip


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_safe_extract_skips_zip_slip(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    get_settings().ensure_dirs()

    zpath = tmp_path / "evil.zip"
    _write_zip(
        zpath,
        {
            "../../evil.txt": b"nope",
            "ok/package.json": b'{"name":"x"}',
        },
    )
    root = safe_extract_zip(str(zpath), "a1", "d1")
    assert (root / "ok" / "package.json").exists()
    # traversal entry must not escape dest root
    assert not (tmp_path / "evil.txt").exists()
    assert list(root.rglob("evil.txt")) == []


def test_safe_extract_ok_package(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    get_settings().ensure_dirs()

    zpath = tmp_path / "app.zip"
    _write_zip(
        zpath,
        {"package.json": b'{"name":"orders-api","dependencies":{"pg":"8"}}'},
    )
    root = safe_extract_zip(str(zpath), "a1", "d1")
    assert (root / "package.json").exists()
    assert b"orders-api" in (root / "package.json").read_bytes()
