# ============================================================
# TCC MVP - Script de Instalação e Verificação (Windows)
# Uso: .\install.ps1          (só verifica dependências)
#      .\install.ps1 -Launch  (verifica e inicia o dashboard)
#
# Se der erro de política de execução, rode:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================================

param(
    [switch]$Launch
)

# Continue (não aborta no primeiro erro de cmdlet)
$ErrorActionPreference = "Continue"

# Muda para o diretório onde o script está localizado
Set-Location -Path $PSScriptRoot

# Título do console (ignorar erro em terminais que não suportam)
try { $Host.UI.RawUI.WindowTitle = "TCC MVP - Instalação" } catch {}

function Write-Step { param($msg) Write-Host "`n[==] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-WARN { param($msg) Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-FAIL { param($msg) Write-Host "  [XX] $msg"   -ForegroundColor Red }

Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  TCC MVP - Instalacao de Dependencias" -ForegroundColor Magenta
Write-Host "  Diretorio: $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "============================================" -ForegroundColor Magenta

# -----------------------------------------------------------
# 1. Python — tenta 'python', depois 'py' (Windows launcher)
# -----------------------------------------------------------
Write-Step "Verificando Python..."
$pythonCmd = $null

foreach ($cmd in @("python", "py", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        # Confirma que é Python real (não o alias da Microsoft Store)
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3") {
            $pythonCmd = $cmd
            Write-OK "$ver (usando '$cmd')"
            break
        }
    }
}

if (-not $pythonCmd) {
    Write-FAIL "Python 3 nao encontrado. Instale em https://python.org (marque 'Add to PATH')"
    Write-FAIL "Ou via winget: winget install Python.Python.3"
    exit 1
}

# -----------------------------------------------------------
# 2. pip install requirements.txt
# -----------------------------------------------------------
Write-Step "Instalando dependencias Python (requirements.txt)..."

if (-not (Test-Path "requirements.txt")) {
    Write-FAIL "requirements.txt nao encontrado em: $PSScriptRoot"
    exit 1
}

& $pythonCmd -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $pythonCmd -m pip install -r requirements.txt --quiet 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-FAIL "Erro ao instalar dependencias (exit code $LASTEXITCODE)"
    Write-WARN "Tente rodar manualmente: $pythonCmd -m pip install -r requirements.txt"
    exit 1
}
Write-OK "streamlit, plotly, psycopg2-binary instalados"

# -----------------------------------------------------------
# 3. Terraform
# -----------------------------------------------------------
Write-Step "Verificando Terraform..."
$tf = Get-Command terraform -ErrorAction SilentlyContinue
if ($tf) {
    $tfOut = terraform version 2>&1 | Select-Object -First 1
    Write-OK "$tfOut"
} else {
    Write-WARN "Terraform nao encontrado no PATH."
    Write-WARN "Instale via winget : winget install Hashicorp.Terraform"
    Write-WARN "Ou baixe em        : https://developer.hashicorp.com/terraform/downloads"
}

# -----------------------------------------------------------
# 4. Ollama
# -----------------------------------------------------------
Write-Step "Verificando Ollama..."
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    $ollamaVer = ollama --version 2>&1 | Select-Object -First 1
    Write-OK "Ollama: $ollamaVer"
} else {
    Write-WARN "Ollama nao encontrado."
    Write-WARN "Instale em: https://ollama.com/download"
}

# Verifica se a API do Ollama responde
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-OK "Ollama API respondendo em localhost:11434"
} catch {
    Write-WARN "Ollama nao esta rodando. Execute 'ollama serve' antes de usar o dashboard."
}

# -----------------------------------------------------------
# 5. Docker
# -----------------------------------------------------------
Write-Step "Verificando Docker..."
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCmd) {
    $dockerVer = docker --version 2>&1 | Select-Object -First 1
    Write-OK "Docker: $dockerVer"

    # Verifica Docker daemon pelo exit code (try/catch nao funciona com exes nativos)
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Docker daemon esta rodando"
    } else {
        Write-WARN "Docker instalado mas daemon nao esta rodando. Abra o Docker Desktop."
    }
} else {
    Write-WARN "Docker nao encontrado. Instale o Docker Desktop em:"
    Write-WARN "  https://docs.docker.com/desktop/windows/"
}

# -----------------------------------------------------------
# 6. Modelos Ollama sugeridos
# -----------------------------------------------------------
Write-Step "Verificando modelos Ollama disponiveis..."
if ($ollamaCmd) {
    $modelList = ollama list 2>&1
    if ($LASTEXITCODE -eq 0) {
        $suggestedModels = @("codellama:7b", "deepseek-coder:6.7b", "qwen2.5-coder:7b", "llama3:8b")
        foreach ($model in $suggestedModels) {
            $base = $model.Split(":")[0]
            if ($modelList -match [regex]::Escape($base)) {
                Write-OK "$model disponivel"
            } else {
                Write-WARN "$model nao encontrado. Execute: ollama pull $model"
            }
        }
    } else {
        Write-WARN "Nao foi possivel listar modelos (Ollama nao esta rodando?)"
    }
} else {
    Write-WARN "Ollama nao instalado — pulando verificacao de modelos"
}

# -----------------------------------------------------------
# 7. Criar .streamlit/config.toml (evita tela branca e loops)
# -----------------------------------------------------------
Write-Step "Verificando configuracao do Streamlit..."
$configDir  = Join-Path $PSScriptRoot ".streamlit"
$configFile = Join-Path $configDir "config.toml"

if (-not (Test-Path $configFile)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    # Nota: "@ deve estar no inicio da linha sem espacos
    $tomlContent = @"
[server]
fileWatcherType = "none"
headless = true
enableWebsocketCompression = false
maxMessageSize = 200

[browser]
gatherUsageStats = false

[runner]
fastReruns = false
"@
    # Use .NET to write UTF-8 WITHOUT BOM — PowerShell 5.x Set-Content adds BOM
    # which can cause TOML parse errors in some tools.
    [System.IO.File]::WriteAllText($configFile, $tomlContent, [System.Text.UTF8Encoding]::new($false))
    Write-OK "Criado .streamlit\config.toml"
} else {
    Write-OK ".streamlit\config.toml ja existe"
}

# -----------------------------------------------------------
# Resumo final
# -----------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  Instalacao concluida!" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Para iniciar o dashboard:" -ForegroundColor White
Write-Host "  streamlit run dashboard.py" -ForegroundColor Green
Write-Host ""
Write-Host "Ou use o atalho:" -ForegroundColor White
Write-Host "  .\install.ps1 -Launch" -ForegroundColor Green
Write-Host ""

if ($Launch) {
    Write-Host "[==] Iniciando dashboard..." -ForegroundColor Cyan
    & $pythonCmd -m streamlit run "$PSScriptRoot\dashboard.py"
}
