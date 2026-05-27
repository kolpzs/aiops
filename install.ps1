# ============================================================
# TCC MVP - Script de Instalação e Verificação (Windows)
# Autor: TCC Automatizado
# Uso: .\install.ps1          (só verifica dependências)
#      .\install.ps1 -Launch  (verifica e inicia o dashboard)
# ============================================================

param(
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "TCC MVP - Instalação"

function Write-Step { param($msg) Write-Host "`n[==] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-WARN { param($msg) Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-FAIL { param($msg) Write-Host "  [XX] $msg"   -ForegroundColor Red }

Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  TCC MVP - Instalação de Dependências" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta

# -----------------------------------------------------------
# 1. Python
# -----------------------------------------------------------
Write-Step "Verificando Python..."
try {
    $pyVer = python --version 2>&1
    Write-OK "$pyVer"
} catch {
    Write-FAIL "Python não encontrado. Instale em https://python.org (marque 'Add to PATH')"
    exit 1
}

# -----------------------------------------------------------
# 2. pip install requirements
# -----------------------------------------------------------
Write-Step "Instalando dependências Python (requirements.txt)..."
try {
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt --quiet
    Write-OK "streamlit, plotly, psycopg2-binary instalados"
} catch {
    Write-FAIL "Erro ao instalar dependências: $_"
    exit 1
}

# -----------------------------------------------------------
# 3. Terraform
# -----------------------------------------------------------
Write-Step "Verificando Terraform..."
$tf = Get-Command terraform -ErrorAction SilentlyContinue
if ($tf) {
    $tfVer = terraform version -json 2>&1 | ConvertFrom-Json
    Write-OK "Terraform $($tfVer.terraform_version)"
} else {
    Write-WARN "Terraform não encontrado no PATH."
    Write-WARN "Instale via winget: winget install Hashicorp.Terraform"
    Write-WARN "Ou baixe em: https://developer.hashicorp.com/terraform/downloads"
}

# -----------------------------------------------------------
# 4. Ollama
# -----------------------------------------------------------
Write-Step "Verificando Ollama..."
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    $ollamaVer = ollama --version 2>&1
    Write-OK "Ollama: $ollamaVer"
} else {
    Write-WARN "Ollama não encontrado."
    Write-WARN "Instale em: https://ollama.com/download"
}

# Verificar se serviço Ollama responde
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-OK "Ollama API respondendo em localhost:11434"
} catch {
    Write-WARN "Ollama não está rodando. Execute 'ollama serve' antes de usar o dashboard."
}

# -----------------------------------------------------------
# 5. Docker
# -----------------------------------------------------------
Write-Step "Verificando Docker..."
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    $dockerVer = docker --version 2>&1
    Write-OK "Docker: $dockerVer"
    # Verificar Docker daemon
    try {
        docker info 2>&1 | Out-Null
        Write-OK "Docker daemon está rodando"
    } catch {
        Write-WARN "Docker instalado mas daemon não está rodando. Abra o Docker Desktop."
    }
} else {
    Write-WARN "Docker não encontrado. Instale o Docker Desktop em: https://docs.docker.com/desktop/windows/"
}

# -----------------------------------------------------------
# 6. Modelos Ollama sugeridos
# -----------------------------------------------------------
Write-Step "Verificando modelos Ollama disponíveis..."
try {
    $models = ollama list 2>&1
    $suggestedModels = @("codellama:7b", "deepseek-coder:6.7b", "qwen2.5-coder:7b", "llama3:8b")
    foreach ($model in $suggestedModels) {
        if ($models -match $model.Split(":")[0]) {
            Write-OK "$model disponível"
        } else {
            Write-WARN "$model não encontrado. Execute: ollama pull $model"
        }
    }
} catch {
    Write-WARN "Não foi possível listar modelos (Ollama não está rodando?)"
}

# -----------------------------------------------------------
# 7. Criar .streamlit/config.toml se não existir
# -----------------------------------------------------------
Write-Step "Verificando configuração do Streamlit..."
$configDir  = ".streamlit"
$configFile = ".streamlit\config.toml"
if (-not (Test-Path $configFile)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    @"
[server]
fileWatcherType = "none"
headless = true
enableWebsocketCompression = false
maxMessageSize = 200

[browser]
gatherUsageStats = false

[runner]
fastReruns = false
"@ | Set-Content -Path $configFile -Encoding UTF8
    Write-OK "Criado .streamlit\config.toml"
} else {
    Write-OK ".streamlit\config.toml já existe"
}

# -----------------------------------------------------------
# Resumo
# -----------------------------------------------------------
Write-Host "`n============================================" -ForegroundColor Magenta
Write-Host "  Instalação concluída!" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Para iniciar o dashboard:" -ForegroundColor White
Write-Host "  streamlit run dashboard.py" -ForegroundColor Green
Write-Host ""

if ($Launch) {
    Write-Host "Iniciando dashboard..." -ForegroundColor Cyan
    streamlit run dashboard.py
}
