#!/usr/bin/env bash
# ============================================================
# TCC MVP - Script de Instalação e Verificação (Linux/macOS)
# Uso: bash install.sh
# ============================================================

set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC} $*"; }
warn() { echo -e "  ${YELLOW}[!!]${NC} $*"; }
fail() { echo -e "  ${RED}[XX]${NC} $*"; }
step() { echo -e "\n${CYAN}[==] $*${NC}"; }

echo "============================================"
echo "  TCC MVP - Instalação de Dependências"
echo "============================================"

# -----------------------------------------------------------
# 1. Python
# -----------------------------------------------------------
step "Verificando Python..."
if command -v python3 &>/dev/null; then
    ok "$(python3 --version)"
else
    fail "Python3 não encontrado. Instale com: sudo apt install python3 python3-pip"
    exit 1
fi

# -----------------------------------------------------------
# 2. pip install requirements
# -----------------------------------------------------------
step "Instalando dependências Python (requirements.txt)..."
PIP_ARGS=""
# Detect managed Python environments (Ubuntu 23.04+, Debian 12+)
if python3 -c "import sys; sys.exit(0 if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) else 1)" 2>/dev/null; then
    ok "Ambiente virtual detectado"
    PIP_ARGS=""
elif python3 -m pip install --dry-run streamlit 2>&1 | grep -q "externally-managed"; then
    warn "Ambiente gerenciado pelo sistema — usando --break-system-packages"
    PIP_ARGS="--break-system-packages"
fi

python3 -m pip install --upgrade pip --quiet $PIP_ARGS 2>/dev/null || true
python3 -m pip install -r requirements.txt --quiet $PIP_ARGS
ok "Dependências Python instaladas"

# -----------------------------------------------------------
# 3. Terraform
# -----------------------------------------------------------
step "Verificando Terraform..."
if command -v terraform &>/dev/null; then
    ok "$(terraform version | head -1)"
else
    warn "Terraform não encontrado."
    warn "Instale com: sudo snap install terraform --classic"
    warn "Ou: https://developer.hashicorp.com/terraform/downloads"
fi

# -----------------------------------------------------------
# 4. Ollama
# -----------------------------------------------------------
step "Verificando Ollama..."
if command -v ollama &>/dev/null; then
    ok "$(ollama --version 2>&1 || echo 'instalado')"
else
    warn "Ollama não encontrado."
    warn "Instale com: curl -fsSL https://ollama.com/install.sh | sh"
fi

if curl -s --max-time 3 http://localhost:11434 &>/dev/null; then
    ok "Ollama API respondendo em localhost:11434"
else
    warn "Ollama não está rodando. Execute 'ollama serve' em outro terminal."
fi

# -----------------------------------------------------------
# 5. Docker
# -----------------------------------------------------------
step "Verificando Docker..."
if command -v docker &>/dev/null; then
    ok "$(docker --version)"
    if docker info &>/dev/null 2>&1; then
        ok "Docker daemon está rodando"
    else
        warn "Docker instalado mas daemon não está rodando. Execute: sudo systemctl start docker"
    fi
else
    warn "Docker não encontrado. Instale com: sudo apt install docker.io"
fi

# -----------------------------------------------------------
# 6. Modelos Ollama sugeridos
# -----------------------------------------------------------
step "Verificando modelos Ollama disponíveis..."
MODELS=("codellama:7b" "deepseek-coder:6.7b" "qwen2.5-coder:7b" "llama3:8b")
if command -v ollama &>/dev/null && curl -s --max-time 2 http://localhost:11434 &>/dev/null; then
    MODEL_LIST=$(ollama list 2>&1 || echo "")
    for model in "${MODELS[@]}"; do
        base="${model%%:*}"
        if echo "$MODEL_LIST" | grep -q "$base"; then
            ok "$model disponível"
        else
            warn "$model não encontrado. Execute: ollama pull $model"
        fi
    done
else
    warn "Ollama não está rodando — não foi possível verificar modelos"
fi

# -----------------------------------------------------------
# 7. Criar .streamlit/config.toml se não existir
# -----------------------------------------------------------
step "Verificando configuração do Streamlit..."
if [ ! -f ".streamlit/config.toml" ]; then
    mkdir -p .streamlit
    cat > .streamlit/config.toml << 'TOML'
[server]
fileWatcherType = "none"
headless = true
enableWebsocketCompression = false
maxMessageSize = 200

[browser]
gatherUsageStats = false

[runner]
fastReruns = false
TOML
    ok "Criado .streamlit/config.toml"
else
    ok ".streamlit/config.toml já existe"
fi

# -----------------------------------------------------------
# Resumo
# -----------------------------------------------------------
echo ""
echo "============================================"
echo -e "  ${GREEN}Instalação concluída!${NC}"
echo "============================================"
echo ""
echo "Para iniciar o dashboard:"
echo -e "  ${GREEN}streamlit run dashboard.py${NC}"
echo ""
