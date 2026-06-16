from __future__ import annotations

from pathlib import Path

from ftp_client.transfer import compute_download_offset, compute_upload_offset


def test_download_offset_without_local_file(tmp_path: Path) -> None:
    assert compute_download_offset(tmp_path / "missing.bin", 100, resume=True) == 0


def test_download_offset_uses_local_partial_size(tmp_path: Path) -> None:
    partial = tmp_path / "file.bin"
    partial.write_bytes(b"abc")
    assert compute_download_offset(partial, 10, resume=True) == 3


def test_download_offset_resets_when_local_larger_than_remote(tmp_path: Path) -> None:
    partial = tmp_path / "file.bin"
    partial.write_bytes(b"abcdef")
    assert compute_download_offset(partial, 3, resume=True) == 0


def test_upload_offset_uses_remote_partial_size() -> None:
    assert compute_upload_offset(local_size=10, remote_size=4, resume=True) == 4


def test_upload_offset_resets_when_remote_larger_than_local() -> None:
    assert compute_upload_offset(local_size=3, remote_size=8, resume=True) == 0


def test_upload_offset_without_resume() -> None:
    assert compute_upload_offset(local_size=10, remote_size=4, resume=False) == 0
