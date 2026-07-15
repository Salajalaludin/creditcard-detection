"""Download, verifikasi, dan ekstraksi deployment artifacts secara aman."""

from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

# Batas mencegah salah konfigurasi URL menghabiskan disk/memory deployment.
DEFAULT_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024


def sha256_file(path: Path) -> str:
    """Hitung SHA-256 file menggunakan pembacaan bertahap."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_path(destination: Path, member_name: str) -> Path:
    """Kembalikan target ekstraksi dan tolak path absolut atau traversal."""
    # ZIP selalu memakai separator POSIX; PurePosixPath membuat validasi konsisten.
    member = PurePosixPath(member_name)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"Path tidak aman di artifact bundle: {member_name}")
    target = (destination / Path(*member.parts)).resolve()
    if not target.is_relative_to(destination.resolve()):
        raise ValueError(f"Path keluar dari artifact root: {member_name}")
    return target


def extract_artifact_archive(
    archive_path: Path,
    destination: Path,
    required_members: tuple[str, ...] = (),
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
) -> None:
    """Ekstrak ZIP melalui staging directory lalu validasi file wajib."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Staging mencegah aplikasi membaca bundle yang baru terisi sebagian.
    with tempfile.TemporaryDirectory(prefix="fraud-artifacts-", dir=destination.parent) as temp:
        staging = Path(temp).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            # Tolak ZIP bomb dan symbolic link sebelum satu file pun diekstrak.
            total_size = sum(info.file_size for info in archive.infolist())
            if total_size > max_extracted_bytes:
                raise ValueError("Ukuran hasil ekstraksi artifact bundle melebihi batas")
            for info in archive.infolist():
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    raise ValueError(f"Symbolic link tidak diizinkan dalam bundle: {info.filename}")
                target = _safe_member_path(staging, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

        # Fail fast jika release tidak memiliki kontrak artifact dashboard.
        missing = [name for name in required_members if not (staging / name).is_file()]
        if missing:
            raise ValueError("Artifact bundle tidak lengkap: " + ", ".join(missing))
        shutil.copytree(staging, destination, dirs_exist_ok=True)


def download_artifact_bundle(
    url: str,
    destination: Path,
    expected_sha256: str | None = None,
    required_members: tuple[str, ...] = (),
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> str:
    """Unduh ZIP, verifikasi checksum opsional, lalu ekstrak ke destination."""
    if not url.startswith(("https://", "http://", "file://")):
        raise ValueError("ARTIFACT_URL harus memakai http://, https://, atau file://")

    # File sementara otomatis dibersihkan baik proses berhasil maupun gagal.
    with tempfile.TemporaryDirectory(prefix="fraud-artifact-download-") as temp:
        archive_path = Path(temp) / "artifacts.zip"
        request = urllib.request.Request(url, headers={"User-Agent": "fraud-risk-dashboard/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as output:
            declared_size = int(response.headers.get("Content-Length", "0"))
            if declared_size > max_download_bytes:
                raise ValueError("Artifact bundle melebihi batas download")
            downloaded = 0
            while block := response.read(1024 * 1024):
                downloaded += len(block)
                if downloaded > max_download_bytes:
                    raise ValueError("Artifact bundle melebihi batas download")
                output.write(block)

        checksum = sha256_file(archive_path)
        if expected_sha256 and checksum.lower() != expected_sha256.strip().lower():
            raise ValueError("SHA-256 artifact bundle tidak cocok")
        extract_artifact_archive(archive_path, destination, required_members)
        return checksum
