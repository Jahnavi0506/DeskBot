# Download and silently install Ollama (Windows). The official installer is ~1.5GB.
$ErrorActionPreference = "Stop"
$dest = Join-Path $env:TEMP "OllamaSetup.exe"
Write-Host "Downloading OllamaSetup.exe (about 1.5GB)..."
curl.exe -L --retry 3 --retry-all-errors -o $dest "https://ollama.com/download/OllamaSetup.exe"
Write-Host "Installing silently..."
Start-Process -FilePath $dest -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-" -Wait
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (Test-Path $ollama) {
  Write-Host "Installed at $ollama"
  & $ollama --version
} else {
  Write-Host "Installer finished. Open Ollama from the Start menu if the API is not up yet."
}
