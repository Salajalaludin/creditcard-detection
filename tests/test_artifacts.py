"""Test keamanan dan integritas deployment artifact bundle."""

import zipfile
from pathlib import Path

import pytest

from fraud_detection.artifacts import download_artifact_bundle, extract_artifact_archive, sha256_file


def make_zip(path: Path, members: dict[str, bytes]) -> None:
    """Buat ZIP kecil untuk skenario unit test."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_extract_archive_validates_required_members(tmp_path: Path) -> None:
    """Bundle valid harus diekstrak dan memiliki seluruh kontrak file."""
    archive = tmp_path / "bundle.zip"
    make_zip(archive, {"models/model.joblib": b"model", "reports/metric.json": b"{}"})
    destination = tmp_path / "runtime"
    extract_artifact_archive(archive, destination, ("models/model.joblib", "reports/metric.json"))
    assert (destination / "models" / "model.joblib").read_bytes() == b"model"


def test_extract_archive_rejects_zip_slip(tmp_path: Path) -> None:
    """Path traversal dalam ZIP tidak boleh menulis di luar artifact root."""
    archive = tmp_path / "unsafe.zip"
    make_zip(archive, {"../outside.txt": b"unsafe"})
    with pytest.raises(ValueError, match="tidak aman"):
        extract_artifact_archive(archive, tmp_path / "runtime")
    assert not (tmp_path / "outside.txt").exists()


def test_extract_archive_rejects_excessive_expanded_size(tmp_path: Path) -> None:
    """Batas hasil ekstraksi melindungi deployment dari ZIP bomb."""
    archive = tmp_path / "large.zip"
    make_zip(archive, {"large.bin": b"12345"})
    with pytest.raises(ValueError, match="ekstraksi"):
        extract_artifact_archive(archive, tmp_path / "runtime", max_extracted_bytes=4)


def test_download_bundle_checks_sha256(tmp_path: Path) -> None:
    """Downloader harus menolak bundle jika checksum release tidak cocok."""
    archive = tmp_path / "bundle.zip"
    make_zip(archive, {"models/model.joblib": b"model"})
    with pytest.raises(ValueError, match="SHA-256"):
        download_artifact_bundle(
            archive.as_uri(),
            tmp_path / "runtime",
            expected_sha256="0" * 64,
            required_members=("models/model.joblib",),
        )
    assert sha256_file(archive) != "0" * 64


def test_download_bundle_accepts_matching_checksum(tmp_path: Path) -> None:
    """File URL dengan checksum benar memudahkan integration test tanpa network."""
    archive = tmp_path / "bundle.zip"
    make_zip(archive, {"models/model.joblib": b"model"})
    checksum = download_artifact_bundle(
        archive.as_uri(),
        tmp_path / "runtime",
        expected_sha256=sha256_file(archive),
        required_members=("models/model.joblib",),
    )
    assert checksum == sha256_file(archive)
    assert (tmp_path / "runtime" / "models" / "model.joblib").exists()
