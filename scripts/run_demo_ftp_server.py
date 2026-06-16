from __future__ import annotations

import argparse
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer


def prepare_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(exist_ok=True)
    (root / "downloads").mkdir(exist_ok=True)
    readme = root / "downloads" / "welcome.txt"
    if not readme.exists():
        readme.write_text(
            "FeatherFTP demo server\n\n"
            "This local FTP server supports browsing, upload, download, rename, "
            "delete, mkdir, and resume-friendly file transfer tests.\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a writable local FTP demo server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2121)
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo123")
    parser.add_argument("--root", type=Path, default=Path("demo_ftp_root"))
    args = parser.parse_args()

    root = args.root.resolve()
    prepare_root(root)

    authorizer = DummyAuthorizer()
    authorizer.add_user(
        args.user,
        args.password,
        str(root),
        perm="elradfmwMT",
    )

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.banner = "FeatherFTP writable demo server ready."

    server = FTPServer((args.host, args.port), handler)
    print("Writable FTP demo server is running.")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"User: {args.user}")
    print(f"Password: {args.password}")
    print(f"Root: {root}")
    server.serve_forever()


if __name__ == "__main__":
    main()
