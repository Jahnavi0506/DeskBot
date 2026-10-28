let activeTaskId = null;
let eventSource = null;
let currentOutput = "";
let currentStepCount = 0;
let activeStepCard = null;

document.addEventListener("DOMContentLoaded", () => {
  initStatus();
  initTabs();
  initPresets();
  initFiles();
  initShortcuts();
  initEventHandlers();
});

// ── Initialize Status & Active Directory ─────────────────────────────────────
async function initStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const modelText = document.getElementById("model-status-text");
    const desktopText = document.getElementById("desktop-path-text");
    const statusChip = document.getElementById("ollama-status-chip");

    if (data.ollama_online) {
      modelText.textContent = `Ollama: ${data.model}`;
      statusChip.querySelector(".status-dot").className = "status-dot online";
    } else {
      modelText.textContent = "Ollama: Offline";
      statusChip.querySelector(".status-dot").className = "status-dot";
    }

    if (data.desktop) {
      desktopText.textContent = data.desktop;
      desktopText.title = data.desktop;
    }
  } catch (err) {
    console.error("Failed to fetch status:", err);
  }
}

// ── Inspector Tabs ───────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll(".itab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".itab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".itab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const target = document.getElementById(btn.dataset.itab);
      if (target) target.classList.add("active");
    });
  });
}

// ── Preset Chips ─────────────────────────────────────────────────────────────
function initPresets() {
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const promptInput = document.getElementById("prompt-input");
      promptInput.value = chip.dataset.prompt;
      promptInput.focus();
    });
  });

  document.getElementById("btn-clear-prompt").addEventListener("click", () => {
    document.getElementById("prompt-input").value = "";
  });
}

// ── Keyboard Shortcuts ───────────────────────────────────────────────────────
function initShortcuts() {
  const promptInput = document.getElementById("prompt-input");
  promptInput.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      startAgentRun();
    }
  });
}

// ── Desktop Files Explorer ───────────────────────────────────────────────────
async function initFiles() {
  const container = document.getElementById("desktop-files-list");
  try {
    const res = await fetch("/api/files");
    const files = await res.json();
    if (!files || !files.length) {
      container.innerHTML = '<div class="empty-state">No files on Desktop yet.</div>';
      return;
    }

    container.innerHTML = files
      .map((f) => {
        let icon = "📄";
        if (f.is_dir) icon = "📁";
        else if (f.name.endsWith(".py")) icon = "🐍";
        else if (f.name.endsWith(".docx") || f.name.endsWith(".doc")) icon = "📘";
        else if (f.name.endsWith(".pdf")) icon = "📕";
        else if (f.name.endsWith(".txt")) icon = "📝";

        return `
          <div class="file-item" data-path="${encodeURIComponent(f.path)}">
            <div class="file-name" title="${f.name}">
              <span>${icon}</span>
              <span>${escapeHtml(f.name)}</span>
            </div>
            <span class="file-size">${f.is_dir ? "" : formatBytes(f.size)}</span>
          </div>
        `;
      })
      .join("");

    container.querySelectorAll(".file-item").forEach((el) => {
      el.addEventListener("click", () => {
        const path = decodeURIComponent(el.dataset.path);
        viewFile(path);
      });
    });
  } catch (err) {
    container.innerHTML = '<div class="empty-state">Failed to load files.</div>';
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// ── File Content Preview ─────────────────────────────────────────────────────
async function viewFile(path) {
  try {
    const res = await fetch(`/api/file-content?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error("Could not read file (may be binary format).");
    const data = await res.json();

    document.getElementById("preview-filename").textContent = data.path;
    document.getElementById("code-content").textContent = data.content;

    document.querySelector('[data-itab="code-view"]').click();
  } catch (err) {
    document.getElementById("preview-filename").textContent = path;
    document.getElementById("code-content").textContent = `[Binary or Formatted File: ${path}]\nNote: Word (.docx) and PDF (.pdf) files can be opened directly from your Desktop / Downloads.`;
    document.querySelector('[data-itab="code-view"]').click();
  }
}

// ── Event Handlers ───────────────────────────────────────────────────────────
function initEventHandlers() {
  document.getElementById("btn-run-agent").addEventListener("click", startAgentRun);
  document.getElementById("btn-refresh-files").addEventListener("click", initFiles);
  document.getElementById("btn-clear-feed").addEventListener("click", clearFeed);
  document.getElementById("btn-unload").addEventListener("click", unloadModel);
  document.getElementById("btn-copy-output").addEventListener("click", copyCurrentOutput);
}

// ── Run Agent Task ───────────────────────────────────────────────────────────
async function startAgentRun() {
  const prompt = document.getElementById("prompt-input").value.trim();
  if (!prompt) {
    alert("Please enter a task instruction.");
    return;
  }

  const runBtn = document.getElementById("btn-run-agent");
  runBtn.disabled = true;
  runBtn.innerHTML = '<span class="icon">⏳</span> Running...';

  const stepBadge = document.getElementById("step-indicator");
  stepBadge.className = "step-badge running";
  stepBadge.textContent = "Running";

  const stepCountBadge = document.getElementById("step-count-badge");
  stepCountBadge.style.display = "inline-block";
  stepCountBadge.textContent = "Step 0";

  clearFeed();
  currentStepCount = 0;
  activeStepCard = null;

  // Render initial instruction card
  addStepCard({
    icon: "🎯",
    title: "Task Request",
    tool: "thought",
    summary: prompt,
    details: prompt,
    collapsible: false,
  });

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Run request failed.");
    }

    const { task_id } = await res.json();
    activeTaskId = task_id;
    listenToStream(task_id);
  } catch (err) {
    addStepCard({
      icon: "❌",
      title: "Request Error",
      tool: "thought",
      summary: err.message,
      details: err.message,
      isError: true,
      collapsible: true,
    });
    resetRunButton();
  }
}

// ── SSE Stream Listener ──────────────────────────────────────────────────────
function listenToStream(taskId) {
  if (eventSource) eventSource.close();

  eventSource = new EventSource(`/api/stream/${taskId}`);

  eventSource.addEventListener("step", (e) => {
    const data = JSON.parse(e.data);
    currentStepCount = data.step;
    document.getElementById("step-indicator").textContent = `Step ${data.step}/${data.max_steps}`;
    document.getElementById("step-count-badge").textContent = `${data.step} steps`;
  });

  eventSource.addEventListener("thought", (e) => {
    const data = JSON.parse(e.data);
    if (!data.content || !data.content.trim()) return;

    addStepCard({
      icon: "💡",
      title: "Model Reasoning",
      tool: "thought",
      summary: extractFirstLine(data.content, 90),
      details: data.content,
      collapsible: true,
    });
  });

  eventSource.addEventListener("tool_start", (e) => {
    const data = JSON.parse(e.data);
    const toolInfo = getToolMeta(data.name);

    let summaryText = "";
    if (data.args) {
      if (data.args.path) summaryText = `File: ${data.args.path}`;
      else if (data.args.filename) summaryText = `File: ${data.args.filename}`;
      else if (data.args.command) summaryText = `Cmd: ${data.args.command}`;
      else if (data.args.to_email || data.args.to) summaryText = `To: ${data.args.to_email || data.args.to}`;
      else summaryText = JSON.stringify(data.args);
    }

    activeStepCard = addStepCard({
      icon: toolInfo.icon,
      title: toolInfo.label,
      tool: data.name,
      summary: summaryText || "Executing action...",
      details: JSON.stringify(data.args, null, 2),
      isActive: true,
      collapsible: true,
    });
  });

  eventSource.addEventListener("tool_result", (e) => {
    const data = JSON.parse(e.data);
    const resultText = data.result || "";

    if (activeStepCard) {
      updateActiveCard(activeStepCard, resultText);
      activeStepCard = null;
    } else {
      const toolInfo = getToolMeta(data.name);
      addStepCard({
        icon: toolInfo.icon,
        title: `${toolInfo.label} Result`,
        tool: data.name,
        summary: extractFirstLine(resultText, 90),
        details: resultText,
        collapsible: true,
      });
    }

    updateTerminal(resultText);
    initFiles(); // Auto-refresh desktop files on each tool result
  });

  eventSource.addEventListener("done", (e) => {
    const data = JSON.parse(e.data);
    addStepCard({
      icon: "✅",
      title: "Task Finished",
      tool: "done",
      summary: data.summary || "All requested tasks finished successfully.",
      details: data.summary,
      isSuccess: true,
      collapsible: true,
    });

    document.getElementById("summary-content").textContent = data.summary;
    document.getElementById("step-indicator").className = "step-badge done";
    document.getElementById("step-indicator").textContent = "Completed";

    // Switch to summary tab automatically
    document.querySelector('[data-itab="summary-view"]').click();
    initFiles();
  });

  eventSource.addEventListener("error", (e) => {
    if (e.data) {
      try {
        const data = JSON.parse(e.data);
        addStepCard({
          icon: "⚠️",
          title: "Agent Error",
          tool: "thought",
          summary: data.message,
          details: data.message,
          isError: true,
          collapsible: true,
        });
      } catch (_) {}
    }
  });

  eventSource.addEventListener("end", () => {
    eventSource.close();
    resetRunButton();
    initFiles();
  });

  eventSource.onerror = () => {
    eventSource.close();
    resetRunButton();
  };
}

// ── Step Card Renderer & Updater ─────────────────────────────────────────────
function addStepCard({
  icon = "⚡",
  title = "Step",
  tool = "thought",
  summary = "",
  details = "",
  isActive = false,
  isError = false,
  isSuccess = false,
  collapsible = true,
}) {
  const container = document.getElementById("feed-container");
  const welcome = container.querySelector(".welcome-banner");
  if (welcome) welcome.remove();

  const card = document.createElement("div");
  let cardClass = "step-card";
  if (isActive) cardClass += " active";
  if (isError) cardClass += " error";
  if (isSuccess) cardClass += " success";
  card.className = cardClass;

  const timeStr = new Date().toLocaleTimeString();

  card.innerHTML = `
    <div class="step-header">
      <div class="step-info">
        <span class="step-icon">${icon}</span>
        <span class="step-title">${escapeHtml(title)}</span>
        <span class="tool-badge ${escapeHtml(tool)}">${escapeHtml(tool)}</span>
      </div>
      <div class="step-meta">
        <span class="step-time">${timeStr}</span>
        ${collapsible ? `<button class="step-toggle-btn" title="View complete code / output">▾ Details</button>` : ""}
      </div>
    </div>
    <div class="step-summary-bar">
      <span class="step-summary-text">${escapeHtml(summary || "Processing...")}</span>
      <span class="step-status-tag ${isError ? "err" : isActive ? "running" : "ok"}">${isError ? "Failed" : isActive ? "Running" : "OK"}</span>
    </div>
    ${collapsible ? `<pre class="step-details">${escapeHtml(details || "")}</pre>` : ""}
  `;

  // Toggle details on header or button click
  if (collapsible) {
    const header = card.querySelector(".step-header");
    const toggleBtn = card.querySelector(".step-toggle-btn");
    const toggleAction = (e) => {
      e.stopPropagation();
      card.classList.toggle("expanded");
      if (toggleBtn) {
        toggleBtn.textContent = card.classList.contains("expanded") ? "▴ Hide" : "▾ Details";
      }
    };
    header.addEventListener("click", toggleAction);
    if (toggleBtn) toggleBtn.addEventListener("click", toggleAction);
  }

  container.appendChild(card);
  autoScrollToBottom();
  return card;
}

// Fallback for any legacy calls
function addFeedItem(type, title, content, toolName = "") {
  return addStepCard({
    icon: type === "done" ? "✅" : type === "error" ? "❌" : "⚙️",
    title: title,
    tool: toolName || type,
    summary: extractFirstLine(content, 90),
    details: content,
    isError: type === "error",
    isSuccess: type === "done",
    collapsible: true,
  });
}

function updateActiveCard(card, resultText) {
  card.classList.remove("active");
  const isErr = resultText.includes("exit_code=1") || resultText.includes("exit_code=2") || resultText.includes("ERROR:") || resultText.includes("Traceback");

  if (isErr) card.classList.add("error");
  else card.classList.add("success");

  const summaryEl = card.querySelector(".step-summary-text");
  const tagEl = card.querySelector(".step-status-tag");
  const detailsEl = card.querySelector(".step-details");

  if (tagEl) {
    tagEl.className = `step-status-tag ${isErr ? "err" : "ok"}`;
    tagEl.textContent = isErr ? "Failed" : "Success";
  }

  if (summaryEl) {
    summaryEl.textContent = extractFirstLine(resultText, 100);
  }

  if (detailsEl) {
    detailsEl.textContent = `${detailsEl.textContent}\n\n=== Output ===\n${resultText}`;
  }

  autoScrollToBottom();
}

function autoScrollToBottom() {
  const chk = document.getElementById("auto-scroll-chk");
  if (!chk || chk.checked) {
    const container = document.getElementById("feed-container");
    container.scrollTop = container.scrollHeight;
  }
}

// ── UI Helpers ───────────────────────────────────────────────────────────────
function getToolMeta(name) {
  switch (name) {
    case "write_file":
      return { icon: "📝", label: "Write Code File" };
    case "run_python":
      return { icon: "▶️", label: "Execute Python" };
    case "create_document":
      return { icon: "📄", label: "Create Document" };
    case "send_email":
      return { icon: "📧", label: "Dispatch Email" };
    case "pip_install":
      return { icon: "📦", label: "Install Packages" };
    case "read_file":
      return { icon: "🔍", label: "Read File" };
    case "list_files":
      return { icon: "📂", label: "List Files" };
    case "done":
      return { icon: "✅", label: "Complete Task" };
    default:
      return { icon: "⚙️", label: name };
  }
}

function extractFirstLine(str, maxLen = 90) {
  if (!str) return "";
  const firstLine = str.trim().split("\n")[0].trim();
  if (firstLine.length > maxLen) {
    return firstLine.substring(0, maxLen) + "...";
  }
  return firstLine;
}

function updateTerminal(text) {
  const term = document.getElementById("terminal-content");
  currentOutput += (currentOutput ? "\n\n" : "") + text;
  term.textContent = currentOutput;
  term.scrollTop = term.scrollHeight;
}

function clearFeed() {
  document.getElementById("feed-container").innerHTML = `
    <div class="welcome-banner">
      <div class="welcome-icon">✨</div>
      <h3>Ready to assist you</h3>
      <p>Type a task instruction or select a preset on the left. The agent will write & execute Python code, create Word & PDF documents, manage files, and send emails autonomously.</p>
    </div>
  `;
  currentOutput = "";
  document.getElementById("terminal-content").textContent = "Waiting for execution output...";
  document.getElementById("step-indicator").className = "step-badge";
  document.getElementById("step-indicator").textContent = "Idle";
  document.getElementById("step-count-badge").style.display = "none";
}

function resetRunButton() {
  const runBtn = document.getElementById("btn-run-agent");
  runBtn.disabled = false;
  runBtn.innerHTML = '<span class="icon">🚀</span> Run Task';
}

async function unloadModel() {
  try {
    const res = await fetch("/api/unload", { method: "POST" });
    const data = await res.json();
    alert(data.message || "Model unloaded.");
  } catch (err) {
    alert("Failed to unload model.");
  }
}

function copyCurrentOutput() {
  const activeTab = document.querySelector(".itab-btn.active").dataset.itab;
  let text = "";
  if (activeTab === "output-view") text = document.getElementById("terminal-content").textContent;
  else if (activeTab === "code-view") text = document.getElementById("code-content").textContent;
  else if (activeTab === "summary-view") text = document.getElementById("summary-content").textContent;

  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById("btn-copy-output");
    const orig = btn.innerHTML;
    btn.innerHTML = "✅ Copied!";
    setTimeout(() => (btn.innerHTML = orig), 2000);
  });
}

function escapeHtml(str) {
  if (typeof str !== "string") str = String(str || "");
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
