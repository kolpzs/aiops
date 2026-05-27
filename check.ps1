# ============================================================
# TCC MVP - Script de Validação de Dependências (Windows)
# Autor: TCC Automatizado
# Uso: .\check.ps1
#      .\check.ps1 -Verbose   (mostra detalhes extras)
#      powershell -ExecutionPolicy Bypass -File check.ps1
# ============================================================

param(
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"
try { $Host.UI.RawUI.WindowTitle = "TCC MVP - Verificação" } catch {}

# Garante que caminhos relativos funcionam independente de onde o script é chamado
Set-Location -Path $PSScriptRoot

# ---- Helpers -----------------------------------------------
function Write-Step { param($msg) Write-Host "`n[==] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-WARN { param($msg) Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-FAIL { param($msg) Write-Host "  [XX] $msg" -ForegroundColor Red }

$global:PassCount = 0
$global:FailCount = 0
$global:WarnCount = 0

function Mark-Pass { param($msg) $global:PassCount++; Write-OK $msg }
function Mark-Fail { param($msg) $global:FailCount++; Write-FAIL $msg }
function Mark-Warn { param($msg) $global:WarnCount++; Write-WARN $msg }

# ============================================================
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  TCC MVP - Verificacao de Dependencias" -ForegroundColor Magenta
Write-Host "  Diretorio: $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "============================================" -ForegroundColor Magenta

# -----------------------------------------------------------
# 1. Python 3
# -----------------------------------------------------------
Write-Step "Python 3"
$pythonCmd = $null
foreach ($cmd in @("python", "py", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3") {
            $pythonCmd = $cmd
            Mark-Pass "$ver  (comando: '$cmd')"
            break
        } elseif ($Verbose) {
            Write-Host "  [..] '$cmd' encontrado mas retornou: $ver" -ForegroundColor DarkGray
        }
    }
}
if (-not $pythonCmd) {
    Mark-Fail "Python 3 nao encontrado no PATH"
    Write-WARN "  Instale em: https://python.org  (marque 'Add to PATH')"
    Write-WARN "  Ou via winget: winget install Python.Python.3"
}

# -----------------------------------------------------------
# 2. Pacotes Python (pip show — sem precisar importar)
# -----------------------------------------------------------
Write-Step "Pacotes Python"
if ($pythonCmd) {
    $requiredPackages = @{
        "streamlit"       = "streamlit"
        "plotly"          = "plotly"
        "psycopg2-binary" = "psycopg2"   # pip show usa o nome do pacote, import usa psycopg2
    }
    foreach ($pkg in $requiredPackages.GetEnumerator()) {
        $pipName   = $pkg.Key
        $importName = $pkg.Value
        $info = & $pythonCmd -m pip show $pipName 2>&1
        if ($LASTEXITCODE -eq 0 -and "$info" -match "Name:") {
            $verLine = ($info | Select-String "^Version:") -replace "Version:\s*", ""
            Mark-Pass "$pipName $verLine"
        } else {
            Mark-Fail "$pipName nao instalado"
            Write-WARN "  Execute: $pythonCmd -m pip install $pipName"
        }
    }
} else {
    Mark-Warn "Python nao disponivel — pacotes nao verificados"
}

# -----------------------------------------------------------
# 3. Terraform
# -----------------------------------------------------------
Write-Step "Terraform"
if (Get-Command terraform -ErrorAction SilentlyContinue) {
    $tfOut = terraform version 2>&1 | Select-Object -First 1
    Mark-Pass "$tfOut"
} else {
    Mark-Fail "Terraform nao encontrado no PATH"
    Write-WARN "  winget install Hashicorp.Terraform"
    Write-WARN "  https://developer.hashicorp.com/terraform/downloads"
}

# -----------------------------------------------------------
# 4. Ollama (binário)
# -----------------------------------------------------------
Write-Step "Ollama"
$ollamaInstalled = $false
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $ollamaVer = ollama --version 2>&1 | Select-Object -First 1
    Mark-Pass "Binario: $ollamaVer"
    $ollamaInstalled = $true
} else {
    Mark-Fail "Ollama nao encontrado no PATH"
    Write-WARN "  Instale em: https://ollama.com/download"
}

# Verificar se o serviço está respondendo (independente do binário estar no PATH)
try {
    $null = Invoke-WebRequest -Uri "http://localhost:11434" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Mark-Pass "API respondendo em localhost:11434"
} catch {
    Mark-Warn "API nao esta respondendo em localhost:11434"
    Write-WARN "  Execute 'ollama serve' (ou abra o Ollama Desktop) antes de usar o dashboard"
}

# -----------------------------------------------------------
# 5. Modelos Ollama
# -----------------------------------------------------------
Write-Step "Modelos Ollama"
if ($ollamaInstalled) {
    $modelList = ollama list 2>&1
    if ($LASTEXITCODE -eq 0) {
        $suggestedModels = @(
            "codellama:7b",
            "deepseek-coder:6.7b",
            "qwen2.5-coder:7b",
            "llama3:8b"
        )
        $foundAny = $false
        foreach ($model in $suggestedModels) {
            $base = $model.Split(":")[0]
            if ($modelList -match [regex]::Escape($base)) {
                Mark-Pass "$model disponivel"
                $foundAny = $true
            } else {
                Mark-Warn "$model nao encontrado  ->  ollama pull $model"
            }
        }
        if (-not $foundAny) {
            Write-WARN "  Nenhum modelo sugerido encontrado. Baixe ao menos um."
        }
        if ($Verbose) {
            Write-Host "`n  Modelos instalados:" -ForegroundColor DarkGray
            $modelList | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
    } else {
        Mark-Warn "Nao foi possivel listar modelos (Ollama nao esta rodando?)"
    }
} else {
    Mark-Warn "Ollama nao instalado — modelos nao verificados"
}

# -----------------------------------------------------------
# 6. Docker
# -----------------------------------------------------------
Write-Step "Docker"
$dockerInstalled = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $dockerVer = docker --version 2>&1 | Select-Object -First 1
    Mark-Pass "Binario: $dockerVer"
    $dockerInstalled = $true
} else {
    Mark-Fail "Docker nao encontrado no PATH"
    Write-WARN "  Instale o Docker Desktop: https://docs.docker.com/desktop/windows/"
}

if ($dockerInstalled) {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Mark-Pass "Docker daemon esta rodando"
    } else {
        Mark-Warn "Docker instalado mas daemon nao esta rodando"
        Write-WARN "  Abra o Docker Desktop e aguarde iniciar"
    }
}

# -----------------------------------------------------------
# 7. Arquivos do projeto
# -----------------------------------------------------------
Write-Step "Arquivos do Projeto"
$requiredFiles = @(
    "dashboard.py",
    "analisador.py",
    "requirements.txt",
    "lab-tcc-terraform\main.tf"
)
foreach ($f in $requiredFiles) {
    $full = Join-Path $PSScriptRoot $f
    if (Test-Path $full) {
        Mark-Pass "$f"
    } else {
        Mark-Fail "$f nao encontrado"
    }
}

# Config Streamlit
$configFile = Join-Path $PSScriptRoot ".streamlit\config.toml"
if (Test-Path $configFile) {
    Mark-Pass ".streamlit\config.toml"
} else {
    Mark-Warn ".streamlit\config.toml nao encontrado"
    Write-WARN "  Execute .\install.ps1 para criar a configuracao"
}

# -----------------------------------------------------------
# Resumo Final
# -----------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  RESUMO DA VERIFICACAO" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  OK     : $global:PassCount verificacao(es) passaram" -ForegroundColor Green
if ($global:WarnCount -gt 0) {
    Write-Host "  AVISO  : $global:WarnCount aviso(s)" -ForegroundColor Yellow
}
if ($global:FailCount -gt 0) {
    Write-Host "  FALHA  : $global:FailCount item(s) nao encontrado(s)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Execute .\install.ps1 para instalar dependencias faltantes." -ForegroundColor White
} else {
    Write-Host ""
    Write-Host "  Ambiente pronto! Para iniciar:" -ForegroundColor Green
    Write-Host "  streamlit run dashboard.py" -ForegroundColor Cyan
}
Write-Host ""

# Retorna exit code 1 se houver falhas (util em CI/CD)
if ($global:FailCount -gt 0) { exit 1 } else { exit 0 }
