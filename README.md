# AIOPs — Análise de Logs em IaC com IA Generativa

> **Análise estruturada de logs em Infraestrutura como Código: um método baseado em Inteligência Artificial Generativa para otimização DevOps.**

## O que é

MVP que demonstra em laboratório local:

1. Execução de cenários Terraform com falhas controladas
2. Captura automática do log técnico
3. Injeção de contexto em um LLM local (Ollama)
4. Geração de relatórios com causa raiz, correção e avaliação de segurança

## Pré-requisitos

| Ferramenta | Versão mínima | Instalação |
|---|---|---|
| Python | 3.11+ | Já incluso na maioria das distros Linux |
| Terraform | 1.5+ | https://developer.hashicorp.com/terraform/install |
| Ollama | 0.2+ | `curl -fsSL https://ollama.com/install.sh \| sh` |

## Instalação rápida

```bash
git clone https://github.com/kolpzs/tcc-mvp.git
cd tcc-mvp
pip install -r requirements.txt
```

Baixe um modelo para o Ollama:

```bash
ollama pull qwen2.5-coder:7b
```

## Como usar

### Dashboard Web (recomendado)

```bash
streamlit run dashboard.py
```

O dashboard abre em `http://localhost:8501` e permite:

- **▶️ Iniciar / ⏹️ Parar** o Ollama direto pela interface
- **Executar cenários** individualmente com um clique
- **Ver logs do Terraform** em tempo real
- **Acompanhar a IA respondendo** token por token (streaming)
- **Ajustar timeout** pelo slider (30s a 600s)
- **Descarregar modelo** da RAM quando terminar
- **Consultar relatórios** salvos direto no painel

### Linha de comando

```bash
# Sem IA (só captura logs)
python3 analisador.py --skip-llm

# Com IA local
python3 analisador.py --scenario 01-sintaxe --model qwen2.5-coder:7b

# Cenário específico
python3 analisador.py --scenario 02-ciclo --ollama-timeout 300
```

## Cenários incluídos

| Cenário | Tipo de falha | Etapa |
|---|---|---|
| `01-sintaxe` | Erro de sintaxe HCL | `init` |
| `02-ciclo` | Dependência cíclica | `validate` |
| `03-nome-invalido` | Validação de nome | `plan` |

## Estrutura

```
tcc-mvp/
├── analisador.py         # Script CLI principal
├── dashboard.py          # Dashboard web (Streamlit)
├── requirements.txt      # Dependências Python
├── cenarios/
│   ├── 01-sintaxe/
│   ├── 02-ciclo/
│   └── 03-nome-invalido/
└── relatorios/           # Gerado automaticamente
```

## Enquadramento no TCC

**Problema:** Como um método estruturado de análise de logs via IA Generativa pode auxiliar na identificação da causa raiz em falhas de IaC?

**Hipóteses:**
- **H1:** Método estruturado reduz esforço de troubleshooting vs. análise manual
- **H2:** LLMs contextualizados identificam causa raiz e sugerem correção segura

**Critérios de avaliação:**
- **Assertividade** — a IA identificou a causa raiz correta?
- **Aderência à documentação** — a correção faz sentido para o Terraform?
- **Segurança** — evita atalhos inseguros?

## Licença

Projeto acadêmico — uso educacional.
