from __future__ import annotations

import socket
import time
from pathlib import Path
from threading import Event
from typing import BinaryIO

from .errors import FtpCommandError, FtpConnectionError, FtpProtocolError
from .models import FtpConnectionConfig, FtpEntry, FtpReply, LogCallback, ProgressCallback
from .parsers import parse_directory_listing, parse_epsv_response, parse_pasv_response

BUFFER_SIZE = 64 * 1024


class FtpSession:
    """FTP control and data connections implemented directly with sockets."""

    def __init__(self, log_callback: LogCallback | None = None) -> None:
        self.config: FtpConnectionConfig | None = None
        self.control: socket.socket | None = None
        self.reader: BinaryIO | None = None
        self.features: set[str] = set()
        self.current_directory = "/"
        self.log_callback = log_callback

    def connect(
        self,
        host: str,
        port: int = 21,
        username: str = "anonymous",
        password: str = "anonymous@",
        timeout: float = 15.0,
        passive_mode: bool = True,
    ) -> FtpReply:
        self.config = FtpConnectionConfig(host, port, username, password, timeout, passive_mode)
        try:
            self.control = socket.create_connection((host, port), timeout=timeout)
            self.control.settimeout(timeout)
            self.reader = self.control.makefile("rb")
        except OSError as exc:
            raise FtpConnectionError(f"Unable to connect to {host}:{port}: {exc}") from exc

        try:
            welcome = self._read_reply()
            if welcome.code != 220:
                raise FtpConnectionError(
                    f"Server did not send an FTP welcome reply: {welcome.code} {welcome.message}"
                )
            self._expect("USER", username, {230, 331})
            if self.last_reply.code == 331:
                self._expect("PASS", password, {230, 202})
            self.type_binary()
            self.features = self._load_features()
            self.current_directory = self.pwd()
            return welcome
        except (FtpConnectionError, FtpProtocolError, FtpCommandError) as exc:
            self._force_close()
            raise FtpConnectionError(_friendly_connect_error(host, port, exc)) from exc

    @property
    def last_reply(self) -> FtpReply:
        if not hasattr(self, "_last_reply"):
            raise FtpProtocolError("No FTP reply has been read yet")
        return self._last_reply

    def close(self) -> None:
        try:
            if self.control:
                try:
                    self.command("QUIT")
                except Exception:
                    pass
        finally:
            if self.reader:
                self.reader.close()
            if self.control:
                self.control.close()
            self.reader = None
            self.control = None

    def _force_close(self) -> None:
        if self.reader:
            try:
                self.reader.close()
            except Exception:
                pass
        if self.control:
            try:
                self.control.close()
            except Exception:
                pass
        self.reader = None
        self.control = None

    def command(self, command: str, argument: str | None = None) -> FtpReply:
        self._send_command(command, argument)
        return self._read_reply()

    def noop(self) -> FtpReply:
        return self._expect("NOOP", None, {200})

    def type_binary(self) -> FtpReply:
        return self._expect("TYPE", "I", {200})

    def pwd(self) -> str:
        reply = self._expect("PWD", None, {257})
        start = reply.message.find('"')
        end = reply.message.find('"', start + 1)
        if start >= 0 and end > start:
            return reply.message[start + 1 : end].replace('""', '"')
        return "/"

    def cwd(self, path: str) -> FtpReply:
        reply = self._expect("CWD", path, {250})
        self.current_directory = self.pwd()
        return reply

    def cdup(self) -> FtpReply:
        reply = self._expect("CDUP", None, {200, 250})
        self.current_directory = self.pwd()
        return reply

    def list(self, path: str = "") -> list[FtpEntry]:
        use_mlsd = "MLST" in self.features or "MLSD" in self.features
        command = "MLSD" if use_mlsd else "LIST"
        data_sock = self._open_data_socket()
        try:
            self._send_command(command, path or None)
            first = self._read_reply()
            if not first.is_preliminary:
                raise FtpCommandError(command, first)
            data = self._read_all_data(data_sock)
            final = self._read_reply()
            if not final.is_positive:
                raise FtpCommandError(command, final)
        finally:
            data_sock.close()
        return parse_directory_listing(data.decode("utf-8", errors="replace"), prefer_mlsx=use_mlsd)

    def size(self, remote_path: str) -> int | None:
        reply = self.command("SIZE", remote_path)
        if reply.code == 550:
            return None
        if reply.code != 213:
            raise FtpCommandError("SIZE", reply)
        try:
            return int(reply.message.strip())
        except ValueError as exc:
            raise FtpProtocolError(f"Invalid SIZE reply: {reply.message}") from exc

    def mdtm(self, remote_path: str) -> str | None:
        reply = self.command("MDTM", remote_path)
        if reply.code == 550:
            return None
        if reply.code != 213:
            raise FtpCommandError("MDTM", reply)
        return reply.message.strip()

    def delete(self, remote_path: str) -> FtpReply:
        return self._expect("DELE", remote_path, {250})

    def rmdir(self, remote_path: str) -> FtpReply:
        return self._expect("RMD", remote_path, {250})

    def mkdir(self, remote_path: str) -> FtpReply:
        return self._expect("MKD", remote_path, {257, 250})

    def rename(self, old_path: str, new_path: str) -> FtpReply:
        self._expect("RNFR", old_path, {350})
        return self._expect("RNTO", new_path, {250})

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        resume: bool = True,
        progress: ProgressCallback | None = None,
        pause_event: Event | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        total = self.size(remote_path)
        offset = local_path.stat().st_size if resume and local_path.exists() else 0
        if total is not None and offset > total:
            offset = 0
        if offset:
            try:
                self._rest(offset)
            except FtpCommandError:
                offset = 0
        mode = "ab" if offset else "wb"
        data_sock = self._open_data_socket()
        transferred = offset
        start_time = time.monotonic()
        try:
            self._send_command("RETR", remote_path)
            first = self._read_reply()
            if not first.is_preliminary:
                raise FtpCommandError("RETR", first)
            with local_path.open(mode + "") as target:
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise TransferInterrupted("cancelled")
                    if pause_event and pause_event.is_set():
                        raise TransferInterrupted("paused")
                    chunk = data_sock.recv(BUFFER_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)
                    transferred += len(chunk)
                    if progress:
                        progress(transferred, total, _speed(transferred - offset, start_time))
            final = self._read_reply()
            if not final.is_positive:
                raise FtpCommandError("RETR", final)
        finally:
            data_sock.close()

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        resume: bool = True,
        progress: ProgressCallback | None = None,
        pause_event: Event | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        total = local_path.stat().st_size
        offset = self.size(remote_path) if resume else 0
        offset = offset or 0
        if offset > total:
            offset = 0
        if offset:
            try:
                self._rest(offset)
            except FtpCommandError:
                self._log("!", "Server rejected REST; falling back to full overwrite upload.")
                offset = 0
        data_sock = self._open_data_socket()
        transferred = offset
        start_time = time.monotonic()
        try:
            self._send_command("STOR", remote_path)
            first = self._read_reply()
            if not first.is_preliminary:
                raise FtpCommandError("STOR", first)
            with local_path.open("rb") as source:
                if offset:
                    source.seek(offset)
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise TransferInterrupted("cancelled")
                    if pause_event and pause_event.is_set():
                        raise TransferInterrupted("paused")
                    chunk = source.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    data_sock.sendall(chunk)
                    transferred += len(chunk)
                    if progress:
                        progress(transferred, total, _speed(transferred - offset, start_time))
            try:
                data_sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            final = self._read_reply()
            if not final.is_positive:
                raise FtpCommandError("STOR", final)
        finally:
            data_sock.close()

    def _load_features(self) -> set[str]:
        reply = self.command("FEAT")
        if reply.code != 211:
            return set()
        features: set[str] = set()
        for line in reply.lines:
            text = line.strip()
            if text and not text.startswith("211"):
                features.add(text.split()[0].upper())
        return features

    def _rest(self, offset: int) -> FtpReply:
        return self._expect("REST", str(offset), {350})

    def _open_data_socket(self) -> socket.socket:
        if not self.config:
            raise FtpConnectionError("FTP session is not connected")
        if not self.config.passive_mode:
            raise FtpConnectionError("Active mode is not implemented; use passive mode")

        host: str
        port: int
        try:
            reply = self._expect("EPSV", None, {229})
            host, port = parse_epsv_response(reply.message, self.config.host)
        except Exception:
            reply = self._expect("PASV", None, {227})
            host, port = parse_pasv_response(reply.message)

        try:
            sock = socket.create_connection((host, port), timeout=self.config.timeout)
            sock.settimeout(self.config.timeout)
            return sock
        except OSError as exc:
            raise FtpConnectionError(f"Unable to open data connection to {host}:{port}: {exc}") from exc

    def _read_all_data(self, data_sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = data_sock.recv(BUFFER_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _expect(self, command: str, argument: str | None, ok_codes: set[int]) -> FtpReply:
        reply = self.command(command, argument)
        if reply.code not in ok_codes:
            raise FtpCommandError(command, reply)
        return reply

    def _send_command(self, command: str, argument: str | None = None) -> None:
        if not self.control:
            raise FtpConnectionError("FTP session is not connected")
        line = command if argument is None else f"{command} {argument}"
        wire = f"{line}\r\n".encode("utf-8")
        self._log("C", _redact_password(line))
        try:
            self.control.sendall(wire)
        except OSError as exc:
            raise FtpConnectionError(f"Failed to send FTP command {command}: {exc}") from exc

    def _read_reply(self) -> FtpReply:
        if not self.reader:
            raise FtpConnectionError("FTP session is not connected")
        first_line = self._readline()
        if len(first_line) < 3 or not first_line[:3].isdigit():
            raise FtpProtocolError(f"Invalid FTP reply: {first_line}")

        code = int(first_line[:3])
        lines = [first_line]
        if len(first_line) > 3 and first_line[3] == "-":
            terminator = f"{code} "
            while True:
                line = self._readline()
                lines.append(line)
                if line.startswith(terminator):
                    break

        message_lines = []
        for line in lines:
            if len(line) > 4 and line[:3].isdigit() and line[3] in {" ", "-"}:
                message_lines.append(line[4:])
            else:
                message_lines.append(line)
        reply = FtpReply(code=code, message="\n".join(message_lines).strip(), lines=lines)
        self._last_reply = reply
        for line in lines:
            self._log("S", line)
        return reply

    def _readline(self) -> str:
        assert self.reader is not None
        try:
            raw = self.reader.readline()
        except OSError as exc:
            raise FtpConnectionError(f"Failed to read FTP reply: {exc}") from exc
        if not raw:
            raise FtpConnectionError("FTP server closed the control connection")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def _log(self, direction: str, text: str) -> None:
        if self.log_callback:
            self.log_callback(direction, text)


class TransferInterrupted(Exception):
    """Internal signal used for pause/cancel during a transfer."""


def _speed(bytes_done: int, start_time: float) -> float:
    elapsed = max(time.monotonic() - start_time, 0.001)
    return bytes_done / elapsed


def _redact_password(line: str) -> str:
    if line.upper().startswith("PASS "):
        return "PASS ********"
    return line


def _friendly_connect_error(host: str, port: int, exc: Exception) -> str:
    text = str(exc)
    hints = [
        f"无法完成 FTP 握手：{host}:{port}",
        f"原始错误：{text}",
        "请确认该地址开放的是 FTP 服务，而不是 SSH/SFTP 服务。",
        "FTP 常见端口是 21；SFTP/SSH 常见端口是 22 或自定义 SSH 端口，二者协议不同。",
    ]
    if port not in {21, 20, 990}:
        hints.append(f"当前端口 {port} 不是常见 FTP 控制端口，请向服务器管理员确认 FTP 端口。")
    return "\n".join(hints)
