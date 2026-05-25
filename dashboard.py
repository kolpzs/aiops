#!/usr/bin/env python3
"""
Dashboard visual do MVP - TCC
Análise Estruturada de Logs em IaC com IA Generativa

Uso:
    streamlit run dashboard.py
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "cenarios"
REPORTS_DIR = ROOT / "relatorios"
CSV_FILE = REPORTS_DIR / "resultados.csv"

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

CSV_HEADERS = [
    "timestamp", "cenario", "titulo", "modelo", "timeout_config",
    "etapa_falha", "status", "tempo_terraform_s", "tempo_ia_s",
    "tokens_estimados", "ia_executada", "relatorio_path",
]


# ---------------------------------------------------------------------------
# Ollama management
# ---------------------------------------------------------------------------
def find_ollama_binary() -> str | None:
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
        lines = out.stdout.strip().split("\n")[1:]
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


def _docker_cmd() -> list[str]:
    """Return docker command with correct socket if Docker Desktop context is active."""
    default_sock = "/var/run/docker.sock"
    desktop_sock = os.path.expanduser("~/.docker/desktop/docker.sock")
    # Se Docker Desktop estiver ativo, o Terraform usa o socket padrão
    if os.path.exists(desktop_sock) and os.path.exists(default_sock):
        return ["docker", "-H", f"unix://{default_sock}"]
    return ["docker"]


def get_docker_container_logs(container_name: str) -> str:
    """Capture logs from a Docker container (running or stopped)."""
    try:
        cmd = _docker_cmd() + ["logs", container_name]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, check=False, timeout=10,
        )
        logs = ""
        if result.stdout.strip():
            logs += f"--- STDOUT ---\n{result.stdout.strip()}\n"
        if result.stderr.strip():
            logs += f"--- STDERR ---\n{result.stderr.strip()}\n"
        return logs or "(sem logs)"
    except Exception:
        return "(não foi possível capturar logs do container)"


def terraform_cleanup(scenario_path: str) -> str:
    """Run terraform destroy to clean up resources."""
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    result = subprocess.run(
        ["terraform", "destroy", "-auto-approve", "-no-color"],
        cwd=scenario_path, capture_output=True, text=True, env=env, check=False, timeout=60,
    )
    if result.returncode == 0:
        return "✅ Recursos limpos com terraform destroy"
    return f"⚠️ Cleanup parcial (exit {result.returncode})"


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
# CSV results tracking
# ---------------------------------------------------------------------------
def append_csv_result(row: dict) -> None:
    """Append a result row to the CSV file, creating it if needed."""
    REPORTS_DIR.mkdir(exist_ok=True)
    file_exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_csv_results() -> list[dict]:
    """Load all CSV results."""
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Scenario execution (reusable for single + batch)
# ---------------------------------------------------------------------------
def execute_scenario(
    scenario: dict,
    model: str,
    ollama_url: str,
    timeout: int,
    skip_llm: bool,
    log_container,
    progress_text=None,
) -> dict:
    """
    Run a single scenario end-to-end. Writes live output to log_container.
    Returns a result dict suitable for CSV.
    """
    log_container.markdown(f"#### 📁 Cenário: **{scenario['slug']}** — {scenario['title']}")

    # Phase 1: Terraform
    log_container.markdown("**🔧 Terraform pipeline**")
    t0_tf = time.time()
    failed_step = None
    exec_log_parts = []

    for step in scenario["steps"]:
        log_container.write(f"⏳ `terraform {step}`...")
        result = run_terraform_step(scenario["path"], step)
        log_block = (
            f"$ {result['command']}\n"
            f"[exit_code={result['returncode']}]\n"
            f"--- STDOUT ---\n{result['stdout'].rstrip() or '(vazio)'}\n"
            f"--- STDERR ---\n{result['stderr'].rstrip() or '(vazio)'}"
        )
        exec_log_parts.append(log_block)

        if result["returncode"] != 0:
            log_container.write(f"❌ Falha em `terraform {step}` (exit {result['returncode']})")
            failed_step = step
            break
        else:
            log_container.write(f"✅ `terraform {step}` OK")

    tf_elapsed = time.time() - t0_tf
    exec_log = "\n\n".join(exec_log_parts)
    status_str = f"failure-captured:{failed_step}" if failed_step else "unexpected-success"

    # Check for Docker container logs (scenarios 04+)
    uses_docker = "docker" in scenario.get("tf_code", "").lower() and "docker_container" in scenario.get("tf_code", "")
    container_logs = ""
    if uses_docker:
        log_container.write("🐳 Verificando logs dos containers...")
        time.sleep(4)  # Aguarda container crashar
        container_names = re.findall(r'name\s*=\s*"(tcc-mvp-[^"]+)"', scenario.get("tf_code", ""))
        for cname in container_names:
            clogs = get_docker_container_logs(cname)
            if clogs and clogs != "(sem logs)":
                container_logs += f"\n[CONTAINER: {cname}]\n{clogs}"
        if container_logs:
            exec_log += f"\n\n--- LOGS DOS CONTAINERS ---{container_logs}"
            log_container.markdown("**🐳 Logs dos containers Docker capturados**")
            # Se Terraform passou mas container crashou, marcar como falha de aplicação
            if not failed_step and ("FATAL" in container_logs or "Error" in container_logs or "error" in container_logs):
                status_str = "app-failure-captured:container-crash"
                log_container.success("✅ Falha de aplicação detectada via logs do container")

    if failed_step:
        log_container.success(f"✅ Falha capturada na etapa: **{failed_step}** ({tf_elapsed:.1f}s)")
    elif "app-failure" in status_str:
        pass  # já mostrou a mensagem acima
    else:
        log_container.warning("⚠️ Nenhuma falha detectada (inesperado)")

    # Phase 2: AI
    ai_response = None
    ai_elapsed = 0.0
    tokens_est = 0

    if not skip_llm:
        ok_now, _ = check_ollama(ollama_url)
        if not ok_now:
            log_container.warning("⚠️ Ollama offline — relatório salvo sem IA.")
        else:
            log_container.markdown("**🤖 Análise com IA...**")
            ai_placeholder = log_container.empty()
            prompt = build_prompt(scenario, scenario["tf_code"], exec_log)
            full_response = ""

            try:
                t0_ai = time.time()
                for token in call_ollama_stream(prompt, model, ollama_url, timeout):
                    full_response += token
                    ai_placeholder.markdown(full_response + "▌")
                ai_elapsed = time.time() - t0_ai
                ai_placeholder.markdown(full_response)
                ai_response = full_response
                tokens_est = len(full_response.split())
                log_container.success(f"✅ IA concluída em {ai_elapsed:.0f}s (~{tokens_est} tokens)")
            except (TimeoutError, socket.timeout):
                log_container.error(f"⏱️ Timeout ({timeout}s)")
            except Exception as exc:
                log_container.error(f"❌ Erro: {exc}")
    else:
        log_container.info("ℹ️ IA ignorada (modo --skip-llm)")

    report_path = save_report(scenario, status_str, scenario["tf_code"], exec_log, ai_response)
    log_container.info(f"📄 Relatório: `{report_path.relative_to(ROOT)}`")

    # Cleanup Docker resources
    if uses_docker:
        log_container.write("🧹 Limpando recursos Docker...")
        cleanup_msg = terraform_cleanup(scenario["path"])
        log_container.write(cleanup_msg)

    csv_row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cenario": scenario["slug"],
        "titulo": scenario["title"],
        "modelo": model if not skip_llm else "N/A",
        "timeout_config": timeout,
        "etapa_falha": failed_step or "nenhuma",
        "status": status_str,
        "tempo_terraform_s": round(tf_elapsed, 2),
        "tempo_ia_s": round(ai_elapsed, 2),
        "tokens_estimados": tokens_est,
        "ia_executada": "sim" if ai_response else "nao",
        "relatorio_path": str(report_path.relative_to(ROOT)),
    }
    append_csv_result(csv_row)
    return csv_row


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
    .block-container { max-width: 1200px; padding: 1rem 1.5rem; }
    @media (max-width: 768px) {
        .block-container { padding: 0.5rem; }
        [data-testid="stSidebar"] { min-width: 250px; }
    }
    .status-card {
        border-radius: 10px; padding: 1rem; margin: 0.5rem 0;
        border: 1px solid rgba(128,128,128,0.2);
    }
    .status-online { background: rgba(0,200,83,0.1); border-color: rgba(0,200,83,0.3); }
    .status-offline { background: rgba(255,82,82,0.1); border-color: rgba(255,82,82,0.3); }
    .metric-card {
        text-align: center; padding: 1rem; border-radius: 10px;
        border: 1px solid rgba(128,128,128,0.2); margin: 0.25rem;
    }
</style>
"""


# ---------------------------------------------------------------------------
# UI: Sidebar
# ---------------------------------------------------------------------------
def render_sidebar():
    with st.sidebar:
        st.header("⚙️ Configuração")
        ollama_url = st.text_input("URL do Ollama", value="http://127.0.0.1:11434/api/generate")
        model = st.text_input("Modelo", value="qwen2.5-coder:1.5b")
        timeout = st.slider("Timeout (s)", 30, 600, 300, 30, help="Tempo máximo para resposta da IA")

        st.divider()
        st.header("📡 Controle do Ollama")
        is_online, version = check_ollama(ollama_url)
        ollama_bin = find_ollama_binary()

        if is_online:
            st.markdown(f'<div class="status-card status-online">✅ <strong>Online</strong> — v{version}</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-card status-offline">❌ <strong>Offline</strong></div>',
                        unsafe_allow_html=True)

        if ollama_bin is None:
            st.warning("⚠️ Ollama não encontrado.\n\n`curl -fsSL https://ollama.com/install.sh | sh`")
        else:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("▶️ Iniciar", key="sb_start", use_container_width=True, disabled=is_online):
                    with st.spinner("Iniciando..."):
                        ok, msg = start_ollama(ollama_bin)
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        time.sleep(1)
                        st.rerun()
            with c2:
                if st.button("⏹️ Parar", key="sb_stop", use_container_width=True, disabled=not is_online):
                    with st.spinner("Parando..."):
                        ok, msg = stop_ollama()
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        time.sleep(1)
                        st.rerun()

            if is_online:
                models = list_ollama_models(ollama_bin)
                if models:
                    st.caption(f"Modelos: {', '.join(models)}")
                    if st.button("🧹 Descarregar da RAM", key="sb_unload", use_container_width=True):
                        ok, msg = unload_model(model, ollama_bin)
                        st.info(msg) if ok else st.error(msg)

            st.button("🔄 Atualizar", key="sb_refresh", use_container_width=True, on_click=lambda: None)

        st.divider()
        st.header("🔬 Sobre o TCC")
        st.markdown(f"**Problema:** {RESEARCH_PROBLEM}")
        with st.expander("Hipóteses"):
            for code, text in HYPOTHESES:
                st.markdown(f"**{code}:** {text}")
        with st.expander("Critérios de avaliação"):
            for name, desc in EVALUATION_CRITERIA:
                st.markdown(f"**{name}:** {desc}")

    return ollama_url, model, timeout, is_online


# ---------------------------------------------------------------------------
# UI: Tab - Individual scenario
# ---------------------------------------------------------------------------
def render_scenario_tab(scenario: dict, model: str, ollama_url: str, timeout: int):
    st.subheader(f"📋 {scenario['title']}")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(scenario["description"])
        st.markdown(f"**Relação com TCC:** {scenario.get('tcc_relation', 'N/A')}")
        if scenario.get("presentation_note"):
            st.info(f"💡 {scenario['presentation_note']}")
    with c2:
        with st.expander("📄 Código Terraform", expanded=False):
            st.code(scenario["tf_code"], language="hcl")

    st.markdown("---")

    skip_llm = st.checkbox("Pular IA (só Terraform)", key=f"skip_{scenario['slug']}")
    run_btn = st.button(
        f"▶️ Executar {scenario['slug']}",
        key=f"run_{scenario['slug']}",
        type="primary",
        use_container_width=True,
    )

    if run_btn:
        log_area = st.container()
        with log_area:
            execute_scenario(scenario, model, ollama_url, timeout, skip_llm, st)


# ---------------------------------------------------------------------------
# UI: Tab - Run All
# ---------------------------------------------------------------------------
def render_run_all_tab(scenarios: list[dict], model: str, ollama_url: str, timeout: int):
    st.subheader("🚀 Executar todos os cenários")
    st.markdown("Executa todos os cenários em sequência, mostrando progresso em tempo real.")

    skip_llm = st.checkbox("Pular IA (só Terraform)", key="skip_all")

    run_all_btn = st.button(
        "▶️ Executar TODOS os cenários",
        key="run_all",
        type="primary",
        use_container_width=True,
    )

    if run_all_btn:
        total = len(scenarios)
        progress_bar = st.progress(0, text=f"Preparando... 0/{total}")
        results = []

        for idx, scenario in enumerate(scenarios):
            progress_bar.progress(
                (idx) / total,
                text=f"Executando {scenario['slug']}... ({idx + 1}/{total})"
            )

            with st.expander(f"📁 {scenario['slug']} — {scenario['title']}", expanded=True):
                result = execute_scenario(
                    scenario, model, ollama_url, timeout, skip_llm, st,
                )
                results.append(result)

        progress_bar.progress(1.0, text=f"✅ Concluído! {total}/{total} cenários")

        # Summary table
        st.markdown("### 📊 Resumo da execução")
        summary_data = []
        for r in results:
            summary_data.append({
                "Cenário": r["cenario"],
                "Etapa Falha": r["etapa_falha"],
                "Terraform (s)": r["tempo_terraform_s"],
                "IA (s)": r["tempo_ia_s"],
                "Tokens": r["tokens_estimados"],
                "Status": "✅" if r["status"].startswith("failure-captured") else "⚠️",
            })
        st.dataframe(summary_data, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# UI: Tab - Results & Charts
# ---------------------------------------------------------------------------
def render_results_tab():
    st.subheader("📊 Resultados e Gráficos")

    data = load_csv_results()
    if not data:
        st.info("Nenhuma execução registrada ainda. Execute cenários para gerar dados.")
        return

    # Convert to use in charts
    st.markdown("### 📋 Histórico de execuções")
    st.dataframe(data, use_container_width=True, hide_index=True)

    # Download CSV
    csv_content = CSV_FILE.read_text(encoding="utf-8")
    st.download_button(
        "⬇️ Baixar CSV completo",
        data=csv_content,
        file_name="resultados_tcc_mvp.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.markdown("---")

    # Filter only rows with AI executed
    ai_rows = [r for r in data if r.get("ia_executada") == "sim"]
    all_rows = data

    # --- Metrics ---
    st.markdown("### 📈 Métricas gerais")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Total execuções", len(all_rows))
    with m2:
        st.metric("Com IA", len(ai_rows))
    with m3:
        if ai_rows:
            avg_ai = sum(float(r["tempo_ia_s"]) for r in ai_rows) / len(ai_rows)
            st.metric("Tempo médio IA (s)", f"{avg_ai:.1f}")
        else:
            st.metric("Tempo médio IA (s)", "N/A")
    with m4:
        if ai_rows:
            avg_tokens = sum(int(r["tokens_estimados"]) for r in ai_rows) / len(ai_rows)
            st.metric("Tokens médio", f"{avg_tokens:.0f}")
        else:
            st.metric("Tokens médio", "N/A")

    st.markdown("---")

    # --- Chart: Tempo por cenário ---
    if ai_rows:
        st.markdown("### ⏱️ Tempo de resposta da IA por cenário")

        chart_data_time = {}
        for r in ai_rows:
            name = r["cenario"]
            t = float(r["tempo_ia_s"])
            if name not in chart_data_time:
                chart_data_time[name] = []
            chart_data_time[name].append(t)

        bar_data = {name: sum(vals) / len(vals) for name, vals in chart_data_time.items()}
        st.bar_chart(bar_data, horizontal=True)

        # --- Chart: Tokens por cenário ---
        st.markdown("### 📝 Tokens estimados por cenário")
        chart_data_tokens = {}
        for r in ai_rows:
            name = r["cenario"]
            tok = int(r["tokens_estimados"])
            if name not in chart_data_tokens:
                chart_data_tokens[name] = []
            chart_data_tokens[name].append(tok)

        bar_tokens = {name: sum(vals) / len(vals) for name, vals in chart_data_tokens.items()}
        st.bar_chart(bar_tokens, horizontal=True)

    # --- Chart: Timeline ---
    if len(all_rows) > 1:
        st.markdown("### 📅 Evolução temporal")
        timeline = []
        for r in all_rows:
            timeline.append({
                "timestamp": r["timestamp"],
                "Terraform (s)": float(r["tempo_terraform_s"]),
                "IA (s)": float(r["tempo_ia_s"]),
            })
        st.line_chart(
            data={
                "Terraform (s)": [t["Terraform (s)"] for t in timeline],
                "IA (s)": [t["IA (s)"] for t in timeline],
            }
        )

    # --- Comparison table ---
    if ai_rows:
        st.markdown("### 🔍 Comparativo detalhado (execuções com IA)")
        compare = []
        for r in ai_rows:
            compare.append({
                "Data": r["timestamp"][:19],
                "Cenário": r["cenario"],
                "Modelo": r["modelo"],
                "Timeout (s)": r["timeout_config"],
                "Terraform (s)": r["tempo_terraform_s"],
                "IA (s)": r["tempo_ia_s"],
                "Tokens": r["tokens_estimados"],
            })
        st.dataframe(compare, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# UI: Tab - Reports
# ---------------------------------------------------------------------------
def render_reports_tab():
    st.subheader("📂 Relatórios salvos")

    if not REPORTS_DIR.exists():
        st.caption("Pasta de relatórios não existe.")
        return

    reports = sorted(REPORTS_DIR.glob("*.md"))
    if not reports:
        st.caption("Nenhum relatório gerado ainda.")
        return

    for rp in reports:
        with st.expander(f"📄 {rp.name}"):
            st.markdown(rp.read_text(encoding="utf-8"))

    # Download all reports as zip-like concatenation
    st.markdown("---")
    all_reports = "\n\n---\n\n".join(
        rp.read_text(encoding="utf-8") for rp in reports
    )
    st.download_button(
        "⬇️ Baixar todos os relatórios (.md)",
        data=all_reports,
        file_name="relatorios_tcc_mvp.md",
        mime="text/markdown",
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="TCC MVP — Análise de Logs IaC + IA",
        page_icon="🔬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    ollama_url, model, timeout, is_online = render_sidebar()

    st.title("🔬 Análise Estruturada de Logs em IaC com IA Generativa")
    st.caption("MVP do TCC — Laboratório de Falhas Controladas com Terraform + Ollama")

    if not shutil.which("terraform"):
        st.error("❌ Terraform não encontrado no PATH.")
        return

    scenarios = load_scenarios()
    if not scenarios:
        st.error("Nenhum cenário encontrado em `cenarios/`.")
        return

    # Build tab list
    tab_names = [f"📁 {s['slug']}" for s in scenarios]
    tab_names.append("🚀 Executar Todos")
    tab_names.append("📊 Resultados")
    tab_names.append("📂 Relatórios")

    tabs = st.tabs(tab_names)

    # Individual scenario tabs
    for i, scenario in enumerate(scenarios):
        with tabs[i]:
            render_scenario_tab(scenario, model, ollama_url, timeout)

    # Run All tab
    with tabs[len(scenarios)]:
        render_run_all_tab(scenarios, model, ollama_url, timeout)

    # Results tab
    with tabs[len(scenarios) + 1]:
        render_results_tab()

    # Reports tab
    with tabs[len(scenarios) + 2]:
        render_reports_tab()

    # Footer
    st.divider()
    st.caption(
        "TCC — Análise Estruturada de Logs em Infraestrutura como Código: "
        "Um Método Baseado em IA Generativa para Otimização DevOps"
    )


if __name__ == "__main__":
    main()
