#!/usr/bin/env python3
"""
Dashboard visual do MVP - TCC
Análise Estruturada de Logs em IaC com IA Generativa

Uso:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "cenarios"
REPORTS_DIR = ROOT / "relatorios"

# ---------------------------------------------------------------------------
# TCC constants
# ---------------------------------------------------------------------------
RESEARCH_PROBLEM = (
    "Como um método estruturado de análise de logs via IA Generativa pode auxiliar "
    "na identificação da causa raiz e na proposição de correções para falhas de "
    "provisionamento em ferramentas de infraestrutura como código?"
)

GENERAL_OBJECTIVE = (
    "Propor um método estruturado de interpretação de logs, utilizando Inteligência "
    "Artificial Generativa, para identificar a causa raiz e sugerir correções em "
    "falhas de provisionamento de infraestrutura como código, visando otimizar o "
    "fluxo de trabalho em operações DevOps."
)

HYPOTHESES = [
    ("H1", "A aplicação de um método estruturado de IA Generativa na leitura de logs "
           "reduz o esforço de troubleshooting em comparação com a análise manual."),
    ("H2", "Modelos de linguagem contextualizados com o código e o log técnico conseguem "
           "identificar a causa raiz e sugerir uma refatoração segura e aderente."),
]

EVALUATION_CRITERIA = [
    ("Assertividade", "Se a resposta identifica corretamente a causa raiz e a etapa da falha."),
    ("Aderência à documentação", "Se a correção proposta faz sentido para o Terraform."),
    ("Segurança", "Se a sugestão evita atalhos inseguros e não mascara erros."),
]


# ---------------------------------------------------------------------------
# Ollama management
# ---------------------------------------------------------------------------
def find_ollama_binary() -> str | None:
    """Locate the ollama binary."""
    found = shutil.which("ollama")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "ollama"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def check_ollama(url: str) -> tuple[bool, str]:
    base = url.split("/api/")[0] if "/api/" in url else url.rstrip("/")
    version_url = f"{base}/api/version"
    try:
        req = urllib.request.Request(version_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return True, body.get("version", "desconhecida")
    except Exception:
        return False, ""


def get_ollama_pid() -> int | None:
    """Return PID of running ollama serve, or None."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "ollama serve"],
            capture_output=True, text=True, check=False,
        )
        pids = out.stdout.strip().split()
        return int(pids[0]) if pids else None
    except Exception:
        return None


def start_ollama(binary: str) -> tuple[bool, str]:
    """Start ollama serve in background."""
    if check_ollama("http://127.0.0.1:11434")[0]:
        return True, "Já está rodando."
    try:
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(15):
            time.sleep(1)
            if check_ollama("http://127.0.0.1:11434")[0]:
                return True, "Iniciado com sucesso."
        return False, "Ollama iniciou mas não respondeu em 15s."
    except Exception as exc:
        return False, f"Falha ao iniciar: {exc}"


def stop_ollama() -> tuple[bool, str]:
    """Stop ollama serve."""
    pid = get_ollama_pid()
    if pid is None:
        return True, "Já estava parado."
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            if get_ollama_pid() is None:
                return True, "Parado com sucesso."
        return False, f"Processo {pid} não terminou em 5s."
    except Exception as exc:
        return False, f"Erro ao parar: {exc}"


def list_ollama_models(binary: str) -> list[str]:
    try:
        out = subprocess.run(
            [binary, "list"], capture_output=True, text=True, check=False,
        )
        lines = out.stdout.strip().split("\n")[1:]  # skip header
        return [line.split()[0] for line in lines if line.strip()]
    except Exception:
        return []


def unload_model(model: str, binary: str) -> tuple[bool, str]:
    try:
        subprocess.run([binary, "stop", model], capture_output=True, text=True, check=False)
        return True, f"Modelo {model} descarregado da memória."
    except Exception as exc:
        return False, f"Erro: {exc}"


# ---------------------------------------------------------------------------
# Terraform + LLM helpers
# ---------------------------------------------------------------------------
def load_scenarios() -> list[dict]:
    scenarios = []
    if not SCENARIOS_DIR.exists():
        return scenarios
    for d in sorted(SCENARIOS_DIR.iterdir()):
        cfg = d / "cenario.json"
        if not cfg.exists():
            continue
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["slug"] = d.name
        data["path"] = str(d)
        data["tf_code"] = (d / "main.tf").read_text(encoding="utf-8")
        scenarios.append(data)
    return scenarios


def run_terraform_step(scenario_path: str, step: str) -> dict:
    commands = {
        "init": ["terraform", "init", "-input=false", "-no-color"],
        "validate": ["terraform", "validate", "-no-color"],
        "plan": ["terraform", "plan", "-input=false", "-no-color"],
        "apply": ["terraform", "apply", "-input=false", "-auto-approve", "-no-color"],
    }
    cmd = commands[step]
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    result = subprocess.run(cmd, cwd=scenario_path, capture_output=True, text=True, env=env, check=False)
    return {
        "step": step,
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def build_prompt(scenario: dict, tf_code: str, exec_log: str) -> str:
    criteria = "\n".join(f"- {n}: {d}" for n, d in EVALUATION_CRITERIA)
    hyps = "\n".join(f"- {c}: {t}" for c, t in HYPOTHESES)
    return textwrap.dedent(f"""\
        Atue como um Engenheiro DevOps Sênior.

        Você recebeu um cenário de laboratório de TCC com Terraform e o log da execução.
        Analise apenas o contexto abaixo e responda em português.

        Contexto da pesquisa:
        - Problema: {RESEARCH_PROBLEM}
        - Objetivo geral: {GENERAL_OBJECTIVE}
        - Hipóteses:
        {hyps}
        - Critérios qualitativos:
        {criteria}

        Entregue exatamente estas seções:
        1. CAUSA RAIZ
        2. CORREÇÃO
        3. TRECHO DE CÓDIGO SUGERIDO
        4. AVALIAÇÃO DE SEGURANÇA
        5. RELAÇÃO COM OS CRITÉRIOS DO EXPERIMENTO

        Regras:
        - Não invente dependências externas.
        - Não sugira expor segredos ou desabilitar validações.
        - Baseie a resposta somente no código e no log fornecidos.

        [CENÁRIO]
        {scenario['slug']} - {scenario['title']}

        [DESCRIÇÃO]
        {scenario['description']}

        [CÓDIGO TERRAFORM]
        {tf_code}

        [LOG DE EXECUÇÃO]
        {exec_log}
    """).strip()


def call_ollama_stream(prompt: str, model: str, url: str, timeout: int):
    """Generator that yields tokens as they arrive from Ollama."""
    payload = json.dumps({
        "model": model,
        "stream": True,
        "prompt": prompt,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buffer = b""
        for chunk in iter(lambda: resp.read(1), b""):
            if not chunk:
                break
            buffer += chunk
            if chunk == b"\n":
                try:
                    obj = json.loads(buffer.decode("utf-8"))
                    token = obj.get("response", "")
                    if token:
                        yield token
                    if obj.get("done", False):
                        return
                except json.JSONDecodeError:
                    pass
                buffer = b""


def save_report(scenario: dict, status: str, tf_code: str, exec_log: str, ai_response: str | None) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{scenario['slug']}.md"
    ai_section = ai_response or "IA não executada nesta rodada."
    content = "\n".join([
        f"# Relatório - {scenario['slug']}",
        "",
        f"**Título:** {scenario['title']}  ",
        f"**Status:** {status}",
        "",
        "## Código Terraform",
        "",
        "```hcl",
        tf_code.rstrip(),
        "```",
        "",
        "## Log capturado",
        "",
        "```text",
        exec_log.rstrip(),
        "```",
        "",
        "## Análise da IA",
        "",
        ai_section,
        "",
    ])
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Custom CSS for responsiveness
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
    /* Responsiveness */
    .block-container {
        max-width: 1200px;
        padding: 1rem 1.5rem;
    }
    @media (max-width: 768px) {
        .block-container { padding: 0.5rem; }
        [data-testid="stSidebar"] { min-width: 250px; }
    }

    /* Status cards */
    .status-card {
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
    .status-online { background: rgba(0, 200, 83, 0.1); border-color: rgba(0, 200, 83, 0.3); }
    .status-offline { background: rgba(255, 82, 82, 0.1); border-color: rgba(255, 82, 82, 0.3); }

    /* Pipeline steps */
    .pipeline-step {
        padding: 0.5rem 1rem;
        border-radius: 8px;
        margin: 0.25rem 0;
        font-family: monospace;
    }
    .step-success { background: rgba(0, 200, 83, 0.1); }
    .step-fail { background: rgba(255, 82, 82, 0.1); }
    .step-wait { background: rgba(255, 193, 7, 0.1); }
</style>
"""


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="TCC MVP — Análise de Logs IaC + IA",
        page_icon="🔬",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    ollama_bin = find_ollama_binary()

    # ===== SIDEBAR =====
    with st.sidebar:
        st.header("⚙️ Configuração")
        ollama_url = st.text_input("URL do Ollama", value="http://127.0.0.1:11434/api/generate")
        model = st.text_input("Modelo", value="qwen2.5-coder:7b")
        timeout = st.slider(
            "Timeout (s)", min_value=30, max_value=600, value=180, step=30,
            help="Tempo máximo para a resposta da IA",
        )

        st.divider()

        # --- Ollama controls ---
        st.header("📡 Controle do Ollama")
        is_online, version = check_ollama(ollama_url)

        if is_online:
            st.markdown(
                f'<div class="status-card status-online">'
                f'✅ <strong>Online</strong> — v{version}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="status-card status-offline">'
                '❌ <strong>Offline</strong></div>',
                unsafe_allow_html=True,
            )

        if ollama_bin is None:
            st.warning("⚠️ Binário do Ollama não encontrado. Instale com:\n\n"
                       "`curl -fsSL https://ollama.com/install.sh | sh`")
        else:
            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("▶️ Iniciar", key="btn_start_ollama", use_container_width=True,
                              disabled=is_online):
                    with st.spinner("Iniciando..."):
                        ok, msg = start_ollama(ollama_bin)
                    if ok:
                        st.success(msg)
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(msg)

            with col_stop:
                if st.button("⏹️ Parar", key="btn_stop_ollama", use_container_width=True,
                              disabled=not is_online):
                    with st.spinner("Parando..."):
                        ok, msg = stop_ollama()
                    if ok:
                        st.success(msg)
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(msg)

            if is_online:
                models = list_ollama_models(ollama_bin)
                if models:
                    st.caption(f"Modelos disponíveis: {', '.join(models)}")
                    if st.button("🧹 Descarregar modelo da RAM", key="btn_unload",
                                 use_container_width=True):
                        ok, msg = unload_model(model, ollama_bin)
                        st.info(msg) if ok else st.error(msg)

            st.button("🔄 Atualizar status", key="btn_refresh", use_container_width=True,
                       on_click=lambda: None)

        st.divider()
        st.header("🔬 Sobre o TCC")
        st.markdown(f"**Problema:** {RESEARCH_PROBLEM}")
        with st.expander("Hipóteses"):
            for code, text in HYPOTHESES:
                st.markdown(f"**{code}:** {text}")
        with st.expander("Critérios de avaliação"):
            for name, desc in EVALUATION_CRITERIA:
                st.markdown(f"**{name}:** {desc}")

    # ===== MAIN AREA =====
    st.title("🔬 Análise Estruturada de Logs em IaC com IA Generativa")
    st.caption("MVP do TCC — Laboratório de Falhas Controladas com Terraform + Ollama")

    # Check terraform
    if not shutil.which("terraform"):
        st.error(
            "❌ **Terraform não encontrado no PATH.** "
            "Instale em https://developer.hashicorp.com/terraform/install"
        )
        return

    scenarios = load_scenarios()
    if not scenarios:
        st.error("Nenhum cenário encontrado em `cenarios/`.")
        return

    # --- Tabs per scenario ---
    tabs = st.tabs([f"📁 {s['slug']}" for s in scenarios])

    for tab, scenario in zip(tabs, scenarios):
        with tab:
            # Info section
            st.subheader(f"📋 {scenario['title']}")

            info_col, action_col = st.columns([3, 2])

            with info_col:
                st.markdown(scenario["description"])
                st.markdown(f"**Relação com TCC:** {scenario.get('tcc_relation', 'N/A')}")
                if scenario.get("presentation_note"):
                    st.info(f"💡 **Nota:** {scenario['presentation_note']}")

            with action_col:
                with st.expander("📄 Código Terraform", expanded=False):
                    st.code(scenario["tf_code"], language="hcl")

            st.markdown("---")

            # Run button
            run_btn = st.button(
                f"▶️ Executar cenário {scenario['slug']}",
                key=f"run_{scenario['slug']}",
                type="primary",
                use_container_width=True,
            )

            if run_btn:
                # --- Phase 1: Terraform ---
                st.markdown("### 🔧 Fase 1 — Execução do Terraform")
                pipeline_status = st.status("Executando pipeline Terraform...", expanded=True)

                failed_step = None
                exec_log_parts = []

                for step in scenario["steps"]:
                    pipeline_status.write(f"⏳ `terraform {step}`...")
                    result = run_terraform_step(scenario["path"], step)

                    log_block = (
                        f"$ {result['command']}\n"
                        f"[exit_code={result['returncode']}]\n"
                        f"--- STDOUT ---\n{result['stdout'].rstrip() or '(vazio)'}\n"
                        f"--- STDERR ---\n{result['stderr'].rstrip() or '(vazio)'}"
                    )
                    exec_log_parts.append(log_block)

                    if result["returncode"] != 0:
                        pipeline_status.write(f"❌ Falha em `terraform {step}` (exit {result['returncode']})")
                        failed_step = step
                        break
                    else:
                        pipeline_status.write(f"✅ `terraform {step}` OK")

                exec_log = "\n\n".join(exec_log_parts)
                status_str = f"failure-captured:{failed_step}" if failed_step else "unexpected-success"

                if failed_step:
                    pipeline_status.update(label=f"✅ Falha capturada: {failed_step}", state="complete")
                else:
                    pipeline_status.update(label="⚠️ Nenhuma falha (inesperado)", state="error")

                with st.expander("📋 Log completo", expanded=True):
                    st.code(exec_log, language="text")

                # --- Phase 2: AI ---
                st.markdown("### 🤖 Fase 2 — Análise com IA Generativa")

                ok_now, _ = check_ollama(ollama_url)
                if not ok_now:
                    st.warning(
                        "⚠️ Ollama offline. Clique **▶️ Iniciar** na sidebar e tente novamente."
                    )
                    save_report(scenario, status_str, scenario["tf_code"], exec_log, None)
                    st.info(f"📄 Relatório salvo (sem IA) em `relatorios/{scenario['slug']}.md`")
                else:
                    prompt = build_prompt(scenario, scenario["tf_code"], exec_log)

                    with st.expander("📝 Prompt enviado à IA", expanded=False):
                        st.code(prompt, language="text")

                    ai_status = st.status("Aguardando resposta da IA...", expanded=True)
                    ai_placeholder = st.empty()
                    full_response = ""

                    try:
                        t0 = time.time()
                        for token in call_ollama_stream(prompt, model, ollama_url, timeout):
                            full_response += token
                            ai_placeholder.markdown(full_response + "▌")
                        elapsed = time.time() - t0

                        ai_placeholder.markdown(full_response)
                        ai_status.update(label=f"✅ Concluída em {elapsed:.0f}s", state="complete")

                        rp = save_report(scenario, status_str, scenario["tf_code"], exec_log, full_response)
                        st.success(f"📄 Relatório salvo em `{rp.relative_to(ROOT)}`")

                    except (TimeoutError, socket.timeout):
                        ai_status.update(label=f"⏱️ Timeout ({timeout}s)", state="error")
                        st.error(f"Timeout de {timeout}s atingido. Aumente na sidebar ou use modelo menor.")
                    except Exception as exc:
                        ai_status.update(label="❌ Erro", state="error")
                        st.error(f"Erro: {exc}")

    # --- Saved reports ---
    st.divider()
    st.subheader("📂 Relatórios salvos")
    if REPORTS_DIR.exists():
        reports = sorted(REPORTS_DIR.glob("*.md"))
        if reports:
            for rp in reports:
                with st.expander(f"📄 {rp.name}"):
                    st.markdown(rp.read_text(encoding="utf-8"))
        else:
            st.caption("Nenhum relatório gerado ainda.")
    else:
        st.caption("Pasta de relatórios não existe.")

    # --- Footer ---
    st.divider()
    st.caption(
        "TCC — Análise Estruturada de Logs em Infraestrutura como Código: "
        "Um Método Baseado em IA Generativa para Otimização DevOps"
    )


if __name__ == "__main__":
    main()
