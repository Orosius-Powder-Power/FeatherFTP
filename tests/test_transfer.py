"""
@file test_transfer.py
@brief 测试文件传输偏移量计算的单元测试套件。
@details
该文件专注于验证断点续传（Resume）逻辑的核心算法。
测试用例覆盖了下载和上传两种场景，包括：
- 本地文件缺失或为空的情况
- 本地/远程文件大小对比（如本地大于远程时重置偏移）
- 断点续传标志（resume=True/False）对计算结果的影响
"""

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
