"""DRL Remote File Node - Companion Service for Person 2 (Consent Server).

Run this on Laptop 2 to grant authorized, read-only access to a specific folder.
Requires standard Python only (zero extra pip packages).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class RemoteNodeHandler(BaseHTTPRequestHandler):
    shared_dir: Path
    auth_pin: str

    def log_message(self, format: str, *args: object) -> None:
        # Override default noisy HTTP logging; custom audit logging used below
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        client_ip = self.client_address[0]
        timestamp = time.strftime("%H:%M:%S")

        req_pin = params.get("pin", [""])[0].strip()
        endpoint = parsed.path.rstrip("/")

        # Root / Ping endpoint
        if endpoint in ("", "/ping", "/status"):
            if req_pin != self.auth_pin:
                print(f"[{timestamp}] [SECURITY ALERT] Unauthorized ping rejected from {client_ip} (Invalid PIN)")
                self._send_json({"error": "Forbidden: Invalid or missing PIN"}, status=403)
                return
            print(f"[{timestamp}] Authorized connection verified from {client_ip}")
            self._send_json({
                "status": "ok",
                "device_name": socket.gethostname(),
                "shared_directory": str(self.shared_dir),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            return

        # PIN Authorization check for all other endpoints
        if req_pin != self.auth_pin:
            print(f"[{timestamp}] [SECURITY ALERT] Access denied to {client_ip} on {endpoint} (Invalid PIN)")
            self._send_json({"error": "Forbidden: Invalid or missing PIN"}, status=403)
            return

        # 1. Directory Listing: /list?pin=...&folder=subfolder
        if endpoint == "/list":
            sub_folder = params.get("folder", [""])[0].strip().strip("/\\")
            target_dir = (self.shared_dir / sub_folder).resolve()

            # Guard against path traversal
            try:
                target_dir.relative_to(self.shared_dir.resolve())
            except ValueError:
                print(f"[{timestamp}] [SECURITY ALERT] Path traversal blocked from {client_ip}: '{sub_folder}'")
                self._send_json({"error": "Access Denied: Path traversal is forbidden"}, status=403)
                return

            if not target_dir.exists() or not target_dir.is_dir():
                self._send_json({"error": f"Folder not found: {sub_folder}"}, status=404)
                return

            items = []
            try:
                for entry in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if entry.is_symlink() or entry.name.lower() in ("my music", "my videos", "my pictures"):
                        continue
                    try:
                        stat = entry.stat()
                        items.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size_bytes": stat.st_size if entry.is_file() else 0,
                            "size_readable": self._format_size(stat.st_size) if entry.is_file() else "DIR",
                            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                            "relative_path": str(entry.relative_to(self.shared_dir.resolve())).replace("\\", "/"),
                        })
                    except (PermissionError, OSError):
                        continue
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return

            display_sub = f"/{sub_folder}" if sub_folder else "root"
            print(f"[{timestamp}] {client_ip} browsed directory: '{display_sub}' ({len(items)} items)")
            self._send_json({
                "status": "success",
                "folder": sub_folder,
                "current_path": str(target_dir),
                "count": len(items),
                "items": items,
            })
            return

        # 2. File Download: /download?pin=...&file=subfolder/file.ext
        if endpoint == "/download":
            file_rel = params.get("file", [""])[0].strip().strip("/\\")
            if not file_rel:
                self._send_json({"error": "Missing 'file' parameter"}, status=400)
                return

            target_file = (self.shared_dir / file_rel).resolve()

            # Guard against path traversal
            try:
                target_file.relative_to(self.shared_dir.resolve())
            except ValueError:
                print(f"[{timestamp}] [SECURITY ALERT] Path traversal download blocked from {client_ip}: '{file_rel}'")
                self._send_json({"error": "Access Denied: Path traversal is forbidden"}, status=403)
                return

            # If file doesn't exist directly, try fuzzy search inside shared_dir
            if not target_file.exists() or not target_file.is_file():
                found = None
                search_name = Path(file_rel).name.lower()
                for f in self.shared_dir.rglob("*"):
                    if f.is_file() and f.name.lower() == search_name:
                        found = f
                        break
                if found:
                    target_file = found
                else:
                    print(f"[{timestamp}] {client_ip} requested file not found: '{file_rel}'")
                    self._send_json({"error": f"File not found: {file_rel}"}, status=404)
                    return

            try:
                data = target_file.read_bytes()
                print(f"[{timestamp}] [TRANSFER SUCCESS] Dispatched '{target_file.name}' ({self._format_size(len(data))}) to {client_ip}")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{target_file.name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                self._send_json({"error": f"File read error: {exc}"}, status=500)
            return

        # Unknown endpoint
        self._send_json({"error": "Unknown endpoint. Supported: /ping, /list, /download"}, status=404)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"


def main() -> None:
    parser = argparse.ArgumentParser(description="DRL Remote File Node (Person 2 Companion)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default 8765)")
    parser.add_argument("--pin", type=str, default="", help="Custom 4-digit PIN (random if empty)")
    parser.add_argument("--folder", type=str, default="", help="Directory to share")
    args = parser.parse_args()

    local_ip = get_local_ip()
    port = args.port
    auth_pin = args.pin if args.pin else str(random.randint(1000, 9999))

    # Determine default folder
    default_dir = Path.home() / "Documents"
    if not default_dir.exists():
        default_dir = Path.home() / "OneDrive" / "Documents"
    if not default_dir.exists():
        default_dir = Path.home() / "Desktop"
    if not default_dir.exists():
        default_dir = Path.home()

    shared_folder_path = None
    if args.folder:
        p = Path(args.folder).expanduser().resolve()
        if p.exists() and p.is_dir():
            shared_folder_path = p

    if not shared_folder_path:
        print("\n================================================================")
        print("     DRL Remote File Node - Consent Configuration (Laptop 2)    ")
        print("================================================================")
        print(f"Default consented directory: {default_dir}")
        try:
            choice = input("Press ENTER to share this folder, or type a custom path: ").strip()
            if choice:
                custom_p = Path(choice).expanduser().resolve()
                if custom_p.exists() and custom_p.is_dir():
                    shared_folder_path = custom_p
                else:
                    print(f"Invalid path '{choice}', using default: {default_dir}")
                    shared_folder_path = default_dir
            else:
                shared_folder_path = default_dir
        except (EOFError, KeyboardInterrupt):
            shared_folder_path = default_dir

    handler = RemoteNodeHandler
    handler.shared_dir = shared_folder_path
    handler.auth_pin = auth_pin

    server = HTTPServer(("0.0.0.0", port), handler)

    print("\n" + "=" * 66)
    print("      DRL REMOTE FILE NODE - LIVE CONSENT SHARING ACTIVE         ")
    print("=" * 66)
    print(f"  [+] Device Name:         {socket.gethostname()}")
    print(f"  [+] Local Wi-Fi IP:      {local_ip}")
    print(f"  [+] Port:                {port}")
    print(f"  [+] One-Time Access PIN: {auth_pin}")
    print(f"  [+] Shared Directory:    {shared_folder_path}")
    print("-" * 66)
    print(f"  To connect from Laptop 1, use:")
    print(f"    IP:   {local_ip}")
    print(f"    PIN:  {auth_pin}")
    print("-" * 66)
    print("  STATUS: Listening for connections... Press Ctrl+C to stop.")
    print("=" * 66 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Remote Node service stopped. Sharing terminated.")
        server.server_close()


if __name__ == "__main__":
    main()
