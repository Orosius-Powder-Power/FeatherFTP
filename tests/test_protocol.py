"""
@file test_protocol.py
@brief 测试协议相关功能的单元测试套件。
@details
该文件主要针对 FTP 客户端的协议处理逻辑进行验证，特别是连接错误的友好提示功能。
确保在底层连接发生异常时，能够生成包含关键信息（如地址、端口）且易于用户理解的错误消息，
并能正确区分 FTP 与 SSH/SFTP 协议。
"""

from __future__ import annotations

from ftp_client.protocol import _friendly_connect_error


def test_friendly_connect_error_mentions_ftp_vs_sftp() -> None:
    message = _friendly_connect_error("8.8.8.8", 11295, RuntimeError("closed"))

    assert "FTP" in message
    assert "SSH/SFTP" in message
    assert "11295" in message
