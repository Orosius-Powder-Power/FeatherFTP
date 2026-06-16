from __future__ import annotations

from .models import FtpReply


class FtpError(Exception):
    """Base class for FTP client errors."""


class FtpConnectionError(FtpError):
    """Raised when a control or data connection cannot be established."""


class FtpProtocolError(FtpError):
    """Raised when the server sends an invalid FTP response."""


class FtpCommandError(FtpError):
    """Raised when the server rejects an FTP command."""

    def __init__(self, command: str, reply: FtpReply):
        self.command = command
        self.reply = reply
        super().__init__(f"{command} failed with {reply.code}: {reply.message}")
