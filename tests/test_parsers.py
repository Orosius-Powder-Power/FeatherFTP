"""
@file test_parsers.py
@brief 测试 FTP 响应解析器的单元测试套件。
@details
该文件负责验证客户端解析服务器响应的能力，涵盖以下功能：
- 被动模式 (PASV/ESPS) IP 和端口的提取
- 目录列表解析（支持 Unix、Windows 及 MLSX 格式）
- 文件属性（大小、时间、类型、权限）的正确映射
- 特殊条目（如 . 和 ..）的过滤逻辑
"""


from __future__ import annotations

from datetime import datetime

import pytest

from ftp_client.models import FtpEntryType
from ftp_client.parsers import (
    parse_directory_listing,
    parse_epsv_response,
    parse_list_line,
    parse_mlsx_line,
    parse_pasv_response,
)


def test_parse_pasv_response() -> None:
    assert parse_pasv_response("Entering Passive Mode (192,168,1,2,195,80).") == (
        "192.168.1.2",
        50000,
    )


def test_parse_epsv_response() -> None:
    assert parse_epsv_response("Entering Extended Passive Mode (|||49152|)", "ftp.example.com") == (
        "ftp.example.com",
        49152,
    )


def test_parse_invalid_pasv_response() -> None:
    with pytest.raises(Exception):
        parse_pasv_response("bad")


def test_parse_unix_listing_file() -> None:
    entry = parse_list_line(
        "-rw-r--r-- 1 user group 1234 Jun 15 14:20 report.txt",
        now=datetime(2026, 6, 15, 15, 0),
    )
    assert entry.name == "report.txt"
    assert entry.type == FtpEntryType.FILE
    assert entry.size == 1234
    assert entry.modified == datetime(2026, 6, 15, 14, 20)


def test_parse_unix_listing_directory() -> None:
    entry = parse_list_line("drwxr-xr-x 2 user group 4096 Jan 02 2025 docs")
    assert entry.name == "docs"
    assert entry.type == FtpEntryType.DIRECTORY


def test_parse_windows_listing() -> None:
    entry = parse_list_line("06-15-26  02:21PM       <DIR>          uploads")
    assert entry.name == "uploads"
    assert entry.type == FtpEntryType.DIRECTORY
    assert entry.modified == datetime(2026, 6, 15, 14, 21)


def test_parse_mlsx_line() -> None:
    entry = parse_mlsx_line("type=file;size=42;modify=20260615142100;perm=adfr; hello.txt")
    assert entry.name == "hello.txt"
    assert entry.type == FtpEntryType.FILE
    assert entry.size == 42
    assert entry.permissions == "adfr"


def test_parse_directory_listing_filters_dot_entries() -> None:
    text = "\n".join(
        [
            "type=cdir; .",
            "type=pdir; ..",
            "type=dir;modify=20260615142100; src",
        ]
    )
    entries = parse_directory_listing(text, prefer_mlsx=True)
    assert [entry.name for entry in entries] == ["src"]
