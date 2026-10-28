"""Terminal CLI for Laptop 1 to access Laptop 2's file explorer and email files."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import config
import tools

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _format_remote_base(ip: str, port: int) -> str:
    clean = ip.strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean.rstrip("/")
    if ":" in clean:
        return f"http://{clean}"
    return f"http://{clean}:{port}"


def ping_remote(ip: str, pin: str, port: int = 8765) -> dict:
    base = _format_remote_base(ip, port)
    url = f"{base}/ping?pin={urllib.parse.quote(pin)}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def list_remote(ip: str, pin: str, folder: str = "", port: int = 8765) -> dict:
    folder_enc = urllib.parse.quote(folder)
    pin_enc = urllib.parse.quote(pin)
    base = _format_remote_base(ip, port)
    url = f"{base}/list?pin={pin_enc}&folder={folder_enc}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def download_remote(ip: str, pin: str, file_rel: str, dest_dir: Path | None = None, port: int = 8765) -> Path | None:
    if dest_dir is None:
        dest_dir = config.DESKTOP / "remote_downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    file_enc = urllib.parse.quote(file_rel)
    pin_enc = urllib.parse.quote(pin)
    base = _format_remote_base(ip, port)
    url = f"{base}/download?pin={pin_enc}&file={file_enc}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            filename = Path(file_rel).name
            dest_file = dest_dir / filename
            data = resp.read()
            dest_file.write_bytes(data)
            return dest_file
    except Exception as exc:
        print(f"[ERROR] Failed to download '{file_rel}': {exc}")
        return None


def interactive_session() -> None:
    print("\n" + "=" * 64)
    print("      DRL REMOTE EXPLORER - TERMINAL CLIENT (Laptop 1)          ")
    print("=" * 64)

    try:
        ip = input("Enter Laptop 2 (Person 2) IP Address: ").strip()
        if not ip:
            print("IP address is required.")
            return

        pin = input("Enter 4-digit Access PIN: ").strip()
        if not pin:
            print("PIN is required.")
            return

        print(f"\n[~] Connecting to {ip}:8765 with PIN '{pin}'...")
        status = ping_remote(ip, pin)
        if "error" in status:
            print(f"[!] Connection failed: {status['error']}")
            print("Make sure Person 2 has 'remote_node.py' running and you are on the same Wi-Fi.")
            return

        print(f"[+] Connected successfully to '{status.get('device_name', 'Remote PC')}'!")
        print(f"    Consented Root: {status.get('shared_directory', '')}\n")

        current_folder = ""
        while True:
            res = list_remote(ip, pin, folder=current_folder)
            if "error" in res:
                print(f"[!] Could not open folder '{current_folder}': {res['error']}")
                if current_folder:
                    parts = current_folder.rstrip("/\\").split("/")
                    current_folder = "/".join(parts[:-1])
                    continue
                break

            items = res.get("items", [])
            display_path = f"/{current_folder}" if current_folder else "/"
            print("-" * 64)
            print(f"Directory: {display_path}  ({len(items)} items)")
            print("-" * 64)
            print(f" {'#':<3} | {'Type':<6} | {'Name':<32} | {'Size':<10} | {'Modified'}")
            print("-" * 64)

            idx_map = {}
            for idx, it in enumerate(items, 1):
                t_str = "[DIR]" if it["is_dir"] else "[FILE]"
                name_str = (it["name"][:29] + "...") if len(it["name"]) > 32 else it["name"]
                print(f" {idx:<3} | {t_str:<6} | {name_str:<32} | {it['size_readable']:<10} | {it['modified']}")
                idx_map[str(idx)] = it

            print("-" * 64)
            print("Actions:")
            print("  <number>        - Open folder OR download/email file")
            print("  ..              - Go up to parent folder")
            print("  refresh         - Reload current directory")
            print("  quit / q        - Disconnect and exit")
            print("-" * 64)

            choice = input("Enter choice: ").strip()
            if choice.lower() in ("q", "quit", "exit"):
                print("Disconnected.")
                break
            elif choice == "..":
                if current_folder:
                    parts = current_folder.rstrip("/\\").split("/")
                    current_folder = "/".join(parts[:-1])
                continue
            elif choice.lower() == "refresh":
                continue
            elif choice in idx_map:
                selected = idx_map[choice]
                if selected["is_dir"]:
                    current_folder = selected["relative_path"]
                else:
                    # File chosen: options to download or send as mail
                    print(f"\nSelected File: {selected['name']} ({selected['size_readable']})")
                    print("  1. Download to local Desktop")
                    print("  2. Download AND Send as Email")
                    print("  3. Cancel")
                    act = input("Choose action (1/2/3): ").strip()

                    if act == "1":
                        print(f"Downloading '{selected['name']}'...")
                        local_path = download_remote(ip, pin, selected["relative_path"])
                        if local_path:
                            print(f"[+] Saved to: {local_path}")
                    elif act == "2":
                        to_email = input("Enter recipient email address: ").strip()
                        if to_email and "@" in to_email:
                            print(f"Downloading '{selected['name']}' from remote PC...")
                            local_path = download_remote(ip, pin, selected["relative_path"])
                            if local_path:
                                print(f"[+] Downloaded. Sending email to {to_email}...")
                                mail_result = tools.send_email(
                                    to_email=to_email,
                                    subject=f"Remote File: {selected['name']}",
                                    body=f"Please find attached the file '{selected['name']}' retrieved with consent from remote PC ({ip}).",
                                    attachments=str(local_path.name),
                                )
                                print(f"[+] {mail_result}")
                        else:
                            print("[!] Invalid email address.")
            else:
                print("Invalid selection.")
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal Client to browse remote PC and email files.")
    parser.add_argument("--ip", type=str, default="", help="IP of remote PC")
    parser.add_argument("--pin", type=str, default="", help="PIN from remote PC")
    parser.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    parser.add_argument("--list", action="store_true", help="List files in remote root")
    parser.add_argument("--folder", type=str, default="", help="Remote subfolder to list")
    parser.add_argument("--file", type=str, default="", help="Remote file to download")
    parser.add_argument("--mail-to", type=str, default="", help="Send downloaded file to this email")

    args = parser.parse_args()

    if not args.ip or not args.pin:
        interactive_session()
        return

    # Non-interactive CLI commands
    if args.list:
        res = list_remote(args.ip, args.pin, folder=args.folder, port=args.port)
        if "error" in res:
            print(f"Error: {res['error']}")
            sys.exit(1)
        print(json.dumps(res, indent=2))
        return

    if args.file:
        dest = download_remote(args.ip, args.pin, args.file, port=args.port)
        if not dest:
            sys.exit(1)
        print(f"Downloaded: {dest}")
        if args.mail_to:
            print(f"Sending email to {args.mail_to}...")
            result = tools.send_email(
                to_email=args.mail_to,
                subject=f"Remote File: {dest.name}",
                body=f"Attached file: {dest.name} fetched from remote PC ({args.ip}).",
                attachments=str(dest.name),
            )
            print(result)


if __name__ == "__main__":
    main()
