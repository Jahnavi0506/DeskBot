"""Local coding agent: natural-language tasks -> files, pip, run, saved output."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import config
import ollama_client
from safety import SafetyError
from tools import DISPATCH, TOOL_SPECS

SYSTEM = f"""You are a versatile AI assistant on Windows.
Desktop: {config.DESKTOP} | Downloads: {config.DOWNLOADS} | Documents: {config.DOCUMENTS}

You MUST use tools to complete tasks step by step. Follow the user's explicit instructions carefully.

Core Capabilities and Rules:
1. CODE WRITING & ALGORITHMS (Primary): When asked to write code (e.g. 'write python code for A star', 'write BFS', 'implement algorithm', 'create script'):
   - ALWAYS use write_file to save the code as a .py file (e.g. astar.py, bfs.py, dfs.py). NEVER use create_document for Python code!
   - Write complete, working, self-contained code with test data and print() statements to show results.
   - NEVER import 'tools' or 'agent' inside Python code (e.g. 'from tools import ...' or 'import tools' is STRICTLY FORBIDDEN and causes ModuleNotFoundError). Python scripts run in an isolated environment. Tools like write_file, send_email, create_document are agent functions called via JSON, NEVER inside Python.
   - Use print() for output; NEVER use 'with open(...)' for stdout. NEVER print uninitialized template variables like {{to_email}} or {{output_file}} in Python.
   - If the user only asks to write/save code (without asking to run or email): save the file and call done().

2. EXECUTING CODE:
   - Call run_python when the user asks to run, execute, test the code, or when the user asks to email the code/output (verifying it works first).
   - If the user explicitly asks to save the execution output under a specific filename (e.g. 'save output as prime_op.txt'), call save_output.

3. DOCUMENT GENERATION:
   - When asked to create formatted Word documents (.docx), PDF reports (.pdf), notes, essays, or summaries (e.g. 'create a Word document on AI definition, advantages...'):
     - Use create_document to generate the .docx or .pdf file.
     - For Word documents (.docx), provide rich content with Markdown headings (#, ##), bullet points (- ), bold (**text**), and clear paragraphs.
     - Save to the requested location: if user says 'in downloads', save to downloads (e.g. 'downloads/ai.docx' or folder='downloads'); if user says 'in documents', save to documents; otherwise Desktop.
     - When the document is saved, call done() with a summary. Do NOT run code or send emails unless requested!

4. SENDING EMAILS & ATTACHING EXISTING FILES:
   - When asked to email, send files, or find existing files and send them (e.g. 'find files matrices.py and mat_op.txt present in desktop and send them to mail...'):
     - If the files already exist on Desktop, DO NOT rewrite or re-run them! Call send_email directly with the recipient email, an appropriate subject, and attachments (e.g. attachments='matrices.py, mat_op.txt').
     - Use send_email with the EXACT recipient email address from the user's prompt (NEVER placeholder addresses).
     - Attach all requested files (code .py, output .txt, or document .docx/.pdf).

5. REMOTE PC ACCESS & FILE TRANSFER (Over Local Wi-Fi):
   - When asked to connect to another PC/laptop (e.g. 'connect to remote pc 192.168.1.5 with pin 1234', 'browse remote pc', 'fetch file from laptop 2'):
     - Use browse_remote_pc with ip, pin, and optional folder to list files and directories on Person 2's laptop.
     - Use fetch_remote_file with ip, pin, and file_path to securely download an authorized file from Person 2's laptop. The file is saved to Desktop/remote_downloads.
     - If the user asks to email the remote file: first download it using fetch_remote_file, then call send_email with the recipient email, subject, and the filename in attachments (e.g. attachments='<filename>'), then call done().

6. COMPLETION:
   - Call done() with a clear summary as soon as all user-requested tasks are finished. Do not perform extra unrequested steps.

If native tool-calling is unavailable, output a single JSON object in a ```json code block:
Example for Python code:
```json
{{"name": "write_file", "arguments": {{"path": "astar.py", "content": "...complete python code..."}}}}
```
Example for Word document:
```json
{{"name": "create_document", "arguments": {{"path": "downloads/ai.docx", "content": "..."}}}}
```
Example for Remote PC:
```json
{{"name": "fetch_remote_file", "arguments": {{"ip": "192.168.1.35", "pin": "1234", "file_path": "report.pdf"}}}}
```
One tool per step.
"""


def main() -> int:
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        print(f"{config.PROJECT_ROOT.name}  (CPU / Ollama local)")
        print(f"Model: {config.MODEL}")
        print("Type a task, or quit.")
        try:
            task = input("> ").strip()
        except EOFError:
            return 0
    if not task or task.lower() in {"q", "quit", "exit"}:
        return 0

    try:
        _ensure_ollama()
    except ollama_client.OllamaError as exc:
        print(exc)
        return 1

    print(f"Working on: {task}\n")
    try:
        summary = run_task(task)
    finally:
        ollama_client.unload()
    print("\n=== Done ===")
    print(summary)
    return 0


def _compact_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact older message history to stay well within model context window and speed up CPU inference."""
    compacted: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i < 2 or i >= len(messages) - 2:
            compacted.append(m)
            continue
        m_copy = dict(m)
        if m_copy.get("role") == "assistant" and m_copy.get("tool_calls"):
            new_calls = []
            for tc in m_copy["tool_calls"]:
                tc_copy = json.loads(json.dumps(tc))
                fn = tc_copy.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, dict) and "content" in args and len(str(args["content"])) > 250:
                    p = args.get("path", "file")
                    args["content"] = str(args["content"])[:120] + f"... [content saved to {p}]"
                new_calls.append(tc_copy)
            m_copy["tool_calls"] = new_calls
        elif m_copy.get("role") in ("tool", "user") and len(str(m_copy.get("content", ""))) > 600:
            m_copy["content"] = str(m_copy["content"])[:500] + "... [truncated]"
        compacted.append(m_copy)
    return compacted


def run_task(task: str, event_callback: Any | None = None) -> str:
    # Ensure local Ollama is active before executing task
    try:
        _ensure_ollama()
    except Exception as exc:
        if event_callback:
            event_callback({"type": "error", "message": str(exc)})
        raise

    # Reset per-session state (run flags, email counter, stdout cache, target folder)
    task_lower = task.lower()
    try:
        import tools as _tools_mod
        _tools_mod.reset_session()
        if any(w in task_lower for w in ["downloads", "download"]):
            _tools_mod.set_session_folder("downloads")
        elif any(w in task_lower for w in ["documents", "document"]):
            _tools_mod.set_session_folder("documents")
        elif any(w in task_lower for w in ["pictures", "picture", "photos", "photo", "images", "image"]):
            _tools_mod.set_session_folder("pictures")
        else:
            _tools_mod.set_session_folder("desktop")
    except Exception:
        pass

    is_remote_pc = any(w in task_lower for w in [
        "remote pc", "remote laptop", "remote node", "other pc", "other laptop",
        "laptop 2", "laptop2", "person 2", "person2", "remote file", "remote explorer", "browse remote", "remote"
    ]) or ("pin" in task_lower and ("ip" in task_lower or re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", task) or "connect" in task_lower))

    user_wants_creation = any(w in task_lower for w in [
        "write", "create", "implement", "generate", "code for", "program for", "script for", "build a", "make a"
    ])
    is_send_existing = (
        not is_remote_pc
        and not user_wants_creation
        and any(w in task_lower for w in ["find", "present in", "existing", "send", "mail", "email", "attach", "forward", "go to", "check", "get", "look for"])
        and any(w in task_lower for w in [".py", ".txt", ".docx", ".doc", ".docs", ".pdf", ".csv", ".json", ".jpg", ".png", ".jpeg", "file", "files", "photo", "photos", "picture", "pictures", "image", "images"])
    )

    user_wants_code = not is_remote_pc and not is_send_existing and any(w in task_lower for w in [
        "python", "write code", "write python", "code for", "script", "algorithm",
        "bfs", "dfs", "a star", "a* algorithm", "a*", "dijkstra", "path finding", "pathfinding",
        "prime", "fibonacci", "armstrong", ".py"
    ])
    user_wants_doc = not is_remote_pc and not is_send_existing and any(w in task_lower for w in [
        "word", "docx", "doc", "pdf", "essay", "definition", "advantages", "disadvantages",
        "report", "document", "notes"
    ]) and not user_wants_code
    user_wants_email = not is_remote_pc and any(w in task_lower for w in [
        "mail", "email", "send to", "send that", "send this", "send them"
    ]) and not user_wants_code and not user_wants_doc and not is_send_existing

    default_py = "script.py"
    if user_wants_code:
        py_m = re.search(r"\b([\w\-]+\.py)\b", task, re.IGNORECASE)
        if py_m:
            default_py = py_m.group(1)
        else:
            if "a star" in task_lower or "a*" in task_lower:
                default_py = "astar.py"
            elif "bfs" in task_lower:
                default_py = "bfs.py"
            elif "dfs" in task_lower:
                default_py = "dfs.py"
            elif "dijkstra" in task_lower:
                default_py = "dijkstra.py"
            elif "prime" in task_lower:
                default_py = "prime.py"
            elif "fibonacci" in task_lower:
                default_py = "fibonacci.py"
            elif "armstrong" in task_lower:
                default_py = "armstrong.py"
            elif "path" in task_lower:
                default_py = "pathfinding.py"
            else:
                default_py = "script.py"

    initial_prompt = task
    if is_remote_pc:
        ip_match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|localhost)\b", task)
        remote_ip = ip_match.group(1) if ip_match else ""

        pin_match = re.search(r"\b(?:pin|code)\s*(?:is|=|:)?\s*([0-9]{4,6})\b", task, re.IGNORECASE)
        if not pin_match:
            potential_pins = re.findall(r"\b([0-9]{4})\b", task)
            remote_pin = potential_pins[0] if potential_pins else ""
        else:
            remote_pin = pin_match.group(1)

        if not remote_ip or not remote_pin:
            try:
                import tools as _t
                c_ip, c_pin = _t.get_last_remote()
                remote_ip = remote_ip or c_ip
                remote_pin = remote_pin or c_pin
            except Exception:
                pass

        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
        found_email = email_match.group(0) if email_match else ""

        ext_pattern = r"\b([\w\-.]+\.(?:py|python|txt|text|docx?|docs?|pdf|csv|json|xlsx?|pptx?|png|jpe?g))\b"
        file_match = re.search(ext_pattern, task, re.IGNORECASE)
        remote_file = file_match.group(1) if file_match else ""
        if not remote_file:
            file_kw_match = re.findall(r"(?:file|files)\s+(['\"]?[\w\-.]+['\"]?)", task, re.IGNORECASE)
            if file_kw_match:
                cand = file_kw_match[0].strip("'\"")
                if cand.lower() not in {"in", "on", "from", "present", "all", "the", "and", "or", "folder"}:
                    remote_file = cand

        user_wants_list = any(w in task_lower for w in ["list", "browse", "show", "see", "view", "what files", "check files", "files in"])
        if user_wants_list and not found_email and not file_match:
            remote_file = ""

        if found_email and remote_file:
            initial_prompt += (
                f"\n\n[Directive: The user wants to retrieve the file '{remote_file}' from Person 2's remote PC "
                f"(IP: '{remote_ip or '<remote_ip>'}', PIN: '{remote_pin or '<pin>'}') and email it to {found_email}. "
                f"Step 1 MUST be to call fetch_remote_file with ip='{remote_ip}', pin='{remote_pin}', file_path='{remote_file}'. "
                f"Step 2: Once downloaded, call send_email with to_email='{found_email}', subject='Remote File: {remote_file}', "
                f"body='Please find attached {remote_file} retrieved from remote PC.', attachments='{remote_file}'. "
                f"Step 3: Call done() with a summary.]"
            )
        elif remote_file:
            initial_prompt += (
                f"\n\n[Directive: The user wants to download the file '{remote_file}' from Person 2's remote PC "
                f"(IP: '{remote_ip or '<remote_ip>'}', PIN: '{remote_pin or '<pin>'}'). "
                f"Step 1 MUST be to call fetch_remote_file with ip='{remote_ip}', pin='{remote_pin}', file_path='{remote_file}'. "
                f"Step 2: Call done() confirming the file has been downloaded.]"
            )
        else:
            initial_prompt += (
                f"\n\n[Directive: The user wants to browse or list files on Person 2's remote PC "
                f"(IP: '{remote_ip or '<remote_ip>'}', PIN: '{remote_pin or '<pin>'}'). "
                f"Step 1 MUST be to call browse_remote_pc with ip='{remote_ip}', pin='{remote_pin}'. "
                f"Step 2: Call done() summarizing the available remote files and folders.]"
            )
    elif is_send_existing:
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
        found_email = email_match.group(0) if email_match else ""
        ext_pattern = r"\b([\w\-.]+\.(?:py|python|txt|text|docx?|docs?|pdf|csv|json|xlsx?|pptx?|png|jpe?g))\b"
        files_in_prompt = [f for f in re.findall(ext_pattern, task, re.IGNORECASE) if f.lower() not in {"requirements.txt"}]
        if not files_in_prompt:
            file_kw_match = re.findall(r"(?:file|files|photo|photos|picture|pictures|image|images)\s+(['\"]?[\w\-.]+['\"]?)", task, re.IGNORECASE)
            if file_kw_match:
                files_in_prompt = [m.strip("'\"") for m in file_kw_match if m.strip("'\"").lower() not in {"present", "in", "on", "from"}]
        if not files_in_prompt and any(w in task_lower for w in ["photo", "photos", "picture", "pictures", "image", "images"]):
            try:
                for img_root in [config.PICTURES, config.DESKTOP, config.DOWNLOADS]:
                    if img_root.exists():
                        img_files = [f for f in img_root.iterdir() if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
                        if img_files:
                            files_in_prompt = [img_files[0].name]
                            break
            except Exception:
                pass

        # Resolve real filenames on disk
        resolved_names = []
        for raw_name in files_in_prompt:
            try:
                from safety import resolve_user_path
                p = resolve_user_path(raw_name)
                if p.exists() and p.is_file():
                    resolved_names.append(p.name)
                else:
                    resolved_names.append(raw_name)
            except Exception:
                resolved_names.append(raw_name)

        attach_str = ", ".join(resolved_names) if resolved_names else ", ".join(files_in_prompt)
        loc_str = "Downloads" if "download" in task_lower else ("Documents" if "document" in task_lower else ("Pictures" if "picture" in task_lower or "photo" in task_lower else "Desktop"))
        if found_email:
            initial_prompt += (
                f"\n\n[Directive: The user wants to send existing file(s): '{attach_str}' to {found_email}. "
                f"Do NOT write any new code or create documents! The file already exists. "
                f"Step 1 MUST be to call send_email with to_email='{found_email}', "
                f"attachments='{attach_str}', subject='Requested File: {attach_str}', "
                f"and body='Please find attached {attach_str} from {loc_str}.' "
                f"After send_email succeeds, call done().]"
            )
        else:
            initial_prompt += (
                f"\n\n[Directive: The user wants to check/find files: '{attach_str}'. "
                f"Call list_dir or read_file to check them and call done() with a summary.]"
            )
    elif user_wants_code:
        extra_note = ""
        if "dfs" in task_lower or "depth first" in task_lower:
            extra_note = " For DFS: use graph.get(node, []) so leaf nodes never cause KeyError. Store path in stack tuples e.g. stack = [(start, [start])] and print the full path found."
        elif "bfs" in task_lower or "breadth first" in task_lower:
            extra_note = " For BFS: use graph.get(node, []) so leaf nodes never cause KeyError. Store path in queue tuples e.g. queue = deque([(start, [start])]) and print the full path found."
        elif "a star" in task_lower or "a*" in task_lower:
            extra_note = " For A* search: represent positions as simple coordinate tuples (x, y) like start=(0,0) and goal=(4,4) with Manhattan distance abs(x1-x2)+abs(y1-y2) and heapq. Do NOT use complicated Node classes."
        initial_prompt += (
            f"\n\n[Directive: The user is requesting Python code. "
            f"Step 1 MUST be write_file to save the complete Python script as '{default_py}'.{extra_note} "
            f"Do NOT use create_document. Then run it with run_python before emailing or completing.]"
        )
    elif user_wants_doc:
        initial_prompt += (
            f"\n\n[Directive: The user is requesting document creation (.docx or .pdf). "
            f"Use create_document to generate the file. Do NOT run code unless requested.]"
        )
    elif user_wants_email:
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
        found_email = email_match.group(0) if email_match else ""
        to_clause = f"to_email='{found_email}'" if found_email else "to_email=<recipient_email>"

        # Derive an appropriate subject based on the task topic
        if "birthday" in task_lower:
            inferred_subject = "Happy Birthday!"
        elif any(w in task_lower for w in ["leave", "absence", "sick", "permission"]):
            inferred_subject = "Leave Application"
        elif any(w in task_lower for w in ["lab", "task", "assignment", "submit", "submission"]):
            inferred_subject = "Lab Task Submission Update"
        elif any(w in task_lower for w in ["project", "report"]):
            inferred_subject = "Project Submission & Update"
        elif any(w in task_lower for w in ["meeting", "appointment"]):
            inferred_subject = "Meeting Request"
        else:
            inferred_subject = "Formal Communication"

        initial_prompt += (
            f"\n\n[Directive: The user wants to compose and send an email directly. "
            f"Step 1 MUST be to call send_email with {to_clause}, subject='{inferred_subject}', and a complete, well-written message body matching the user's instructions. "
            f"Do NOT write python code or create document files unless specifically requested. "
            f"After send_email succeeds, call done().]"
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": initial_prompt},
    ]
    last_summary = "Stopped without a done() call."
    empty_stdout_warned = False
    if event_callback:
        event_callback({"type": "start", "task": task})

    for step in range(1, config.MAX_STEPS + 1):
        print(f"-- step {step}/{config.MAX_STEPS} --")
        if event_callback:
            event_callback({"type": "step", "step": step, "max_steps": config.MAX_STEPS})
        message = ollama_client.chat(_compact_history(messages), TOOL_SPECS)
        messages.append(message)

        calls = message.get("tool_calls") or _parse_text_tool(message.get("content") or "")
        if not calls:
            content = (message.get("content") or "").strip()
            if content:
                print(content[:500])
                if event_callback:
                    event_callback({"type": "thought", "content": content[:1000]})
            fallback = _code_fence_fallback(task, content)
            if fallback:
                calls = fallback
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Call a tool now to complete the next step. Example JSON:\n'
                            '```json\n{"name": "write_file", "arguments": {"path": "amstrong.py", "content": "...python code..."}}\n```'
                        ),
                    }
                )
                continue

        for call in calls:
            name, args = _call_parts(call)
            print(f"tool: {name} { _short_args(name, args) }")
            if event_callback:
                event_callback({"type": "tool_start", "name": name, "args": args})
            if name == "done":
                summary = str(args.get("summary") or last_summary)
                if event_callback:
                    event_callback({"type": "done", "summary": summary})
                return summary
            fn = DISPATCH.get(name)
            if not fn:
                result = f"Unknown tool: {name}"
            else:
                try:
                    result = str(fn(**args))
                except (SafetyError, TypeError, OSError, TimeoutError) as exc:
                    result = f"ERROR: {exc}"
            print(result[:800] + ("..." if len(result) > 800 else ""))
            last_summary = result
            if event_callback:
                event_callback({"type": "tool_result", "name": name, "result": result})

            # Calculate next_hint and perform automated hooks for ALL calls (native and text-parsed)
            next_hint = ""
            user_wants_run = any(w in task.lower() for w in [
                "run", "execute", "exec", "output", "op.txt", "_op.txt", "_output", "save the op", "save output", "test it", "print output"
            ])
            user_wants_email = any(w in task.lower() for w in [
                "mail", "email", "send to", "send that", "send this"
            ])
            email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
            found_email = email_match.group(0) if email_match else ""

            if name in ("write_file", "create_document"):
                p = args.get("path") or args.get("filename") or "file"
                p_str = str(p).lower()
                if p_str.endswith(".py"):
                    if user_wants_run or user_wants_email:
                        next_hint = (
                            f" NEXT STEP: Call run_python with path='{p}' to execute the script and verify output. "
                            f"Do NOT call send_email or done until it runs with exit_code=0."
                        )
                    else:
                        next_hint = (
                            f" Successfully saved Python script '{p}'. "
                            f"The task did not request running or emailing the code. Call done() with a summary to finish."
                        )
                else:
                    # Document file (.docx, .doc, .txt, .md, etc.)
                    if user_wants_code and not p_str.endswith(".py"):
                        next_hint = (
                            f" NOTE: The user requested Python code. "
                            f"You must now call write_file to save the Python script '{default_py}' with the algorithm logic. "
                            f"Do NOT send email until '{default_py}' is written and executed with run_python!"
                        )
                    elif user_wants_email:
                        to_clause = f"to_email='{found_email}'" if found_email else "to_email=<email from user prompt>"
                        next_hint = (
                            f" Document '{p}' created successfully! "
                            f"Now call send_email with {to_clause}, attachments='{p}', "
                            f"subject='Document: {p}', body='Please find the attached document.'"
                        )
                    else:
                        next_hint = (
                            f" Document '{p}' created and saved successfully! "
                            f"Call done() with a summary of the document to finish the task."
                        )
            elif name == "run_python":
                run_failed = "exit_code=1" in result or "exit_code=2" in result or ("Error" in result and "exit_code=0" not in result)
                p = args.get("path") or args.get("filename") or "script.py"
                script_stem = str(p).replace("\\", "/").split("/")[-1]
                if run_failed:
                    if "ModuleNotFoundError" in result or "No module named 'tools'" in result or "No module named 'agent'" in result:
                        next_hint = (
                            f" ERROR: The script '{script_stem}' failed with: {result[:200]}. "
                            f"DO NOT import 'tools' or 'agent' inside Python! Python scripts cannot access agent tools. "
                            f"Call write_file with path='{script_stem}' and pure algorithm code (using only standard libraries like heapq, collections, math), "
                            f"then run it again with run_python."
                        )
                    else:
                        next_hint = (
                            f" ERROR: The script '{script_stem}' failed with the error above. "
                            f"You MUST fix the code: call write_file with path='{script_stem}' and corrected Python code, "
                            f"then run it again with run_python. Do NOT call send_email or done until it runs with exit_code=0."
                        )
                else:
                    # Check for silent success (exit_code=0 but no stdout)
                    stdout_empty = "--- stdout ---\n(empty)" in result or "stdout ---\n(empty)" in result
                    if stdout_empty and not empty_stdout_warned:
                        empty_stdout_warned = True
                        p2 = args.get("path") or args.get("filename") or "script.py"
                        stem2 = str(p2).replace("\\", "/").split("/")[-1]
                        next_hint = (
                            f" WARNING: The script ran with exit_code=0 but produced NO output. "
                            f"The output file will be empty. You MUST fix '{stem2}': "
                            f"add print() statements to display results, then call run_python again."
                        )
                        full_result_text = f"{result[:3500]}\n\n{next_hint}".strip()
                        if message.get("tool_calls"):
                            messages.append({"role": "tool", "content": full_result_text, "tool_name": name})
                        else:
                            messages.append({"role": "user", "content": f"Tool '{name}' result:\n{full_result_text}\nPlease proceed."})
                        continue
                    user_wants_email = any(w in task.lower() for w in ["mail", "email", "send to", "send that", "send this"])
                    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
                    found_email = email_match.group(0) if email_match else ""
                    # Check if user specified a custom output filename (e.g. "save the op as exe1_op.txt")
                    custom_out_match = re.search(
                        r"(?:save\s+(?:the\s+)?(?:output|ouput|op|o/p|out|results?|files?|file)?\s*(?:as|to|file\s+as)|(?:output|ouput|op|o/p|out)\s+files?\s+(?:as|named?)?|name\s+(?:the\s+)?(?:output|ouput|op|o/p|it)\s+as)\s+([\w\-]+\.txt)",
                        task, re.IGNORECASE
                    )

                    if custom_out_match:
                        output_file = custom_out_match.group(1)
                    else:
                        txts = [f for f in re.findall(r"\b([\w\-]+\.txt)\b", task, re.IGNORECASE) if f.lower() not in {"requirements.txt"}]
                        output_file = txts[0] if txts else re.sub(r"\.py$", "_output.txt", script_stem)

                    try:
                        import tools as _t
                        _t.save_output(output_file, "")
                    except Exception:
                        pass

                    if user_wants_email:
                        to_clause = f"to_email='{found_email}'" if found_email else "to_email=<email from user prompt>"
                        next_hint = (
                            f" The script succeeded! Output saved to '{output_file}'. "
                            f"Now call send_email with {to_clause}, "
                            f"attachments='{script_stem}, {output_file}', "
                            f"subject='Code and Output: {script_stem}', "
                            f"body='Please find the attached code and output files.'"
                        )
                    else:
                        next_hint = f" The script succeeded and output was saved to '{output_file}'. You can now call done with a summary of what was accomplished."
            elif name == "save_output":
                user_wants_email = any(w in task.lower() for w in ["mail", "email", "send to", "send that", "send this"])
                if user_wants_email:
                    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
                    found_email = email_match.group(0) if email_match else ""
                    saved_file = str(args.get("path") or "output.txt").split("/")[-1].split("\\")[-1]
                    to_clause = f"to_email='{found_email}'" if found_email else "to_email=<email from user prompt>"
                    py_match = re.search(r"([\w\-]+\.py)", task, re.IGNORECASE)
                    py_file = py_match.group(1) if py_match else ""
                    attach_list = f"{py_file}, {saved_file}" if py_file else saved_file
                    next_hint = (
                        f" Output saved as '{saved_file}'. "
                        f"Now call send_email with {to_clause}, "
                        f"attachments='{attach_list}', "
                        f"subject='Code and Output', "
                        f"body='Please find the attached files.'"
                    )
                else:
                    next_hint = " Output saved. You can now call done with a summary."
            elif name == "send_email":
                # If email sent successfully, force immediate task completion
                if "successfully sent" in result.lower() or "email sent" in result.lower():
                    summary = f"Task completed. {result[:300]}"
                    if event_callback:
                        event_callback({"type": "done", "summary": summary})
                    return summary
                elif "already sent" in result.lower():
                    next_hint = " Email already sent. Call done() with a brief summary."
                elif "run_python" in result.lower() or "failed execution" in result.lower():
                    py_m = re.search(r"[\w\-]+\.py", task, re.IGNORECASE)
                    stem_fix = py_m.group(0) if py_m else "script.py"
                    next_hint = (
                        f" BLOCKED: The email cannot be sent because the script failed. "
                        f"You MUST fix the code: 1) call write_file with corrected Python code for '{stem_fix}' "
                        f"that uses print() statements and has no errors, "
                        f"2) call run_python and verify exit_code=0 with real stdout output, "
                        f"3) ONLY THEN call send_email."
                    )
                else:
                    next_hint = f" {result[:200]} Fix the issue then call send_email again, or call done() to end."
            elif name == "browse_remote_pc":
                next_hint = " Files on remote PC listed above. If you need to download a file, call fetch_remote_file. Otherwise, call done() with the summary."
            elif name == "fetch_remote_file":
                if "Successfully downloaded" in result:
                    user_wants_email = any(w in task.lower() for w in ["mail", "email", "send to", "send that", "send this"])
                    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", task)
                    found_email = email_match.group(0) if email_match else ""
                    downloaded_name = str(args.get("file_path") or "").replace("\\", "/").split("/")[-1]
                    if user_wants_email and found_email:
                        next_hint = (
                            f" Remote file '{downloaded_name}' downloaded successfully to Desktop/remote_downloads! "
                            f"Now call send_email with to_email='{found_email}', attachments='{downloaded_name}', "
                            f"subject='Remote File: {downloaded_name}', body='Please find attached the file retrieved from remote PC.'."
                        )
                    else:
                        next_hint = f" Remote file '{downloaded_name}' downloaded to Desktop/remote_downloads. Call done() to complete."
                else:
                    next_hint = " Error downloading remote file. Check IP, PIN, or file path, or call done()."

            full_result_text = f"{result[:3500]}\n\n{next_hint}".strip()
            if message.get("tool_calls"):
                messages.append(
                    {
                        "role": "tool",
                        "content": full_result_text,
                        "tool_name": name,
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{name}' execution result:\n{full_result_text}\nPlease proceed to the next step using a tool, or call done if all tasks are finished.",
                    }
                )

    if event_callback:
        event_callback({"type": "done", "summary": last_summary})
    return last_summary + f"\nReached max steps ({config.MAX_STEPS}) to limit CPU heat."


def _ensure_ollama() -> None:
    if not ollama_client.is_up():
        import setup_model

        setup_model._try_start_ollama()
        import time

        for _ in range(20):
            if ollama_client.is_up():
                break
            time.sleep(1)
    if not ollama_client.is_up():
        raise ollama_client.OllamaError(
            "Ollama is not running. Open the Ollama app from the Start menu, then retry."
        )
    names = ollama_client.list_models()
    if any(config.MODEL in n or n.startswith(config.MODEL) for n in names):
        return
    print(f"Model {config.MODEL} not found. Running setup_model.py (Hugging Face GGUF import)...")
    import subprocess

    result = subprocess.run(
        [sys.executable, str(config.PROJECT_ROOT / "setup_model.py")],
        cwd=str(config.PROJECT_ROOT),
    )
    if result.returncode != 0:
        raise ollama_client.OllamaError(
            "Could not install the local model. Run: python setup_model.py"
        )
    print("Model ready.")


def _call_parts(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if "function" in call and isinstance(call["function"], dict):
        fn = call["function"]
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                raw_args = {}
        return name, raw_args or {}
    name = call.get("name") or call.get("tool") or call.get("action", "")
    args = call.get("arguments") or call.get("args") or call.get("parameters") or {
        k: v for k, v in call.items() if k not in {"action", "name", "tool", "type", "function"}
    }
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            args = {}
    return str(name), args or {}


def _parse_text_tool(text: str) -> list[dict[str, Any]]:
    """Fallback when the small model writes JSON or tool tags instead of native tool calls."""
    if not text:
        return []
    calls: list[dict[str, Any]] = []

    tag_matches = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
    fence_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = tag_matches + fence_matches
    if not candidates:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            candidates = [match.group(0)]
        else:
            unclosed = re.search(r"\{[\s\S]*", text)
            if unclosed:
                candidates = [unclosed.group(0)]

    for candidate in candidates:
        raw = candidate.strip()
        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            for suffix in ['"', '"}', '"}}', '"}}}', '}', '}}', '}}}']:
                try:
                    data = json.loads(raw + suffix)
                    break
                except json.JSONDecodeError:
                    continue

        if data is None:
            # Regex extraction if JSON string was cut off
            name_m = re.search(r'"(?:name|tool|action)":\s*"([^"]+)"', raw)
            path_m = re.search(r'"(?:path|filename)":\s*"([^"]+)"', raw)
            content_m = re.search(r'"(?:content|code)":\s*"(.*)', raw, re.DOTALL)
            if name_m and path_m and content_m:
                t_name = name_m.group(1)
                t_path = path_m.group(1)
                t_content = content_m.group(1)
                t_content = re.sub(r'["}\s]+$', '', t_content)
                try:
                    t_content = t_content.encode("utf-8").decode("unicode_escape")
                except Exception:
                    pass
                try:
                    import tools as _tools
                    t_content = _tools._clean_markdown_escapes(t_content)
                    t_content = _tools._fix_split_strings(t_content)
                except Exception:
                    pass
                calls.append({"function": {"name": t_name, "arguments": {"path": t_path, "content": t_content}}})
            continue

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    calls.append(item)
            continue
        if isinstance(data, dict):
            if "function" in data and isinstance(data["function"], dict):
                calls.append(data)
            else:
                name = data.get("action") or data.get("name") or data.get("tool")
                if name:
                    args = data.get("arguments") or data.get("args") or data.get("parameters") or {
                        k: v for k, v in data.items() if k not in {"action", "name", "tool", "type", "function"}
                    }
                    calls.append({"function": {"name": name, "arguments": args}})
    return calls


def _code_fence_fallback(task: str, content: str) -> list[dict[str, Any]]:
    """If the model dumps code or document text instead of calling a tool, still save it."""
    is_doc_task = any(w in task.lower() for w in ["word", "doc", "docx", "pdf", "document", "notes", "summary", "definition", "essay"])
    if is_doc_task and len(content.strip()) > 80:
        doc_match = re.search(r"[\w\-]+\.(?:docx|doc|pdf|txt|md)", task, re.IGNORECASE)
        if doc_match:
            doc_name = doc_match.group(0)
        elif "pdf" in task.lower():
            doc_name = "document.pdf"
        else:
            doc_name = "document.docx" if any(w in task.lower() for w in ["word", "doc", "docx"]) else "document.txt"
        return [{"function": {"name": "create_document", "arguments": {"path": doc_name, "content": content.strip()}}}]

    match = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
    code = ""
    if match:
        code = match.group(1).strip()
    else:
        unclosed = re.search(r"```(?:python)?\s*\n(.*)", content, re.DOTALL | re.IGNORECASE)
        if unclosed:
            code = unclosed.group(1).strip()
    if not code:
        return []
    if "def " not in code and "print" not in code:
        return []
    name_match = re.search(r'''(?:save|file|as)\s+["']?([\w.-]+\.py)''', task, re.IGNORECASE)
    filename = name_match.group(1) if name_match else "script.py"
    return [{"function": {"name": "write_file", "arguments": {"path": filename, "content": code}}}]


def _short_args(name: str, args: dict[str, Any]) -> str:
    if name in ("write_file", "create_document"):
        return str(args.get("path", ""))
    if name == "run_python":
        return str(args.get("path", ""))
    return json.dumps({k: v for k, v in args.items() if k != "content"})[:120]


if __name__ == "__main__":
    raise SystemExit(main())
