from __future__ import annotations

from ftp_client.protocol import _friendly_connect_error


def test_friendly_connect_error_mentions_ftp_vs_sftp() -> None:
    message = _friendly_connect_error("8.8.8.8", 11295, RuntimeError("closed"))

    assert "FTP" in message
    assert "SSH/SFTP" in message
    assert "11295" in message
