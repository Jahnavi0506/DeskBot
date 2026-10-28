"""SSH File Fetcher & Emailer for Laptop 1.

Connects to Laptop 2 using Windows built-in OpenSSH / SCP, downloads the requested
file, and sends it as an email attachment using the agent's SMTP configuration.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config
import tools


def fetch_file_via_scp(ip: str, user: str, remote_file_path: str, dest_dir: Path | None = None, port: int = 22) -> Path | None:
    """Download a file from Laptop 2 using native scp."""
    if dest_dir is None:
        dest_dir = config.DESKTOP / "remote_downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(remote_file_path.replace("\\", "/")).name
    local_dest = dest_dir / filename

    remote_spec = f"{user}@{ip}:{remote_file_path}"
    print(f"\n[+] Connecting via SCP to {remote_spec} (Port {port})...")
    print(f"[*] (If prompted, enter {user}'s Windows password on Laptop 2)")

    cmd = ["scp", "-P", str(port), remote_spec, str(local_dest)]
    try:
        res = subprocess.run(cmd)
        if res.returncode == 0 and local_dest.exists():
            print(f"[+] Download successful! Saved to: {local_dest}")
            return local_dest
        else:
            print(f"[-] SCP failed with exit code: {res.returncode}")
            return None
    except Exception as exc:
        print(f"[-] Error executing SCP: {exc}")
        return None


def interactive_ssh_session() -> None:
    print("\n" + "=" * 64)
    print("        SSH FILE FETCHER & EMAIL CLIENT (Laptop 1)        ")
    print("=" * 64)

    ip = input("Enter Laptop 2 (Person 2) IP Address: ").strip()
    if not ip:
        print("IP address is required.")
        return

    user = input("Enter Laptop 2 Windows Username: ").strip()
    if not user:
        print("Username is required.")
        return

    print("\nChoose an action:")
    print("  1. Open Interactive SFTP Terminal (Browse Laptop 2 files)")
    print("  2. Download a specific file via SCP")
    print("  3. Download a file AND Email it to a recipient")
    print("  4. Open Remote SSH Shell")
    choice = input("Enter choice (1/2/3/4): ").strip()

    if choice == "1":
        print(f"\n[+] Starting SFTP session with {user}@{ip}...")
        print("    Use 'ls' to list files, 'cd <dir>' to navigate, 'get <file>' to download, 'bye' to exit.")
        subprocess.run(["sftp", f"{user}@{ip}"])

    elif choice == "2":
        remote_path = input("Enter remote file path on Laptop 2 (e.g. Documents/report.pdf): ").strip()
        if remote_path:
            fetch_file_via_scp(ip, user, remote_path)

    elif choice == "3":
        remote_path = input("Enter remote file path on Laptop 2 (e.g. Documents/report.pdf): ").strip()
        to_email = input("Enter recipient email address: ").strip()
        if remote_path and to_email and "@" in to_email:
            local_file = fetch_file_via_scp(ip, user, remote_path)
            if local_file:
                print(f"\n[+] Sending '{local_file.name}' to {to_email}...")
                result = tools.send_email(
                    to_email=to_email,
                    subject=f"Requested File: {local_file.name}",
                    body=f"Please find attached '{local_file.name}' retrieved via SSH from remote laptop ({ip}).",
                    attachments=str(local_file.name),
                )
                print(f"[+] {result}")
        else:
            print("Invalid file path or email address.")

    elif choice == "4":
        print(f"\n[+] Opening SSH Shell on {user}@{ip}...")
        subprocess.run(["ssh", f"{user}@{ip}"])
    else:
        print("Invalid choice.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SSH/SCP File Fetcher and Emailer")
    parser.add_argument("--ip", type=str, default="", help="IP address of Laptop 2")
    parser.add_argument("--user", type=str, default="", help="Username on Laptop 2")
    parser.add_argument("--file", type=str, default="", help="Remote file path to download")
    parser.add_argument("--mail-to", type=str, default="", help="Email address to send the file to")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default 22)")

    args = parser.parse_args()

    if not args.ip or not args.user:
        interactive_ssh_session()
        return

    if args.file:
        local_path = fetch_file_via_scp(args.ip, args.user, args.file, port=args.port)
        if local_path and args.mail_to:
            print(f"[+] Emailing '{local_path.name}' to {args.mail_to}...")
            result = tools.send_email(
                to_email=args.mail_to,
                subject=f"Requested File: {local_path.name}",
                body=f"Please find attached '{local_path.name}' retrieved via SSH from Laptop 2 ({args.ip}).",
                attachments=str(local_path.name),
            )
            print(f"[+] {result}")


if __name__ == "__main__":
    main()
