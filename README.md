# DRL Coding Agent

Autonomous local AI coding and system assistant running directly on your laptop using **Ollama (CPU only)**.

---

## 🌟 Key Features

1. **Natural Language Coding**: Type an English prompt to write code, save it on Desktop/Downloads/Documents, install requirements, execute it, and save verified output.
2. **Project Folder Analysis & Auto-Run**: Name any folder, and the agent inspects code, detects dependencies, executes entry points, and displays execution results.
3. **Formatted Document Generation**: Generates structured Word documents (`.docx`) and PDF reports (`.pdf`) with headings, bullet points, and formatting.
4. **Local File Retrieval & Automated Emailing**: Finds existing files across Desktop, Downloads, Documents, or Pictures and sends them via Gmail SMTP with zero hallucinations.
5. **Remote PC Access & File Transfer (Over Local Wi-Fi with Consent)**: Connects to another laptop's consented file explorer over Wi-Fi using one-time PIN authorization, downloads files, and emails them directly.
6. **Complete Modern Web UI**: Real-time browser dashboard featuring live step-by-step streaming, terminal output viewer, code inspector, and Desktop file manager.

---

## 🚀 First-Time Setup on a New PC (Transfer Guide)

Follow these steps in order when transferring this project folder to a new computer or another person:

### Phase 1: Software Installations on the New PC (One-Time)

#### Step 1: Install Python (3.10 or higher)
1. Download Python from [python.org/downloads](https://www.python.org/downloads/).
2. Run the installer.
3. ⚠️ **VERY IMPORTANT:** On the very first installer screen, check the box:
   **`☑ Add python.exe to PATH`**
4. Click **Install Now**.

#### Step 2: Install Ollama (Local AI Engine)
1. Download Ollama from [ollama.com/download](https://ollama.com/download).
2. Run the installer and finish setup.
3. Ollama will start automatically in the Windows system tray (llama icon).

---

### Phase 2: Project Setup (Inside `drl_coding_agent` Folder)

Copy/Paste the `drl_coding_agent` folder to any location on the new PC (e.g. `C:\Users\<Name>\drl_coding_agent` or Desktop).

Open **PowerShell** or **Command Prompt** inside this project folder:
*(Tip: In Windows File Explorer, click the address bar, type `powershell` or `cmd`, and press Enter).*

#### Step 3: Install Required Python Libraries
Run this command in the terminal:
```powershell
pip install -r requirements.txt
```
*(Installs FastAPI, Uvicorn, python-docx, reportlab, python-dotenv, etc.)*

#### Step 4: Configure Email Settings (`.env`)
1. In the project folder, locate **`.env.example`**.
2. Make a copy of it and name it **`.env`** (or open `.env` in Notepad).
3. Fill in your Gmail address and 16-character App Password:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your_email@gmail.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

> **How to generate a 16-character Gmail App Password (1 minute):**
> 1. Go to Google Account Security: [myaccount.google.com/security](https://myaccount.google.com/security).
> 2. Ensure **2-Step Verification** is turned **ON**.
> 3. In the top search bar, search for **App passwords**.
> 4. Enter a name (e.g. `DRL Agent`) $\rightarrow$ Click **Create**.
> 5. Google generates a 16-character password (e.g. `abcd efgh ijkl mnop`).
> 6. Paste that code into `SMTP_PASSWORD` in `.env` (remove any spaces).

#### Step 5: Download & Setup the Local AI Model
Run this command in your terminal:
```powershell
python setup_model.py
```
*(Downloads the lightweight ~1GB `qwen2.5-coder` model into Ollama and registers it as `drl-coder`).*

---

## 💻 How to Run the Agent

You can interact with your agent in any of the 3 interfaces:

### Option 1: Modern Web UI (Recommended for Demos)
Double-click **`run_web.bat`**, or run:
```powershell
python web_server.py
```
* Open your browser at: **`http://127.0.0.1:8000`**
* Type any task into the chat box!

### Option 2: Direct Command Line Mode
Run tasks directly from PowerShell or Command Prompt:
```powershell
# Write code:
python agent.py "write python code for BFS algorithm and save it as bfs.py"

# Write, execute, and email code and output:
python agent.py "write python code for prime numbers, run it, save output as prime_op.txt, and email both files to friend@gmail.com"

# Local folder search and email:
python agent.py "go to downloads find text.py and send to email friend@gmail.com"
```

### Option 3: Interactive Terminal Shell
```powershell
python agent.py
```
Opens an interactive prompt where you can type tasks continuously:
```text
> write python code for A star algorithm
```

---

## 🌐 Remote PC Access & File Transfer (Over Local Wi-Fi)

To access files on a classmate's or professor's laptop (Laptop 2) and email them:

### 1. Network Connection (Bypassing College Wi-Fi AP Isolation)
College Wi-Fi networks block device-to-device communication. To bypass this:
* On **Laptop 1 (Your PC):** Open Windows **Settings** $\rightarrow$ **Network & internet** $\rightarrow$ **Mobile hotspot** $\rightarrow$ Turn **ON** *(Sharing from Wi-Fi)*.
* On **Laptop 2 (Other PC):** Connect to Laptop 1's hotspot Wi-Fi.

### 2. On Laptop 2 (Person 2):
1. Copy only **`remote_node.py`** and **`start_remote_node.bat`** to Laptop 2.
2. Double-click **`start_remote_node.bat`**.
3. Press **Enter** to share the `Documents` folder (or type `Desktop`).
4. Note down the **IP** (e.g. `192.168.137.50`) and **4-digit PIN** (e.g. `4821`).

### 3. On Laptop 1 (Your PC):
Ask the agent in terminal or Web UI:
```powershell
python agent.py "connect to remote pc 192.168.137.50 with pin 4821, find report.pdf and send to professor@university.edu"
```
Or browse their folders interactively using the CLI menu:
```powershell
python remote_cli.py
```

---

## 📋 Example Prompts Catalog

| Capability | Example Prompt |
| :--- | :--- |
| **Algorithms** | `"write python code for A star algorithm and save it as astar.py"` |
| **Code Execution** | `"run astar.py and save output as astar_op.txt"` |
| **Word Documents** | `"create a Word document on AI definition, advantages, and disadvantages in downloads as ai_overview.docx"` |
| **PDF Reports** | `"create a PDF document on Machine Learning types and save on Desktop as ml_report.pdf"` |
| **Local Email** | `"go to downloads find text.py and send to email test@gmail.com"` |
| **Formal Email** | `"send an email to supervisor@company.com requesting leave for 2 days due to fever"` |
| **Remote Browse** | `"connect to remote pc 192.168.137.50 with pin 4821 and list all files"` |
| **Remote Fetch & Mail** | `"connect to remote pc 192.168.137.50 pin 4821, find text.txt and send to test@gmail.com"` |
| **Full Pipeline** | `"write DFS algorithm, run it, save output as dfs_op.txt, and email both files to student@university.edu"` |

---

## 🛠️ Troubleshooting & FAQs

* **`HTTP Error 403: Forbidden` on Remote PC:**
  * PIN changed: Every time `start_remote_node.bat` is restarted, a fresh 4-digit PIN is generated. Enter the PIN currently showing on Laptop 2's screen.
  * System Junctions: Legacy Windows shortcuts like `My Music` or `My Videos` inside `Documents` point outside the shared folder and are blocked by the security sandbox. Only select real user files.
* **`Connection timed out` on Wi-Fi:**
  * Campus/College Wi-Fi has AP Client Isolation. Use Windows Mobile Hotspot on Laptop 1 and connect Laptop 2 to it.
* **Gmail SMTP Authentication Error:**
  * Ensure 2-Step Verification is enabled and you are using a 16-character Google **App Password**, not your regular Gmail account password.
* **Python Not Found:**
  * Reinstall Python and make sure to check **"Add python.exe to PATH"**.
