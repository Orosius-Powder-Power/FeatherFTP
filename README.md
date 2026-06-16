# Socket FTP Client

Windows graphical FTP client for the 2026 Computer Networks Practice project.

The FTP protocol layer is implemented directly with Python's standard `socket`
module. The project does not use `ftplib`, `FluentFTP`, or any other high-level
FTP client library.

## Features

- PySide6 desktop GUI with site manager, local browser, remote browser, transfer
  queue, status bar, and protocol log.
- WinSCP-style right-click operations and drag-and-drop transfers between local
  and remote panes.
- FTP login, directory browsing, upload, download, resume, delete, rename, and
  create directory.
- Local files open with the operating system's default app; remote files are
  downloaded to a temporary cache and then opened locally.
- Passive mode data connections with `EPSV` first and `PASV` fallback.
- Resume support through `REST` + `RETR` for downloads and `REST` + `STOR` for
  uploads when the server supports it.
- SQLite site and history storage through the standard library. Passwords are
  not saved by default.
- Protocol log shows real FTP commands and replies for course demonstration.

## Run

WSL or Linux:

```bash
.venv/bin/python scripts/run_demo_ftp_server.py
bash scripts/run.sh
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m ftp_client
```

Windows Command Prompt:

```bat
scripts\run_windows.bat
```

## Writable Demo FTP Server

For upload/download demonstrations, start the bundled writable FTP server in a
separate terminal:

```bash
.venv/bin/python scripts/run_demo_ftp_server.py
```

If you are already using an activated virtual environment, `python
scripts/run_demo_ftp_server.py` works as well.

Then connect from the GUI with:

```text
Host: 127.0.0.1
Port: 2121
User: demo
Password: demo123
```

Its root directory is `demo_ftp_root/`, which is ignored by Git. This server is
only for local testing; the client itself still performs FTP protocol operations
through its own socket implementation.

## Tests

```bash
PYTHONPATH=src python -m pytest
```

The tests focus on protocol parsing and resume offset logic. GUI testing is
mainly manual because the target deliverable is a Windows desktop application.
