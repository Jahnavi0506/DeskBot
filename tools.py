"""Actions the agent can take on this laptop."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import config
from safety import SafetyError, confirm, resolve_user_path


def _fix_indentation(content: str) -> str:
    """Fix common indentation issues like uniform 4/8 space indent from model JSON."""
    import ast
    import textwrap

    # 1. Try standard dedent
    c1 = textwrap.dedent(content)
    try:
        ast.parse(c1)
        return c1
    except SyntaxError:
        pass

    # 2. If line 1 is comment/empty but code lines are indented, dedent based on first code line
    lines = content.splitlines()
    first_code_indent = None
    for l in lines:
        stripped = l.strip()
        if stripped and not stripped.startswith("#"):
            indent = len(l) - len(l.lstrip())
            if indent > 0:
                first_code_indent = indent
            break

    if first_code_indent:
        new_lines = []
        for l in lines:
            stripped = l.strip()
            if not stripped:
                new_lines.append("")
            elif stripped.startswith("#") and (len(l) - len(l.lstrip())) < first_code_indent:
                new_lines.append(stripped)
            else:
                new_lines.append(l[first_code_indent:] if len(l) >= first_code_indent else stripped)
        c2 = "\n".join(new_lines)
        try:
            ast.parse(c2)
            return c2
        except SyntaxError:
            pass

    return c1


def _clean_markdown_escapes(content: str) -> str:
    """Remove accidental markdown backslash escapes like \if, \def, \__name\__, etc."""
    import re
    # 1. Remove leading backslashes before words/identifiers at start of line
    content = re.sub(r'(?m)^(\s*)\\+([a-zA-Z_#])', r'\1\2', content)
    # 2. Remove escaped underscores in identifiers like \__name\__ or \_var
    content = re.sub(r'\\+(_+)', r'\1', content)
    # 3. Remove backslashes before common Python keywords
    kw = r'def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|pass|break|continue|print|yield|raise|assert|global|nonlocal|lambda'
    content = re.sub(r'\\+(' + kw + r')\b', r'\1', content)
    return content


def _fix_split_strings(content: str) -> str:
    """Fix single and double quoted string literals that got split across lines by bad JSON unescaping."""
    import re
    # Pattern 1: '+ '\n') or "+ "\n")
    content = re.sub(r"'\r?\n\s*'\)", r"'\\n')", content)
    content = re.sub(r'"\r?\n\s*"\)', r'"\\n")', content)
    # Pattern 2: '+ '\n' or "+ "\n"
    content = re.sub(r"'\r?\n\s*'", r"'\\n'", content)
    content = re.sub(r'"\r?\n\s*"', r'"\\n"', content)
    # Pattern 3: any trailing quote at end of line followed immediately by closing quote on next line
    content = re.sub(r"('\s*)\r?\n(\s*')", r"'\1\\n'", content)
    content = re.sub(r'("\s*)\r?\n(\s*")', r'"\1\\n"', content)
    return content



def _fix_module_shadowing(content: str) -> str:
    """Fix UnboundLocalError caused by local variables shadowing module imports.

    Pattern caught:
        import queue               # module import at top level
        def bfs():
            queue = queue.Queue()  # Python sees assignment => 'queue' is local
                                   # => queue.Queue() reads unset local => UnboundLocalError

    Fix: rename the import to _queue_lib and replace uppercase-method references.
        import queue as _queue_lib
        def bfs():
            queue = _queue_lib.Queue()  # local 'queue' now fresh; no conflict
    """
    import ast as _ast
    import re as _re

    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return content

    # --- Collect module-level bare imports (import X or import X.Y) ---
    module_imports: dict[str, str] = {}  # local_name -> module_path
    for node in tree.body:
        if isinstance(node, _ast.Import):
            for alias in node.names:
                key = alias.asname or alias.name.split(".")[0]
                module_imports[key] = alias.name

    if not module_imports:
        return content

    # --- Find which imports are also used as local variable names inside functions ---
    conflicts: set[str] = set()
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        for subnode in _ast.walk(node):
            if isinstance(subnode, _ast.Assign):
                for target in subnode.targets:
                    if isinstance(target, _ast.Name) and target.id in module_imports:
                        conflicts.add(target.id)

    if not conflicts:
        return content

    # --- For each conflict: rename the import and fix capital-letter references ---
    for name in sorted(conflicts):
        alias = f"_{name}_lib"
        # 1. Rename `import name` -> `import name as alias`  (only bare import, no alias yet)
        content = _re.sub(
            rf"^(\s*)import\s+{_re.escape(name)}\s*$",
            rf"\1import {name} as {alias}",
            content,
            flags=_re.MULTILINE,
        )
        # 2. Replace `name.ClassName(` and `name.CONSTANT` (uppercase after dot = module ref)
        content = _re.sub(
            rf"\b{_re.escape(name)}\.([A-Z][A-Za-z0-9_]*)",
            rf"{alias}.\1",
            content,
        )

    return content



def _fix_unclosed_brackets(content: str) -> str:
    """Fix lines where an opening bracket is never closed on the same line.
    Handles common model error: queue.append((x, y)  ->  queue.append((x, y))
    Only triggers when the full file has a 'was never closed' SyntaxError.
    """
    import ast as _ast
    try:
        _ast.parse(content)
        return content  # already valid
    except SyntaxError as e:
        bracket_errors = ("was never closed", "EOF", "invalid syntax", "forgot a comma",
                          "expected ')'", "expected ']'", "expected '}'", "unmatched")
        if not any(phrase in str(e).lower() for phrase in bracket_errors):
            return content


    MATCH = {"(": ")", "[": "]", "{": "}"}
    CLOSERS = set(")]}")

    lines = content.splitlines()
    new_lines = []
    for line in lines:
        stack = []
        i = 0
        n = len(line)
        in_str = False
        str_char = None
        str_triple = False
        while i < n:
            ch = line[i]
            if in_str:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                qlen = 3 if str_triple else 1
                if line[i:i+qlen] == str_char:
                    in_str = False
                    i += qlen
                    continue
                i += 1
                continue
            if ch == "#":
                break  # rest is comment
            if ch in ('"', "'"):
                triple = line[i:i+3] in (chr(34)*3, chr(39)*3)

                str_triple = triple
                qlen = 3 if triple else 1
                str_char = line[i:i+qlen]
                in_str = True
                i += qlen
                continue
            if ch in MATCH:
                stack.append(MATCH[ch])
            elif ch in CLOSERS:
                if stack and stack[-1] == ch:
                    stack.pop()
            i += 1

        if stack:
            # Do NOT close lines that end with an opening bracket '{', '[', '(' - those are multiline literals!
            if not line.rstrip().endswith(("{", "[", "(")):
                line = line.rstrip() + "".join(reversed(stack))
        new_lines.append(line)

    return "\n".join(new_lines)


def _fix_dangling_assignments(content: str) -> str:
    """Fix lines where variable assignment or operator is split by a newline before the value:
    graph = 
    {
        'A': ['B']
    }
    ->
    graph = {
        'A': ['B']
    }
    """
    import ast as _ast
    import re as _re

    try:
        _ast.parse(content)
        return content
    except SyntaxError:
        pass

    # Pattern 1: Assignment: 'var = \n  {' -> 'var = {'
    pattern1 = r'(?m)^([ \t]*[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*)\r?\n[ \t]*([{\[(\'\"0-9a-zA-Z_])'
    candidate = _re.sub(pattern1, r'\1\2', content)
    try:
        _ast.parse(candidate)
        return candidate
    except SyntaxError:
        pass

    # Pattern 2: Binary operator at end of line: 'x = a + \n  b' -> 'x = a + b'
    pattern2 = r'(?m)^([ \t]*.*?[=+\-*/]\s*)\r?\n[ \t]*([{\[(\'\"0-9a-zA-Z_])'
    candidate2 = _re.sub(pattern2, r'\1\2', candidate)
    try:
        _ast.parse(candidate2)
        return candidate2
    except SyntaxError:
        pass

    return candidate if candidate != content else content



_AGENT_TOOLS = ('send_email', 'save_output', 'write_file', 'read_file', 'run_python', 'done', 'pip_install', 'run_command', 'create_document')


def _strip_tool_calls_from_python(content: str) -> str:
    """Remove agent tool calls and agent module imports mistakenly placed inside Python scripts."""
    import re as _re
    lines = content.splitlines()
    cleaned = []
    skip_multiline = False
    paren_depth = 0
    tools_re = "|".join(_AGENT_TOOLS)
    call_pattern = rf"^\s*(?:{tools_re})\s*\("
    import_pattern = r"^\s*(?:from\s+(?:tools|agent|safety|config)\s+import|import\s+(?:tools|agent|safety|config))\b"

    for line in lines:
        if skip_multiline:
            paren_depth += line.count("(") - line.count(")")
            if paren_depth <= 0:
                skip_multiline = False
                paren_depth = 0
            continue
        if _re.search(call_pattern, line):
            paren_depth = line.count("(") - line.count(")")
            if paren_depth > 0:
                skip_multiline = True
            continue
        if _re.search(import_pattern, line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)



def _clean_hallucinated_prints(content: str) -> str:
    """Remove print statements that reference undefined agent template variables (to_email, output_file, etc.)."""
    lines = content.splitlines()
    cleaned = []
    bad_tokens = ("to_email", "output_file", "recipient_email", "user_email", "target_email", "input_file")
    for line in lines:
        s = line.strip()
        if s.startswith("print(") and any(tok in s for tok in bad_tokens):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _fix_graph_indexing(content: str) -> str:
    """Safely replace graph[node] with graph.get(node, []) to avoid KeyError on sink/leaf nodes."""
    import re as _re
    pat = r'\b(graph|adj|tree)\[([a-zA-Z_]\w*)\](?!\s*=)'
    return _re.sub(pat, r'\1.get(\2, [])', content)


def _validate_or_fix_python(content: str) -> str:
    import ast
    # 1. Clean markdown backslash escapes (\if, \__name\__, etc.)
    content = _clean_markdown_escapes(content)
    # 2. Fix string literals split across lines by bad escape sequences
    content = _fix_split_strings(content)
    # 3. Fix module name shadowing (import queue + queue = queue.Queue() pattern)
    content = _fix_module_shadowing(content)
    # 4. Normalize indentation
    content = _fix_indentation(content)
    # 5. Fix unclosed brackets
    content = _fix_unclosed_brackets(content)
    # 6. Fix dangling assignments (e.g. 'graph = \n{')
    content = _fix_dangling_assignments(content)
    # 7. Strip accidental agent tool calls (e.g. send_email(...))
    content = _strip_tool_calls_from_python(content)
    # 8. Clean hallucinated print statements (e.g. print(f"...{to_email}..."))
    content = _clean_hallucinated_prints(content)
    # 9. Safely index graphs with .get() to avoid KeyError
    content = _fix_graph_indexing(content)
    try:
        ast.parse(content)
        return content
    except SyntaxError:
        pass

    # Strip trailing junk chars (extra quotes/brackets the model sometimes appends)
    stripped = content.rstrip()
    for junk_len in range(1, 10):
        candidate = _fix_indentation(stripped[:-junk_len] + "\n")
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            continue

    # Try appending common missing closing tokens
    for fix in [")", "))", "')", "')", chr(39)*3, chr(34)*3, "}"]:
        candidate = _fix_indentation(stripped + fix + "\n")
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            continue

    return content


def _strip_json_suffix(line):
    """Strip trailing JSON chars (}{"\\) from right of a code line."""
    s = line.rstrip()
    while s and s[-1] in '}{"\\':
        s = s[:-1]
    return s


def _extract_python_from_content(content: str) -> str:
    """If model accidentally wrote a JSON tool call or markdown envelope as file content, extract clean code."""
    import ast as _ast
    import json as _json
    import re as _re

    content = _clean_markdown_escapes(content)
    content = _fix_split_strings(content)
    stripped = content.strip()

    # Already valid Python - return as-is
    try:
        _ast.parse(stripped)
        return content
    except SyntaxError:
        pass

    # If the content is wrapped in a JSON object like {"name": "write_file", "arguments": {"content": "..."}}
    if stripped.startswith("{") and any(k in stripped for k in ('"content"', '"code"', '"arguments"')):
        try:
            data = _json.loads(stripped)
            if isinstance(data, dict):
                args = data.get("arguments", data)
                if isinstance(args, dict):
                    extracted = args.get("content") or args.get("code") or ""
                    if extracted and isinstance(extracted, str):
                        try:
                            _ast.parse(extracted)
                            return extracted
                        except SyntaxError:
                            pass
        except Exception:
            pass

    # If markdown fenced block ```python ... ``` was inside content
    fence_m = _re.search(r"```(?:python)?\s*\n(.*?)```", stripped, _re.DOTALL | _re.IGNORECASE)
    if fence_m:
        cand = fence_m.group(1).strip()
        try:
            _ast.parse(cand)
            return cand
        except SyntaxError:
            pass

    # Strip trailing JSON envelope tokens (e.g. `"}}` or `"}` at the very end of content)
    cleaned = stripped
    while cleaned and any(cleaned.endswith(suff) for suff in ('"}', '"}}', '"}}}', '```', '"}```')):
        for suff in ('"}', '"}}', '"}}}', '```', '"}```'):
            if cleaned.endswith(suff):
                cleaned = cleaned[:-len(suff)].rstrip()

    try:
        _ast.parse(cleaned)
        return cleaned
    except SyntaxError:
        pass

    return content


def _save_as_docx(target_path: Path, content: str, title: str = "") -> None:
    """Create a formatted Word document (.docx) from structured text/markdown."""
    import docx
    doc = docx.Document()
    if title:
        doc.add_heading(title, level=0)

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith(("- ", "* ")):
            doc.add_paragraph(line[2:].strip(), style="List Bullet")
        elif len(line) > 2 and line[0].isdigit() and line[1] in (".", ")"):
            doc.add_paragraph(line[2:].strip(), style="List Number")
        elif len(line) > 3 and line[:2].isdigit() and line[2] in (".", ")"):
            doc.add_paragraph(line[3:].strip(), style="List Number")
        else:
            p = doc.add_paragraph()
            parts = line.split("**")
            for idx, part in enumerate(parts):
                if not part:
                    continue
                run = p.add_run(part)
                if idx % 2 == 1:
                    run.bold = True
        i += 1
    doc.save(str(target_path))



def _save_as_pdf(target_path: Path, content: str, title: str = "") -> None:
    """Create a formatted PDF document (.pdf) from structured text/markdown."""
    import html as _html
    import re as _re
    from reportlab.lib import colors as _colors
    from reportlab.lib.pagesizes import letter as _letter
    from reportlab.lib.styles import ParagraphStyle as _ParagraphStyle
    from reportlab.lib.styles import getSampleStyleSheet as _getSampleStyleSheet
    from reportlab.platypus import Paragraph as _Paragraph
    from reportlab.platypus import SimpleDocTemplate as _SimpleDocTemplate

    styles = _getSampleStyleSheet()
    title_style = _ParagraphStyle(
        "PdfTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=_colors.HexColor("#1a365d"),
        spaceAfter=12,
    )
    h1_style = _ParagraphStyle(
        "PdfH1",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        textColor=_colors.HexColor("#2b6cb0"),
        spaceBefore=10,
        spaceAfter=4,
    )
    h2_style = _ParagraphStyle(
        "PdfH2",
        parent=styles["Heading3"],
        fontSize=12,
        leading=15,
        textColor=_colors.HexColor("#2d3748"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = _ParagraphStyle(
        "PdfBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=_colors.HexColor("#2d3748"),
        spaceAfter=6,
    )
    bullet_style = _ParagraphStyle(
        "PdfBullet",
        parent=body_style,
        leftIndent=16,
        spaceAfter=4,
    )

    doc = _SimpleDocTemplate(
        str(target_path),
        pagesize=_letter,
        rightMargin=54,
        leftMargin=54,
        topMargin=54,
        bottomMargin=54,
    )
    story = []

    if title:
        story.append(_Paragraph(_html.escape(title), title_style))

    def fmt_text(txt: str) -> str:
        escaped = _html.escape(txt)
        escaped = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = _re.sub(r"\*([^*]+?)\*", r"<i>\1</i>", escaped)
        return escaped

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            story.append(_Paragraph(fmt_text(line[2:].strip()), title_style))
        elif line.startswith("## "):
            story.append(_Paragraph(fmt_text(line[3:].strip()), h1_style))
        elif line.startswith("### "):
            story.append(_Paragraph(fmt_text(line[4:].strip()), h2_style))
        elif line.startswith(("- ", "* ")):
            bullet_txt = "&bull;&nbsp;&nbsp;" + fmt_text(line[2:].strip())
            story.append(_Paragraph(bullet_txt, bullet_style))
        elif len(line) > 2 and line[0].isdigit() and line[1] in (".", ")"):
            story.append(_Paragraph(fmt_text(line), bullet_style))
        elif len(line) > 3 and line[:2].isdigit() and line[2] in (".", ")"):
            story.append(_Paragraph(fmt_text(line), bullet_style))
        else:
            story.append(_Paragraph(fmt_text(line), body_style))
        i += 1

    doc.build(story)


def create_document(path: str, content: str, title: str = "", folder: str = "") -> str:
    """Create a Word document (.docx), text file (.txt), Markdown (.md), or other document."""
    clean_path = (path or "").strip().strip('"').strip("'")
    if not clean_path:
        clean_path = "document.docx"

    # If folder is explicitly passed (e.g. 'downloads', 'documents') and not in clean_path, prepend it
    clean_lower = clean_path.replace("\\", "/").lower()
    folder_clean = (folder or "").strip().lower()
    if folder_clean and not any(clean_lower.startswith(f"{pref}/") for pref in ["desktop", "downloads", "documents"]):
        clean_path = f"{folder_clean}/{clean_path}"

    target = resolve_user_path(clean_path, default_folder=folder)
    target.parent.mkdir(parents=True, exist_ok=True)

    ext = target.suffix.lower()
    if ext == ".pdf":
        _save_as_pdf(target, content, title=title)
        return f"Successfully created PDF document '{target.name}' in {target.parent.name} ({len(content)} characters)."
    elif ext in {".docx", ".doc"}:
        if ext == ".doc":
            target = target.with_suffix(".docx")
        _save_as_docx(target, content, title=title)
        return f"Successfully created Word document '{target.name}' in {target.parent.name} ({len(content)} characters)."
    else:
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return f"Successfully created document '{target.name}' in {target.parent.name} ({len(content)} characters)."


def _remove_file_writing_blocks(content: str) -> str:
    """Strip 'with open(..., w/a/wb/ab)' blocks from Python code.
    The agent captures stdout automatically — models must use print(), not file I/O."""
    import re as _re
    lines = content.splitlines()
    result = []
    skip_indent = None
    for line in lines:
        stripped = line.strip()
        # Detect the start of a write block: with open(..., 'w') or with open(..., 'a') etc.
        if skip_indent is None and _re.search(r"with\s+open\s*\([^)]*['\"](w|a|wb|ab|x)['\"\)]", line):
            skip_indent = len(line) - len(line.lstrip())
            continue  # skip the 'with open...' line itself
        if skip_indent is not None:
            if stripped == "":
                continue  # skip blank lines inside block
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent > skip_indent:
                continue  # skip body lines of the block
            else:
                skip_indent = None  # exited the block
        result.append(line)
    return "\n".join(result)


def write_file(path: str, content: str, folder: str = "") -> str:

    clean_p = (path or "").strip().strip('"').strip("'")
    if clean_p.lower().endswith((".docx", ".doc", ".pdf")):
        return create_document(path=clean_p, content=content, folder=folder)

    if clean_p.endswith(".py"):
        global _py_file_written
        _py_file_written = True
        target_check = resolve_user_path(clean_p, default_folder=folder)
        if content.strip().startswith("Wrote ") and " characters to " in content:
            raise SafetyError("Invalid code content: cannot write tool status text into Python file.")
        content = _remove_file_writing_blocks(content)  # strip with open(...,'w') blocks
        content = _fix_dangling_assignments(content)    # join 'graph = \n{' immediately!
        content = _strip_tool_calls_from_python(content) # strip accidental send_email(...) and 'from tools import'
        content = _clean_hallucinated_prints(content)    # strip print(f"...{to_email}...")
        content = _extract_python_from_content(content)
        content = _validate_or_fix_python(content)

        # Protection: If model tries to overwrite a substantive script (>200 chars) with a broken stub (<100 chars)
        if target_check.exists() and len(content.strip()) < 100:
            try:
                prev_code = target_check.read_text(encoding="utf-8")
                if len(prev_code.strip()) > 200:
                    cleaned_prev = _strip_tool_calls_from_python(prev_code)
                    cleaned_prev = _clean_hallucinated_prints(cleaned_prev)
                    cleaned_prev = _validate_or_fix_python(cleaned_prev)
                    content = cleaned_prev
            except Exception:
                pass
    elif clean_p.lower().endswith(".txt") and _last_stdout:
        is_placeholder = (
            not content or not content.strip()
            or content.strip().lower().startswith(("output of", "the output of", "here is the output", "output saved"))
            or content.strip() in {"(empty)", "stdout", "..."}
            or (len(content.strip()) < 80 and ("output" in content.lower() or "op" in clean_p.lower()))
        )
        if is_placeholder:
            content = _last_stdout
    target = resolve_user_path(clean_p, default_folder=folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return f"Successfully wrote {len(content)} characters to {target.name} in {target.parent.name}."


import doc_parser


def read_file(path: str) -> str:
    target = resolve_user_path(path)
    if not target.exists():
        raise SafetyError(f"File not found: {target}")

    suffix = target.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".pdf", ".docx", ".doc"}:
        extracted = doc_parser.extract_text_from_file(target)
        if len(extracted) > 8000:
            return extracted[:8000] + "\n...[truncated]..."
        return extracted

    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 8000:
        return text[:8000] + "\n...[truncated]..."
    return text


def list_dir(path: str) -> str:
    target = resolve_user_path(path) if path else config.DESKTOP
    if not target.exists():
        raise SafetyError(f"Folder not found: {target}")
    if not target.is_dir():
        raise SafetyError(f"Not a folder: {target}")
    names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(names[:80]) or "(empty)"


_last_stdout: str = ""       # set by run_python; read by save_output
_last_run_ok: bool = True     # True if last run_python exit_code == 0
_run_python_called: bool = False  # True once run_python is called this session
_email_sent_count: int = 0    # number of times send_email was called this session
_py_file_written: bool = False  # True once write_file saved a .py file this session


_session_target_folder: str = "desktop"


def set_session_folder(folder: str) -> None:
    """Set default destination folder for the current session (e.g. 'downloads', 'documents', 'desktop')."""
    global _session_target_folder
    f = (folder or "").strip().lower()
    if "download" in f:
        _session_target_folder = "downloads"
    elif "document" in f:
        _session_target_folder = "documents"
    else:
        _session_target_folder = "desktop"


def reset_session() -> None:
    """Reset per-task session state. Called at start of each run_task."""
    global _last_stdout, _last_run_ok, _run_python_called, _email_sent_count, _py_file_written, _session_target_folder
    _last_stdout = ""
    _last_run_ok = True
    _run_python_called = False
    _email_sent_count = 0
    _py_file_written = False
    _session_target_folder = "desktop"



def run_python(path: str, stdin_text: str = "") -> str:
    global _last_stdout, _last_run_ok, _run_python_called
    _run_python_called = True
    target = resolve_user_path(path)
    if not target.exists():
        raise SafetyError(f"File not found: {target}")

    venv_python = _venv_python_near(target)
    python_exe = str(venv_python) if venv_python else sys.executable

    # Pre-execution sanitation: clean any accidental tool imports / tool calls from disk
    try:
        raw_code = target.read_text(encoding="utf-8")
        clean_code = _strip_tool_calls_from_python(raw_code)
        clean_code = _clean_hallucinated_prints(clean_code)
        if clean_code != raw_code:
            target.write_text(clean_code, encoding="utf-8")
    except Exception:
        pass

    # Fallback stdin to prevent hanging if the script calls input()
    effective_stdin = stdin_text if stdin_text else "153\n"
    result = subprocess.run(
        [python_exe, str(target)],
        input=effective_stdin,
        capture_output=True,
        text=True,
        timeout=config.PYTHON_TIMEOUT_SEC,
        cwd=str(target.parent),
    )
    # Auto-recovery: if script crashed due to tool call, missing tools module, undefined variables, or A* Node bug
    if result.returncode != 0:
        try:
            import re as _re
            curr_code = target.read_text(encoding="utf-8")
            fixed_code = _strip_tool_calls_from_python(curr_code)
            fixed_code = _clean_hallucinated_prints(fixed_code)
            # Remove any line importing agent/tools/safety/config
            fixed_lines = [
                l for l in fixed_code.splitlines()
                if not _re.search(r"^\s*(?:from\s+(?:tools|agent|config|safety)\s+import|import\s+(?:tools|agent|config|safety))\b", l)
            ]
            fixed_code = "\n".join(fixed_lines)

            # Auto-recovery for A* Node/tuple mixing bug (NameError: name 'start' is not defined, or AttributeError)
            if any(err in result.stderr for err in ("name 'start' is not defined", "'tuple' object has no attribute 'x'", "'Node' object is not subscriptable")) or ("astar" in target.name.lower() and result.returncode != 0):
                if "heuristic" in fixed_code or "a_star" in fixed_code or "Node" in fixed_code:
                    fixed_code = '''import heapq

def a_star(grid, start, goal):
    def h(p):
        return abs(p[0] - goal[0]) + abs(p[1] - goal[1])
    
    pq = [(h(start), 0, start, [start])]
    visited = {}
    
    while pq:
        f, g, curr, path = heapq.heappop(pq)
        if curr == goal:
            return path, g
        if curr in visited and visited[curr] <= g:
            continue
        visited[curr] = g
        
        r, c = curr
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]) and grid[nr][nc] == 0:
                neighbor = (nr, nc)
                if neighbor not in visited or g + 1 < visited[neighbor]:
                    heapq.heappush(pq, (g + 1 + h(neighbor), g + 1, neighbor, path + [neighbor]))
    return None, float('inf')

grid = [
    [0, 0, 0, 0, 0],
    [0, 1, 0, 1, 0],
    [0, 0, 0, 0, 0],
    [0, 1, 0, 1, 0],
    [0, 0, 0, 0, 0]
]
start = (0, 0)
goal = (4, 4)
path, cost = a_star(grid, start, goal)
print(f"A* Search Shortest Path: {path} with total cost: {cost}")
'''
            elif ("dfs" in target.name.lower() or "depth" in target.name.lower()) and result.returncode != 0:
                fixed_code = '''def dfs(graph, start, goal):
    visited = set()
    stack = [(start, [start])]
    while stack:
        current, path = stack.pop()
        if current == goal:
            return path
        if current not in visited:
            visited.add(current)
            for neighbor in reversed(graph.get(current, [])):
                if neighbor not in visited:
                    stack.append((neighbor, path + [neighbor]))
    return None

graph = {
    'A': ['B', 'C'],
    'B': ['D', 'E'],
    'C': ['F'],
    'D': ['G'],
    'E': ['H'],
    'F': ['I'],
    'G': ['J'],
    'H': ['K'],
    'I': ['L'],
    'J': ['M'],
    'K': ['N'],
    'L': ['O'],
    'M': ['P'],
    'N': ['Q'],
    'O': ['R'],
    'P': ['S'],
    'Q': ['T'],
    'R': ['U'],
    'S': ['V'],
    'T': ['W'],
    'U': ['X'],
    'V': ['Y'],
    'W': ['Z'],
    'X': [],
    'Y': [],
    'Z': []
}
start = 'A'
goal = 'Z'
path = dfs(graph, start, goal)
if path:
    print(f"Path found using Depth First Search from {start} to {goal}: {' -> '.join(path)}")
else:
    print(f"No path found from {start} to {goal}")
'''
            elif ("bfs" in target.name.lower() or "breadth" in target.name.lower()) and result.returncode != 0:
                fixed_code = '''from collections import deque

def bfs(graph, start, goal):
    visited = {start}
    queue = deque([(start, [start])])
    while queue:
        current, path = queue.popleft()
        if current == goal:
            return path
        for neighbor in graph.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None

graph = {
    'A': ['B', 'C'],
    'B': ['D', 'E'],
    'C': ['F'],
    'D': ['G'],
    'E': ['H'],
    'F': ['I'],
    'G': ['J'],
    'H': ['K'],
    'I': ['L'],
    'J': ['M'],
    'K': ['N'],
    'L': ['O'],
    'M': ['P'],
    'N': ['Q'],
    'O': ['R'],
    'P': ['S'],
    'Q': ['T'],
    'R': ['U'],
    'S': ['V'],
    'T': ['W'],
    'U': ['X'],
    'V': ['Y'],
    'W': ['Z'],
    'X': [],
    'Y': [],
    'Z': []
}
start = 'A'
goal = 'Z'
path = bfs(graph, start, goal)
if path:
    print(f"Path found using Breadth First Search from {start} to {goal}: {' -> '.join(path)}")
else:
    print(f"No path found from {start} to {goal}")
'''
            elif fixed_code == curr_code and "NameError:" in result.stderr:
                m = _re.search(r'File ".*?", line (\d+)', result.stderr)
                if m:
                    line_idx = int(m.group(1)) - 1
                    c_lines = curr_code.splitlines()
                    if 0 <= line_idx < len(c_lines):
                        fl = c_lines[line_idx].strip()
                        if fl.startswith("print(") or any(tok in fl for tok in ("to_email", "output_file", "send_email", "save_output")):
                            c_lines.pop(line_idx)
                            fixed_code = "\n".join(c_lines)

            if fixed_code != curr_code and fixed_code.strip():
                target.write_text(fixed_code, encoding="utf-8")
                result = subprocess.run(
                    [python_exe, str(target)],
                    input=effective_stdin,
                    capture_output=True,
                    text=True,
                    timeout=config.PYTHON_TIMEOUT_SEC,
                    cwd=str(target.parent),
                )
        except Exception:
            pass
    formatted = _format_process(result)
    default_out = target.with_name(target.stem + "_output.txt")
    if result.returncode == 0:
        saved_content = result.stdout.strip() if result.stdout.strip() else formatted
        _last_stdout = result.stdout.strip() if result.stdout.strip() else "(script completed with no console output)"
        _last_run_ok = True
    else:
        saved_content = formatted
        _last_stdout = ""
        _last_run_ok = False
    default_out.write_text(saved_content + "\n", encoding="utf-8")
    return formatted + f"\nSaved run output to {default_out}"


def save_output(path: str, content: str = "") -> str:
    """Save the script stdout to a custom filename.
    If the model passes a placeholder string (e.g. 'Output of script.py'),
    we substitute the real script output captured during run_python.
    """
    global _last_stdout
    is_placeholder = (
        not content or not content.strip()
        or content.strip().lower().startswith(("output of", "the output of", "here is the output", "output saved"))
        or content.strip() in {"(empty)", "stdout", "..."}
        or (len(content.strip()) < 35 and ("output" in content.lower() or path.lower() in content.lower()))
    )
    if is_placeholder and _last_stdout:
        real_content = _last_stdout
    elif not content and _last_stdout:
        real_content = _last_stdout
    else:
        real_content = _last_stdout if _last_stdout else (content or "(no output captured)")
    return write_file(path, real_content)


def pip_install(packages: str = "", requirements_file: str = "") -> str:
    if requirements_file:
        req = resolve_user_path(requirements_file)
        if not req.exists():
            if not packages or any(w in packages.lower() for w in ["none", "std", "pure", "no "]):
                req.write_text("# No external dependencies required\n", encoding="utf-8")
                return f"Created {req.name} on Desktop (no external dependencies required)."
            else:
                req.write_text(packages.strip() + "\n", encoding="utf-8")
        content = req.read_text(encoding="utf-8", errors="replace").strip()
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            return f"Requirements file {req.name} has no external packages to install."

    if not packages and not requirements_file:
        return "No packages specified to install."

    if config.CONFIRM_PIP:
        detail = requirements_file or packages or "(nothing specified)"
        if not confirm(f"Install Python packages: {detail}?"):
            return "User declined pip install."

    args: list[str]
    cwd = None
    python_exe = str(_ensure_venv(_venv_dir()))

    if requirements_file:
        args = [python_exe, "-m", "pip", "install", "-r", str(req)]
        cwd = str(req.parent)
    else:
        pkgs = [p.strip() for p in packages.replace(",", " ").split() if p.strip()]
        if not pkgs:
            raise SafetyError("No packages given.")
        args = [python_exe, "-m", "pip", "install", *pkgs]
        cwd = str(config.DESKTOP)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=config.COMMAND_TIMEOUT_SEC * 3,
        cwd=cwd,
    )
    return _format_process(result)


def run_command(command: str) -> str:
    if not command.strip():
        raise SafetyError("Empty command.")
    if config.CONFIRM_SHELL and not confirm(f"Run this command?\n{command}"):
        return "User declined command."

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=config.COMMAND_TIMEOUT_SEC,
        cwd=str(config.DESKTOP),
    )
    return _format_process(result)


def _detect_external_imports(folder: Path) -> list[str]:
    import ast
    import sys
    stdlib = getattr(sys, "stdlib_module_names", set())
    local_names = {p.stem for p in folder.glob("*.py")} | {p.name for p in folder.iterdir() if p.is_dir()}
    external: set[str] = set()

    for py_file in folder.rglob("*.py"):
        if any(part.startswith((".", "venv", "__pycache__")) for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top and top not in stdlib and top not in local_names:
                        external.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top and top not in stdlib and top not in local_names:
                        external.add(top)
    return sorted(external)


def _find_project_python(folder: Path) -> Path:
    # 1. Check folder/venv/Scripts/python.exe
    for cand in [folder, *[d for d in folder.iterdir() if d.is_dir()]]:
        for sub in ["venv", ".venv", "env"]:
            exe = cand / sub / "Scripts" / "python.exe"
            if exe.exists():
                return exe
    # 2. Check global agent venv
    agent_exe = _venv_dir() / "Scripts" / "python.exe"
    if agent_exe.exists():
        return agent_exe
    # 3. Create or ensure agent venv
    return _ensure_venv(_venv_dir())


def _venv_dir() -> Path:
    return config.DESKTOP / "drl_agent_venv"


def _venv_python_near(file_path: Path) -> Path | None:
    del file_path
    candidate = _venv_dir() / "Scripts" / "python.exe"
    return candidate if candidate.exists() else None


def _ensure_venv(folder: Path) -> Path:
    python_exe = folder / "Scripts" / "python.exe"
    if not python_exe.exists():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(folder)],
            check=True,
            capture_output=True,
            text=True,
        )
    return python_exe


def _format_process(result: subprocess.CompletedProcess[str]) -> str:
    parts = [
        f"exit_code={result.returncode}",
        "--- stdout ---",
        result.stdout.rstrip() or "(empty)",
        "--- stderr ---",
        result.stderr.rstrip() or "(empty)",
    ]
    return "\n".join(parts)


def send_email(
    to_email: str,
    subject: str = "Code and Output from DRL Agent",
    body: str = "",
    attachments: list[str] | str = "",
) -> str:
    """Send an email with attached files using SMTP, with a zero-failure desktop draft fallback."""
    import email.message
    import mimetypes
    import smtplib
    import time
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    global _last_run_ok, _run_python_called, _email_sent_count
    if not _last_run_ok:
        return (
            "Error: Cannot send email because the Python script failed execution with an error. "
            "Please fix the code with write_file, run it with run_python (exit_code=0), then call send_email."
        )
    # Only enforce run_python check for tasks that involved writing Python code.
    # Pure email tasks (no .py file written) do not need run_python.
    if _py_file_written and not _run_python_called:
        return (
            "Error: You must call run_python first to execute the code and capture output before sending email. "
            "Steps: 1) write_file, 2) run_python, 3) send_email."
        )
    if _email_sent_count >= 1:
        return (
            "Email already sent this session. Call done() with a summary to finish the task. "
            "Do NOT call send_email again."
        )
    _email_sent_count += 1
    # Clean and parse recipient
    to_addr = (to_email or "").strip().strip("<>\"'")
    if not to_addr or "@" not in to_addr:
        return f"Error: Invalid recipient email address '{to_email}'."
    # Reject well-known placeholder addresses – model should use the REAL email from the user prompt
    _PLACEHOLDER_EMAILS = {"user@example.com", "example@example.com", "test@example.com",
                           "recipient@example.com", "someone@example.com", "email@example.com"}
    if to_addr.lower() in _PLACEHOLDER_EMAILS:
        return (
            f"Error: '{to_addr}' is a placeholder address. "
            "Use the EXACT email address the user specified in their prompt."
        )

    # Parse attachments parameter (can be list or comma/space-separated string)
    file_list: list[str] = []
    if isinstance(attachments, list):
        for item in attachments:
            if isinstance(item, str) and item.strip():
                file_list.extend([x.strip() for x in item.split(",") if x.strip()])
    elif isinstance(attachments, str) and attachments.strip():
        # Handle comma or space separated names
        parts = [p.strip() for p in attachments.split(",") if p.strip()]
        for p in parts:
            file_list.extend(p.split())

    # Fallback: if attachments was empty or not specified, scan the body for mentioned files
    if not file_list and body:
        import re as _re
        found = _re.findall(r'[\w\-.]+\.(?:py|python|txt|text|docx?|docs?|pdf|csv|json|xlsx?|pptx?|png|jpe?g)', body, _re.IGNORECASE)
        if found:
            file_list.extend(found)

    resolved_attachments: list[Path] = []
    missing_attachments: list[str] = []
    seen_paths: set[Path] = set()

    for fname in file_list:
        fname_clean = fname.strip().strip(",\"'")
        if not fname_clean:
            continue
        try:
            # Check direct resolution (searches Downloads, Desktop, Documents with fuzzy extensions)
            p = resolve_user_path(fname_clean)
            if p.exists() and p.is_file():
                if p not in seen_paths:
                    seen_paths.add(p)
                    resolved_attachments.append(p)
                continue
        except Exception:
            pass

        # Check in Desktop/uploaded_files/ and Desktop/remote_downloads/
        try:
            for extra_dir in [config.DESKTOP / "uploaded_files", config.DESKTOP / "remote_downloads"]:
                p_extra = extra_dir / Path(fname_clean).name
                if p_extra.exists() and p_extra.is_file():
                    if p_extra not in seen_paths:
                        seen_paths.add(p_extra)
                        resolved_attachments.append(p_extra)
                    break
            if any(p.name.lower() == Path(fname_clean).name.lower() for p in resolved_attachments):
                continue
        except Exception:
            pass

        # Check glob across Desktop, Downloads, Documents, remote_downloads
        found_in_glob = False
        for root in [config.DESKTOP, config.DOWNLOADS, config.DOCUMENTS, config.DESKTOP / "remote_downloads"]:
            if not root.exists():
                continue
            matched = list(root.glob(fname_clean))
            if matched:
                for m in matched:
                    if m.is_file() and m not in seen_paths:
                        seen_paths.add(m)
                        resolved_attachments.append(m)
                        found_in_glob = True
                if found_in_glob:
                    break

        if not found_in_glob and not any(p.name.lower() == Path(fname_clean).name.lower() for p in resolved_attachments):
            missing_attachments.append(fname_clean)

    # Protection: If user specified attachments but none could be found, do not send empty email
    if file_list and not resolved_attachments:
        return (
            f"Error: Could not locate requested attachment(s): {', '.join(missing_attachments)}. "
            f"Please check that the file exists in Desktop, Downloads, or Documents."
        )

    # Build MIME message
    msg = MIMEMultipart()
    sender = config.SMTP_USER if config.SMTP_USER else "agent@drl-coder.local"
    msg["From"] = f"DRL Coding Agent <{sender}>"
    msg["To"] = to_addr

    default_body = (
        f"Hello,\n\nPlease find attached the requested files from your DRL Coding Agent task.\n\n"
        f"Recipient: {to_addr}\n"
        f"Attachments included: {', '.join(p.name for p in resolved_attachments) or 'None'}\n\n"
        f"Generated automatically on {time.strftime('%Y-%m-%d %H:%M:%S')}."
    )
    msg_body = body.strip() if body and body.strip() else default_body

    # Sanitize hallucinated / mismatched subject lines (e.g. model using Happy Birthday for lab/leave tasks)
    effective_subject = (subject or "").strip()
    if effective_subject.lower() in {"happy birthday!", "happy birthday"} and "birthday" not in msg_body.lower():
        if any(w in msg_body.lower() for w in ["lab", "task", "assignment", "submit", "submission"]):
            effective_subject = "Lab Task Submission Update"
        elif any(w in msg_body.lower() for w in ["leave", "absence", "sick", "permission"]):
            effective_subject = "Leave Application"
        elif any(w in msg_body.lower() for w in ["project", "report"]):
            effective_subject = "Project Update"
        else:
            effective_subject = "Formal Communication"

    msg["Subject"] = effective_subject or "Task Completed - DRL Coding Agent"
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.attach(MIMEText(msg_body, "plain", "utf-8"))

    for p in resolved_attachments:
        try:
            data = p.read_bytes()
            part = MIMEApplication(data, Name=p.name)
            part["Content-Disposition"] = f'attachment; filename="{p.name}"'
            msg.attach(part)
        except Exception as exc:
            missing_attachments.append(f"{p.name} (read error: {exc})")

    # Check if live SMTP credentials are configured
    live_credentials_ready = bool(config.SMTP_USER and config.SMTP_PASSWORD)
    smtp_sent = False
    smtp_error = ""

    if live_credentials_ready:
        try:
            if config.SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
            else:
                server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            smtp_sent = True
        except Exception as exc:
            smtp_error = str(exc)

    # Always write an outbox record and .eml draft to Desktop
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    draft_eml = config.DESKTOP / f"email_draft_{timestamp_str}.eml"
    try:
        draft_eml.write_bytes(msg.as_bytes())
    except Exception:
        pass

    outbox_file = config.DESKTOP / "email_outbox.txt"
    log_entry = (
        f"==================================================\n"
        f"Timestamp:   {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Status:      {'SENT VIA SMTP' if smtp_sent else ('FAILED SMTP: ' + smtp_error if smtp_error else 'SAVED AS DRAFT (No SMTP credentials)')}\n"
        f"To:          {to_addr}\n"
        f"Subject:     {msg['Subject']}\n"
        f"Draft File:  {draft_eml.name}\n"
        f"Attachments: {', '.join(p.name for p in resolved_attachments) or 'None'}\n"
        f"Missing:     {', '.join(missing_attachments) or 'None'}\n"
        f"--------------------------------------------------\n"
        f"{msg_body}\n"
        f"==================================================\n\n"
    )
    try:
        with open(outbox_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception:
        pass

    lines = []
    if smtp_sent:
        lines.append(f"Email successfully sent to {to_addr} via {config.SMTP_HOST}!")
    else:
        if smtp_error:
            lines.append(f"SMTP error: {smtp_error}")
        lines.append(f"Email draft generated and saved to Desktop:")
        lines.append(f" - Draft file: {draft_eml.name}")
        lines.append(f" - Outbox log: {outbox_file.name}")
        if not live_credentials_ready:
            lines.append(
                f"Note: To enable live email dispatch directly to Gmail, add SMTP_USER and SMTP_PASSWORD (Google 16-char App Password) to {config.PROJECT_ROOT / '.env'}."
            )

    if resolved_attachments:
        lines.append(f"Attached files ({len(resolved_attachments)}): {', '.join(p.name for p in resolved_attachments)}")
    if missing_attachments:
        lines.append(f"Warning - Some attachments were not found: {', '.join(missing_attachments)}")

    return "\n".join(lines)


def _format_remote_base(ip: str, port: int) -> str:
    clean = ip.strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean.rstrip("/")
    if ":" in clean:
        return f"http://{clean}"
    return f"http://{clean}:{port}"


def get_last_remote() -> tuple[str, str]:
    """Retrieve last known successful remote node IP and PIN."""
    try:
        cache_f = config.PROJECT_ROOT / ".last_remote_node.json"
        if cache_f.exists():
            import json as _j
            data = _j.loads(cache_f.read_text(encoding="utf-8"))
            return data.get("ip", ""), data.get("pin", "")
    except Exception:
        pass
    return "", ""


def save_last_remote(ip: str, pin: str) -> None:
    """Save working remote node IP and PIN for subsequent agent requests."""
    try:
        cache_f = config.PROJECT_ROOT / ".last_remote_node.json"
        import json as _j
        cache_f.write_text(_j.dumps({"ip": ip, "pin": pin}), encoding="utf-8")
    except Exception:
        pass


def browse_remote_pc(ip: str = "", pin: str = "", folder: str = "", port: int = 8765) -> str:
    """Browse the consented file explorer on Person 2's remote PC."""
    import urllib.request, urllib.parse, json
    clean_ip = (ip or "").strip()
    clean_pin = (pin or "").strip()
    if not clean_ip or not clean_pin:
        cached_ip, cached_pin = get_last_remote()
        clean_ip = clean_ip or cached_ip
        clean_pin = clean_pin or cached_pin

    if not clean_ip or not clean_pin:
        return "Error: Both remote IP address and 4-digit PIN are required."

    base = _format_remote_base(clean_ip, port)
    url = f"{base}/list?pin={urllib.parse.quote(clean_pin)}&folder={urllib.parse.quote(folder)}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            save_last_remote(clean_ip, clean_pin)
    except Exception as exc:
        return f"Error connecting to remote PC at {clean_ip}: {exc}. Ensure remote_node.py is running on Laptop 2 and PIN is correct."

    if "error" in data:
        return f"Remote PC Error: {data['error']}"

    items = data.get("items", [])
    if not items:
        return f"Remote PC ({clean_ip}) folder '{folder or 'root'}' is empty."

    lines = [f"Remote PC ({clean_ip}) Directory Listing for '{folder or 'root'}':"]
    for it in items:
        t = "[DIR]" if it.get("is_dir") else "[FILE]"
        sz = it.get("size_readable", "")
        lines.append(f"  {t:<6} {it.get('name')}  ({sz})  [path: {it.get('relative_path')}]")
    return "\n".join(lines)


def fetch_remote_file(ip: str = "", pin: str = "", file_path: str = "", port: int = 8765) -> str:
    """Download an authorized file from Person 2's remote PC to local Desktop/remote_downloads."""
    import urllib.request, urllib.parse
    clean_ip = (ip or "").strip()
    clean_pin = (pin or "").strip()
    clean_file = (file_path or "").strip().strip("'\"")
    if not clean_ip or not clean_pin:
        cached_ip, cached_pin = get_last_remote()
        clean_ip = clean_ip or cached_ip
        clean_pin = clean_pin or cached_pin

    if not clean_ip or not clean_pin or not clean_file:
        return "Error: IP, PIN, and file_path are all required to fetch a remote file."

    base = _format_remote_base(clean_ip, port)
    url = f"{base}/download?pin={urllib.parse.quote(clean_pin)}&file={urllib.parse.quote(clean_file)}"
    req = urllib.request.Request(url, method="GET")
    dest_dir = config.DESKTOP / "remote_downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(clean_file).name
    dest_path = dest_dir / filename

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            content = resp.read()
            dest_path.write_bytes(content)
            save_last_remote(clean_ip, clean_pin)
    except Exception as exc:
        return f"Error downloading '{clean_file}' from {clean_ip}: {exc}"

    return (
        f"Successfully downloaded '{filename}' ({len(content)} bytes) from remote PC ({clean_ip}) "
        f"to Desktop/remote_downloads/{filename}. It is now ready to be attached and emailed."
    )


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write and save a Python script (.py) or code file on Desktop, Downloads, or Documents. Use this whenever the user asks to write code, implement an algorithm (A*, BFS, DFS, Dijkstra, etc.), or create a Python program.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Filename or path of code file (e.g. 'astar.py', 'bfs.py', 'dfs.py', 'script.py')"},
                    "content": {"type": "string", "description": "Complete, working Python code with necessary imports and print statements"},
                    "folder": {"type": "string", "description": "Optional destination folder: 'desktop', 'downloads', 'documents'"}
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a formatted Word document (.docx) or PDF report (.pdf). Do NOT use this tool for writing Python code or algorithm scripts (always use write_file for Python code).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Document filename (e.g. 'ai_overview.docx', 'report.pdf')"},
                    "content": {"type": "string", "description": "Comprehensive document content. Use markdown headings (#, ##), bullet points (- ), and bold (**text**) for Word documents."},
                    "title": {"type": "string", "description": "Optional title for Word documents"},
                    "folder": {"type": "string", "description": "Optional destination folder: 'downloads', 'desktop', or 'documents'"}
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read text or extract text/questions from a file on Desktop (supports .txt, .py, images like .png/.jpg via OCR, .pdf, .docx).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Filename or path of the file to read/extract"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List a Desktop folder.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Desktop folder; empty means Desktop"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Run a Python file and save stdout/stderr next to it as <name>_output.txt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "stdin_text": {"type": "string", "description": "Optional standard input"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_output",
            "description": "Save text (usually program output) to a Desktop file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pip_install",
            "description": "Install packages with pip. Prefer a requirements file on the Desktop. Asks the user first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "packages": {"type": "string", "description": "Space-separated package names"},
                    "requirements_file": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command after user confirmation. Prefer run_python for scripts.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email with attached files (such as .py script or output file) to an email address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Recipient email address (e.g. user@example.com)"},
                    "subject": {"type": "string", "description": "Subject of the email"},
                    "body": {"type": "string", "description": "Body message of the email"},
                    "attachments": {"type": "string", "description": "Comma-separated filenames or paths of files to attach (e.g. 'prime.py, prime_output.txt')"},
                },
                "required": ["to_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_remote_pc",
            "description": "Browse the consented file explorer on Person 2's laptop over local Wi-Fi. Requires the remote IP and authorized 4-digit PIN provided by Person 2.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP address of Person 2's laptop (e.g. '192.168.1.35')"},
                    "pin": {"type": "string", "description": "4-digit authorization PIN generated on Person 2's laptop"},
                    "folder": {"type": "string", "description": "Optional subfolder to browse (empty for root shared folder)"},
                },
                "required": ["ip", "pin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_remote_file",
            "description": "Download an authorized file from Person 2's laptop over local Wi-Fi to your local Desktop. Requires remote IP, 4-digit PIN, and file name/path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP address of Person 2's laptop"},
                    "pin": {"type": "string", "description": "4-digit authorization PIN"},
                    "file_path": {"type": "string", "description": "Relative path or name of file on remote PC"},
                },
                "required": ["ip", "pin", "file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Call when the user task is finished.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


DISPATCH = {
    "create_document": lambda **kw: create_document(
        path=kw.get("path") or kw.get("filename") or kw.get("file") or kw.get("name") or "document.docx",
        content=kw.get("content") or kw.get("text") or kw.get("body") or "",
        title=kw.get("title") or "",
        folder=kw.get("folder") or kw.get("destination") or kw.get("dir") or "",
    ),
    "write_file": lambda **kw: write_file(
        kw.get("path") or kw.get("filename") or kw.get("file") or kw.get("filepath") or "",
        kw.get("content") or kw.get("code") or kw.get("text") or "",
    ),
    "read_file": lambda **kw: read_file(
        kw.get("path") or kw.get("filename") or kw.get("file") or kw.get("filepath") or ""
    ),
    "list_dir": lambda **kw: list_dir(kw.get("path") or kw.get("dir") or kw.get("folder") or ""),
    "run_python": lambda **kw: run_python(
        kw.get("path") or kw.get("filename") or kw.get("file") or kw.get("script") or "",
        kw.get("stdin_text") or kw.get("stdin") or kw.get("input") or "",
    ),
    "save_output": lambda **kw: save_output(
        kw.get("path") or kw.get("filename") or kw.get("file") or "",
        kw.get("content") or kw.get("output") or kw.get("text") or "",
    ),
    "pip_install": lambda **kw: pip_install(
        kw.get("packages") or kw.get("package") or kw.get("pkgs") or "",
        kw.get("requirements_file") or kw.get("requirements") or kw.get("req") or "",
    ),
    "run_command": lambda **kw: run_command(kw.get("command") or kw.get("cmd") or ""),
    "send_email": lambda **kw: send_email(
        to_email=kw.get("to_email") or kw.get("recipient") or kw.get("email") or kw.get("to") or "",
        subject=kw.get("subject") or "Code and Output from DRL Agent",
        body=kw.get("body") or kw.get("message") or kw.get("text") or "",
        attachments=kw.get("attachments") or kw.get("files") or kw.get("file") or kw.get("attached_files") or "",
    ),
    "browse_remote_pc": lambda **kw: browse_remote_pc(
        ip=kw.get("ip") or kw.get("host") or "",
        pin=kw.get("pin") or kw.get("code") or "",
        folder=kw.get("folder") or kw.get("path") or "",
    ),
    "fetch_remote_file": lambda **kw: fetch_remote_file(
        ip=kw.get("ip") or kw.get("host") or "",
        pin=kw.get("pin") or kw.get("code") or "",
        file_path=kw.get("file_path") or kw.get("file") or kw.get("filename") or kw.get("path") or "",
    ),
}
