"""Keep file writes on the Desktop and confirm risky actions."""

from __future__ import annotations

from pathlib import Path

import config


class SafetyError(ValueError):
    pass


def resolve_user_path(raw: str, default_folder: str = "") -> Path:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise SafetyError("Empty path.")

    # Determine default folder hint ('downloads', 'documents', 'desktop')
    folder_hint = (default_folder or "").strip().lower()
    if not folder_hint:
        try:
            import tools as _t
            folder_hint = getattr(_t, "_session_target_folder", "desktop").lower()
        except Exception:
            folder_hint = "desktop"

    path = Path(text).expanduser()
    if path.is_absolute() and path.exists():
        return path.resolve()

    rem_dl = config.DESKTOP / "remote_downloads"
    if "picture" in folder_hint or "photo" in folder_hint or "image" in folder_hint:
        search_roots = [config.PICTURES, config.DESKTOP, config.DOWNLOADS, config.DOCUMENTS, rem_dl]
    elif "download" in folder_hint:
        search_roots = [config.DOWNLOADS, config.DESKTOP, config.DOCUMENTS, config.PICTURES, rem_dl]
    elif "document" in folder_hint:
        search_roots = [config.DOCUMENTS, config.DESKTOP, config.DOWNLOADS, config.PICTURES, rem_dl]
    else:
        search_roots = [config.DESKTOP, config.DOWNLOADS, config.DOCUMENTS, config.PICTURES, rem_dl]

    # Explicit prefix check
    lower = text.replace("\\", "/").lower()
    if lower.startswith("desktop/") or lower.startswith("desktop\\"):
        search_roots = [config.DESKTOP]
        text = text.split("/", 1)[-1] if "/" in text else text.split("\\", 1)[-1]
    elif lower.startswith("downloads/") or lower.startswith("downloads\\"):
        search_roots = [config.DOWNLOADS]
        text = text.split("/", 1)[-1] if "/" in text else text.split("\\", 1)[-1]
    elif lower.startswith("documents/") or lower.startswith("documents\\"):
        search_roots = [config.DOCUMENTS]
        text = text.split("/", 1)[-1] if "/" in text else text.split("\\", 1)[-1]
    elif lower.startswith("pictures/") or lower.startswith("pictures\\"):
        search_roots = [config.PICTURES]
        text = text.split("/", 1)[-1] if "/" in text else text.split("\\", 1)[-1]

    raw_path = Path(text)
    stem = raw_path.stem.lower()
    ext = raw_path.suffix.lower()

    ext_aliases: dict[str, list[str]] = {
        ".docs": [".docx", ".doc"],
        ".doc": [".docx"],
        ".docx": [".doc"],
        ".text": [".txt"],
        ".txt": [".text"],
        ".python": [".py"],
        ".py": [".python"],
        ".jpeg": [".jpg"],
        ".jpg": [".jpeg"],
    }
    check_exts = ext_aliases.get(ext, [ext]) if ext else []

    # 1. Exact match in search roots
    for root in search_roots:
        cand = (root / raw_path).resolve()
        if cand.exists() and cand.is_file():
            return cand

    # 2. Case-insensitive & alias match in search roots
    for root in search_roots:
        if not root.exists():
            continue
        for f in root.iterdir():
            if not f.is_file():
                continue
            if f.name.lower() == raw_path.name.lower():
                return f.resolve()
            f_stem = f.stem.lower()
            f_ext = f.suffix.lower()
            if f_stem == stem and f_ext in check_exts:
                return f.resolve()

    # 3. Stem match if extension omitted or fuzzy (e.g. Ex7 -> Ex7.docx)
    for root in search_roots:
        if not root.exists():
            continue
        for f in root.iterdir():
            if not f.is_file():
                continue
            if f.stem.lower() == stem:
                return f.resolve()
            if f.stem.lower().startswith(stem) and not ext:
                return f.resolve()

    # Fallback to default destination
    dest_dir = search_roots[0]
    resolved = (dest_dir / raw_path).resolve()

    # Guard against sensitive system directories
    for sys_name in ["C:/Windows", "C:/Program Files", "C:/Program Files (x86)"]:
        s_root = Path(sys_name)
        if s_root.exists():
            try:
                resolved.relative_to(s_root.resolve())
                raise SafetyError(f"Access to system path is forbidden: {resolved}")
            except ValueError:
                pass

    if not any(_is_inside(resolved, root) for root in config.ALLOWED_ROOTS):
        raise SafetyError(
            f"Path is not allowed: {resolved}. Files must be under Desktop, Downloads, Documents, or user workspace."
        )
    return resolved



def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}
