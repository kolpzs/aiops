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
import logging
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
import datetime as _dt_module
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Terminal logger — always visible in the terminal running Streamlit
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("aiops")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "cenarios"
REPORTS_DIR = ROOT / "relatorios"
CSV_FILE = REPORTS_DIR / "resultados.csv"

# 3 modelos focados em código/infra, de empresas distintas, ~7B params
KNOWN_MODELS = [
    "qwen2.5-coder:7b",      # Alibaba (Qwen) — análise e geração de código
    "deepseek-coder:6.7b",   # DeepSeek — code review, bug fixing
    "codellama:7b",           # Meta — variante do Llama otimizada para código
]

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
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
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
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# PostgreSQL helpers (optional persistence)
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tcc_resultados (
    id SERIAL PRIMARY KEY,
    timestamp TEXT,
    cenario TEXT,
    titulo TEXT,
    modelo TEXT,
    timeout_config INTEGER,
    etapa_falha TEXT,
    status TEXT,
    tempo_terraform_s REAL,
    tempo_ia_s REAL,
    tokens_estimados INTEGER,
    ia_executada TEXT,
    relatorio_path TEXT
);
"""


def check_postgres(dsn: str):
    """Test PG connection, create table if needed. Returns (ok, message)."""
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        conn.close()
        return True, "ok"
    except Exception as e:
        return False, str(e)


def append_postgres_result(dsn: str, row: dict) -> None:
    """Insert a result row into PostgreSQL if connection is available."""
    if not dsn:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["%s"] * len(row))
        cur.execute(f"INSERT INTO tcc_resultados ({cols}) VALUES ({placeholders})", list(row.values()))
        conn.commit()
        conn.close()
        logger.info("🐘 Resultado inserido no PostgreSQL")
    except Exception as e:
        logger.warning(f"⚠️ Falha ao salvar no PostgreSQL: {e}")


def load_csv_results() -> list[dict]:
    """Load all CSV results."""
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Activity Log (session-based)
# ---------------------------------------------------------------------------
def _init_activity_log():
    """Initialize the activity log in session state."""
    if "activity_log" not in st.session_state:
        st.session_state.activity_log = []


def add_activity(icon: str, message: str):
    """Add an entry to the activity log (UI + terminal)."""
    _init_activity_log()
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.activity_log.insert(0, {"time": ts, "icon": icon, "message": message})
    st.session_state.activity_log = st.session_state.activity_log[:200]
    logger.info(f"{icon} {message}")


def render_activity_log_tab():
    """Render the activity log tab."""
    _init_activity_log()
    st.subheader("📋 Log de Atividades")
    st.caption("Registro em tempo real de todas as ações realizadas nesta sessão.")

    if not st.session_state.activity_log:
        st.info("Nenhuma atividade registrada ainda. Execute cenários ou interaja com o dashboard.")
        return

    if st.button("🗑️ Limpar log", key="clear_log"):
        st.session_state.activity_log = []
        st.rerun()

    # Render as a scrollable container with clear separation between entries
    log_text = "\n\n".join(
        f"`{entry['time']}` {entry['icon']} {entry['message']}"
        for entry in st.session_state.activity_log
    )
    st.markdown(log_text)


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
    pg_dsn: str = "",
) -> dict:
    """
    Run a single scenario end-to-end. Writes live output to log_container.
    Returns a result dict suitable for CSV.
    """
    log_container.markdown(f"#### 📁 Cenário: **{scenario['slug']}** — {scenario['title']}")
    add_activity("📁", f"Iniciando cenário: {scenario['slug']}")

    # Phase 1: Terraform
    log_container.markdown("**🔧 Terraform pipeline**")
    add_activity("🔧", f"[{scenario['slug']}] Terraform pipeline iniciado")
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
            add_activity("❌", f"[{scenario['slug']}] terraform {step} — FALHOU (exit {result['returncode']})")
            failed_step = step
            break
        else:
            log_container.write(f"✅ `terraform {step}` OK")
            add_activity("✅", f"[{scenario['slug']}] terraform {step} — OK")

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
            add_activity("⚠️", f"[{scenario['slug']}] Ollama offline — IA ignorada")
        else:
            log_container.markdown("**🤖 Análise com IA em andamento...**")
            add_activity("🤖", f"[{scenario['slug']}] Enviando prompt para {model}...")
            prompt = build_prompt(scenario, scenario["tf_code"], exec_log)
            full_response = ""

            # Section tracking for live progress
            _SECTIONS = [
                ("CAUSA RAIZ", "🔍 Identificando causa raiz..."),
                ("CORREÇÃO", "🔧 Gerando correção..."),
                ("TRECHO DE CÓDIGO SUGERIDO", "💻 Escrevendo código corrigido..."),
                ("AVALIAÇÃO DE SEGURANÇA", "🔒 Avaliando segurança..."),
                ("RELAÇÃO COM OS CRITÉRIOS", "📊 Relacionando com critérios do TCC..."),
            ]
            sections_found: list[str] = []

            # Live status area
            status_box = log_container.empty()
            progress_bar = log_container.progress(0, text="Aguardando modelo carregar na memória...")
            section_status = log_container.empty()
            ai_placeholder = log_container.empty()
            stats_placeholder = log_container.empty()

            def _update_live_display(elapsed: float, tok_count: int, current_section: str):
                """Update the live metrics display."""
                tok_per_sec = tok_count / elapsed if elapsed > 0 else 0
                pct = min(elapsed / timeout, 0.99)
                progress_bar.progress(pct, text=f"⏱️ {elapsed:.0f}s / {timeout}s  |  📝 {tok_count} tokens  |  ⚡ {tok_per_sec:.1f} tok/s")

                # Build section checklist
                checklist_lines = []
                for sec_key, sec_label in _SECTIONS:
                    if sec_key in sections_found:
                        checklist_lines.append(f"✅ ~~{sec_label}~~")
                    elif sec_key == current_section:
                        checklist_lines.append(f"⏳ **{sec_label}**")
                    else:
                        checklist_lines.append(f"⬜ {sec_label}")
                section_status.markdown("\n".join(checklist_lines))

            try:
                t0_ai = time.time()
                current_sec = ""
                token_count = 0
                last_ui_update = 0.0

                for token in call_ollama_stream(prompt, model, ollama_url, timeout):
                    full_response += token
                    token_count += 1

                    # Detect which section we're in
                    for sec_key, _ in _SECTIONS:
                        if sec_key in full_response and sec_key not in sections_found:
                            if current_sec and current_sec != sec_key:
                                sections_found.append(current_sec)
                            current_sec = sec_key

                    # Throttle UI updates to every ~0.3s to avoid flickering
                    now = time.time()
                    if now - last_ui_update > 0.3:
                        elapsed = now - t0_ai
                        _update_live_display(elapsed, token_count, current_sec)
                        ai_placeholder.markdown(full_response + "▌")
                        last_ui_update = now

                # Mark last section done
                if current_sec and current_sec not in sections_found:
                    sections_found.append(current_sec)

                ai_elapsed = time.time() - t0_ai
                ai_placeholder.markdown(full_response)
                ai_response = full_response
                tokens_est = token_count
                tok_s = tokens_est / ai_elapsed if ai_elapsed > 0 else 0

                # Final state
                progress_bar.progress(1.0, text=f"✅ Concluído em {ai_elapsed:.0f}s  |  {tokens_est} tokens  |  {tok_s:.1f} tok/s")
                sections_found = [s for s, _ in _SECTIONS]  # all done
                _update_live_display(ai_elapsed, tokens_est, "")
                status_box.success(f"✅ IA concluída em {ai_elapsed:.0f}s — {tokens_est} tokens ({tok_s:.1f} tok/s)")
                add_activity("🤖", f"[{scenario['slug']}] IA concluída — {tokens_est} tokens em {ai_elapsed:.0f}s ({tok_s:.1f} tok/s)")

            except (TimeoutError, socket.timeout):
                elapsed = time.time() - t0_ai
                progress_bar.progress(1.0, text=f"⏱️ Timeout após {elapsed:.0f}s")
                log_container.error(f"⏱️ Timeout ({timeout}s). Resposta parcial ({token_count} tokens) salva.")
                add_activity("⏱️", f"[{scenario['slug']}] IA timeout após {elapsed:.0f}s ({token_count} tokens parciais)")
                if full_response:
                    ai_response = full_response + "\n\n*(resposta interrompida por timeout)*"
                    tokens_est = token_count
            except Exception as exc:
                log_container.error(f"❌ Erro: {exc}")
                add_activity("💥", f"[{scenario['slug']}] Erro na IA: {exc}")
    else:
        log_container.info("ℹ️ IA ignorada (modo --skip-llm)")
        add_activity("⏭️", f"[{scenario['slug']}] IA ignorada (skip-llm)")

    report_path = save_report(scenario, status_str, scenario["tf_code"], exec_log, ai_response)
    log_container.info(f"📄 Relatório: `{report_path.relative_to(ROOT)}`")
    add_activity("📄", f"[{scenario['slug']}] Relatório salvo: {report_path.name}")

    # Cleanup Docker resources
    if uses_docker:
        log_container.write("🧹 Limpando recursos Docker...")
        add_activity("🧹", f"[{scenario['slug']}] Limpando recursos Docker")
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
    if pg_dsn:
        append_postgres_result(pg_dsn, csv_row)
        add_activity("🐘", f"[{scenario['slug']}] Resultado salvo no PostgreSQL")
    add_activity("🧪", f"Cenário '{scenario['slug']}' executado — {status_str}")
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

        # Model dropdown — lists installed Ollama models, falls back to text input
        _bin = find_ollama_binary()
        installed_models = list_ollama_models(_bin) if _bin else []
        model_options = sorted(set(installed_models + KNOWN_MODELS)) + ["✏️ Digitar manualmente..."]
        default_idx = model_options.index("qwen2.5-coder:1.5b") if "qwen2.5-coder:1.5b" in model_options else 0
        selected = st.selectbox("Modelo", options=model_options, index=default_idx, key="model_select")
        if selected == "✏️ Digitar manualmente...":
            model = st.text_input("Nome do modelo (ex: phi3:mini)", key="model_custom", placeholder="modelo:tag")
        else:
            model = selected

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
                if st.button("▶️ Iniciar", key="sb_start", width="stretch", disabled=is_online):
                    with st.spinner("Iniciando..."):
                        ok, msg = start_ollama(ollama_bin)
                    st.success(msg) if ok else st.error(msg)
                    add_activity("▶️", f"Ollama {'iniciado' if ok else 'falha ao iniciar'}")
                    if ok:
                        time.sleep(1)
                        st.rerun()
            with c2:
                if st.button("⏹️ Parar", key="sb_stop", width="stretch", disabled=not is_online):
                    with st.spinner("Parando..."):
                        ok, msg = stop_ollama()
                    st.success(msg) if ok else st.error(msg)
                    add_activity("⏹️", f"Ollama {'parado' if ok else 'falha ao parar'}")
                    if ok:
                        time.sleep(1)
                        st.rerun()

            if is_online:
                models = list_ollama_models(ollama_bin)
                if models:
                    st.caption(f"Modelos: {', '.join(models)}")
                    if st.button("🧹 Descarregar da RAM", key="sb_unload", width="stretch"):
                        ok, msg = unload_model(model, ollama_bin)
                        st.info(msg) if ok else st.error(msg)
                        add_activity("🧹", f"Modelo {'descarregado' if ok else 'falha ao descarregar'}")

            st.button("🔄 Atualizar", key="sb_refresh", width="stretch", on_click=lambda: None)

        st.divider()
        st.header("🔬 Sobre o TCC")
        st.markdown(f"**Problema:** {RESEARCH_PROBLEM}")
        with st.expander("Hipóteses"):
            for code, text in HYPOTHESES:
                st.markdown(f"**{code}:** {text}")
        with st.expander("Critérios de avaliação"):
            for name, desc in EVALUATION_CRITERIA:
                st.markdown(f"**{name}:** {desc}")

        st.divider()
        st.header("🐘 PostgreSQL (opcional)")
        pg_dsn = st.text_input(
            "Connection string",
            value="",
            key="pg_dsn",
            placeholder="postgresql://user:senha@localhost:5433/tcc_resultados",
            help="Se preenchida, os resultados também são salvos no banco.",
        )
        if pg_dsn:
            pg_ok, pg_msg = check_postgres(pg_dsn)
            if pg_ok:
                st.success(f"✅ PG conectado")
                add_activity("🐘", "PostgreSQL conectado com sucesso")
            else:
                st.error(f"❌ {pg_msg}")
        else:
            pg_ok = False

    return ollama_url, model, timeout, is_online, pg_dsn


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
        width="stretch",
    )

    if run_btn:
        log_area = st.container()
        with log_area:
            execute_scenario(scenario, model, ollama_url, timeout, skip_llm, st)


# ---------------------------------------------------------------------------
# UI: Tab - Run All
# ---------------------------------------------------------------------------
def render_run_all_tab(scenarios: list[dict], model: str, ollama_url: str, timeout: int, pg_dsn: str = ""):
    st.subheader("🚀 Executar todos os cenários")
    st.markdown("Executa todos os cenários em sequência, mostrando progresso em tempo real.")

    skip_llm = st.checkbox("Pular IA (só Terraform)", key="skip_all")

    # --- Loop mode controls ---
    loop_col1, loop_col2 = st.columns([1, 2])
    with loop_col1:
        loop_mode = st.checkbox("🔁 Modo Loop", key="loop_mode", help="Repete a execução automaticamente N vezes")
    with loop_col2:
        if loop_mode:
            loop_count = st.number_input("Iterações", min_value=1, max_value=50, value=3, step=1, key="loop_count")
        else:
            loop_count = 1

    run_all_btn = st.button(
        "▶️ Executar TODOS os cenários",
        key="run_all",
        type="primary",
        width="stretch",
    )

    if run_all_btn:
        if "stop_loop" not in st.session_state:
            st.session_state.stop_loop = False
        st.session_state.stop_loop = False

        stop_btn_placeholder = st.empty()

        for iteration in range(loop_count):
            if st.session_state.get("stop_loop"):
                st.warning("⏹️ Loop interrompido pelo usuário.")
                add_activity("⏹️", "Loop interrompido pelo usuário")
                break

            if loop_mode:
                st.markdown(f"---\n### 🔁 Iteração {iteration + 1} / {loop_count}")
                add_activity("🔁", f"Loop — Iteração {iteration + 1}/{loop_count} iniciada")

            with stop_btn_placeholder:
                if loop_mode and iteration < loop_count - 1:
                    if st.button("⏹️ Parar Loop", key=f"stop_{iteration}", type="secondary"):
                        st.session_state.stop_loop = True

            add_activity("🚀", f"Execução em lote iniciada ({len(scenarios)} cenários)")
            total = len(scenarios)
            progress_bar = st.progress(0, text=f"Preparando... 0/{total}")
            results = []

            for idx, scenario in enumerate(scenarios):
                if st.session_state.get("stop_loop"):
                    break
                progress_bar.progress(
                    (idx) / total,
                    text=f"Executando {scenario['slug']}... ({idx + 1}/{total})"
                )

                with st.expander(f"📁 {scenario['slug']} — {scenario['title']}", expanded=True):
                    result = execute_scenario(
                        scenario, model, ollama_url, timeout, skip_llm, st, pg_dsn=pg_dsn,
                    )
                    results.append(result)

            progress_bar.progress(1.0, text=f"✅ Concluído! {total}/{total} cenários")
            if loop_mode:
                add_activity("✅", f"Loop — Iteração {iteration + 1}/{loop_count} concluída")

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
            st.dataframe(summary_data, width="stretch", hide_index=True)

        stop_btn_placeholder.empty()


# ---------------------------------------------------------------------------
# UI: Tab - Results & Charts
# ---------------------------------------------------------------------------
def render_results_tab():
    try:
        import plotly.graph_objects as go
        import plotly.express as px
        from statistics import mean, median, mode, stdev
        HAS_PLOTLY = True
    except ImportError:
        HAS_PLOTLY = False

    st.subheader("📊 Resultados e Análise Estatística")

    data = load_csv_results()
    if not data:
        st.info("Nenhuma execução registrada ainda. Execute cenários para gerar dados.")
        return

    ai_rows = [r for r in data if r.get("ia_executada") == "sim"]
    all_rows = data

    # --- Metrics row ---
    st.markdown("### 📈 Métricas Gerais")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.metric("Total execuções", len(all_rows))
    with m2:
        st.metric("Com IA", len(ai_rows))
    with m3:
        models_used = set(r["modelo"] for r in ai_rows)
        st.metric("Modelos", len(models_used))
    with m4:
        scenarios_used = set(r["cenario"] for r in ai_rows)
        st.metric("Cenários", len(scenarios_used))
    with m5:
        if ai_rows:
            avg_ai = mean([float(r["tempo_ia_s"]) for r in ai_rows])
            st.metric("Média IA (s)", f"{avg_ai:.1f}")
        else:
            st.metric("Média IA (s)", "N/A")
    with m6:
        if ai_rows:
            avg_tok = mean([int(r["tokens_estimados"]) for r in ai_rows])
            st.metric("Média tokens", f"{avg_tok:.0f}")
        else:
            st.metric("Média tokens", "N/A")

    if not ai_rows or not HAS_PLOTLY:
        st.warning("Sem dados com IA ou Plotly não disponível.")
        return

    # --- Outlier filtering (IQR method) ---
    def remove_outliers(values, factor=1.5):
        """Remove outliers using IQR method. Returns cleaned list."""
        if len(values) < 4:
            return values
        s = sorted(values)
        q1 = s[len(s) // 4]
        q3 = s[3 * len(s) // 4]
        iqr = q3 - q1
        lower = q1 - factor * iqr
        upper = q3 + factor * iqr
        return [v for v in values if lower <= v <= upper]

    def percentile(values, p):
        """Get p-th percentile (0-100)."""
        s = sorted(values)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    # Filter out extreme outliers from ai_rows for visualization
    all_ai_times = [float(r["tempo_ia_s"]) for r in ai_rows if float(r["tempo_ia_s"]) > 0]
    all_tokens = [int(r["tokens_estimados"]) for r in ai_rows if int(r["tokens_estimados"]) > 0]

    # Use P99 as cap for axis ranges
    time_cap = percentile(all_ai_times, 97) * 1.3 if all_ai_times else 100
    token_cap = percentile(all_tokens, 97) * 1.3 if all_tokens else 2000

    # Clean rows (remove extreme outliers for charts, keep for stats table)
    ai_rows_clean = [r for r in ai_rows
                     if 0 < float(r["tempo_ia_s"]) <= time_cap
                     and 0 < int(r["tokens_estimados"]) <= token_cap]

    n_removed = len(ai_rows) - len(ai_rows_clean)

    # Prepare data lists (from clean data)
    ai_times = [float(r["tempo_ia_s"]) for r in ai_rows_clean]
    tokens_list = [int(r["tokens_estimados"]) for r in ai_rows_clean]
    toks_per_sec = [int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
                    for r in ai_rows_clean if float(r["tempo_ia_s"]) > 0]

    if n_removed > 0:
        st.caption(f"⚠️ {n_removed} outlier(s) extremos removidos dos gráficos (>{time_cap:.0f}s ou >{token_cap:.0f} tokens). Estatísticas descritivas usam dados completos.")

    # ── Estatísticas Descritivas (tabela resumo) ─────────────────────────────
    st.markdown("---")
    st.markdown("### 📐 Estatísticas Descritivas")

    def calc_stats(values, label):
        if not values:
            return {}
        try:
            m = mode(values)
        except Exception:
            m = "N/A"
        return {
            "Métrica": label,
            "N": len(values),
            "Média": f"{mean(values):.2f}",
            "Mediana": f"{median(values):.2f}",
            "Moda": f"{m:.2f}" if isinstance(m, (int, float)) else str(m),
            "Desvio Padrão": f"{stdev(values):.2f}" if len(values) > 1 else "N/A",
            "Mín": f"{min(values):.2f}",
            "Máx": f"{max(values):.2f}",
        }

    stats_table = [
        calc_stats([float(r["tempo_ia_s"]) for r in ai_rows if float(r["tempo_ia_s"]) > 0], "Tempo IA (s)"),
        calc_stats([int(r["tokens_estimados"]) for r in ai_rows if int(r["tokens_estimados"]) > 0], "Tokens gerados"),
        calc_stats([int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
                    for r in ai_rows if float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0], "Tokens/segundo"),
        calc_stats([float(r["tempo_terraform_s"]) for r in ai_rows], "Tempo Terraform (s)"),
    ]
    st.dataframe(stats_table, width="stretch", hide_index=True)

    # ── Estatísticas por Modelo ──────────────────────────────────────────────
    st.markdown("### 🤖 Estatísticas por Modelo")
    model_stats = []
    for m in sorted(set(r["modelo"] for r in ai_rows)):
        m_rows = [r for r in ai_rows if r["modelo"] == m]
        m_times = [float(r["tempo_ia_s"]) for r in m_rows if float(r["tempo_ia_s"]) > 0]
        m_tokens = [int(r["tokens_estimados"]) for r in m_rows if int(r["tokens_estimados"]) > 0]
        m_tps = [t / s for t, s in zip(m_tokens, m_times) if s > 0] if m_times else []
        try:
            m_mode = mode(m_times)
            m_mode_str = f"{m_mode:.2f}"
        except Exception:
            m_mode_str = "N/A"
        model_stats.append({
            "Modelo": m,
            "Execuções": len(m_rows),
            "Tempo Mín (s)": f"{min(m_times):.2f}" if m_times else "N/A",
            "Tempo Máx (s)": f"{max(m_times):.2f}" if m_times else "N/A",
            "Tempo Médio (s)": f"{mean(m_times):.2f}" if m_times else "N/A",
            "Mediana (s)": f"{median(m_times):.2f}" if m_times else "N/A",
            "Moda (s)": m_mode_str,
            "Tokens Médio": f"{mean(m_tokens):.0f}" if m_tokens else "N/A",
            "Tok/s Médio": f"{mean(m_tps):.1f}" if m_tps else "N/A",
        })
    st.dataframe(model_stats, width="stretch", hide_index=True)

    st.markdown("---")

    # ── CHART 1: Box Plot — Tempo IA por Modelo ──────────────────────────────
    st.markdown("### 📦 1. Box Plot — Tempo de Resposta da IA por Modelo")
    st.caption("Mostra mediana, quartis e outliers para cada modelo.")

    fig_box = go.Figure()
    models_sorted = sorted(set(r["modelo"] for r in ai_rows_clean))
    colors = ["#1E88E5", "#43A047", "#E53935", "#FB8C00", "#8E24AA"]
    for idx, m in enumerate(models_sorted):
        times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        fig_box.add_trace(go.Box(
            y=times, name=m,
            marker_color=colors[idx % len(colors)],
            boxmean="sd",  # show mean + standard deviation
        ))
    fig_box.update_layout(
        yaxis_title="Tempo de resposta (s)",
        height=400,
        margin=dict(t=30, b=40),
        showlegend=False,
        yaxis=dict(range=[0, time_cap]),
    )
    st.plotly_chart(fig_box, width="stretch")

    st.markdown("---")

    # ── CHART 2: Violin Plot — Tokens por Modelo ────────────────────────────
    st.markdown("### 🎻 2. Violin Plot — Distribuição de Tokens por Modelo")
    st.caption("Densidade de probabilidade + box plot interno. Revela bimodalidades.")

    fig_violin = go.Figure()
    for idx, m in enumerate(models_sorted):
        toks = [int(r["tokens_estimados"]) for r in ai_rows_clean if r["modelo"] == m and int(r["tokens_estimados"]) > 0]
        fig_violin.add_trace(go.Violin(
            y=toks, name=m,
            box_visible=True,
            meanline_visible=True,
            fillcolor=colors[idx % len(colors)],
            opacity=0.7,
            line_color="black",
        ))
    fig_violin.update_layout(
        yaxis_title="Tokens estimados",
        height=400,
        margin=dict(t=30, b=40),
        showlegend=False,
        yaxis=dict(range=[0, token_cap]),
    )
    st.plotly_chart(fig_violin, width="stretch")

    st.markdown("---")

    # ── CHART 2.5: Histograma — Distribuição Tempo IA por Modelo ────────────
    st.markdown("### 📊 2.5. Histograma — Distribuição do Tempo de Resposta por Modelo")
    st.caption("Barras mostram frequência. Linhas verticais: média (tracejada), mediana (pontilhada), moda (sólida).")

    for idx, m in enumerate(models_sorted):
        m_times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        if not m_times:
            continue
        fig_hist_m = go.Figure()
        fig_hist_m.add_trace(go.Histogram(
            x=m_times, name=m,
            nbinsx=30,
            marker_color=colors[idx % len(colors)],
            opacity=0.75,
        ))
        m_mean = mean(m_times)
        m_median = median(m_times)
        try:
            m_mode_val = mode(m_times)
        except Exception:
            m_mode_val = None

        fig_hist_m.add_vline(x=m_mean, line_dash="dash", line_color="#1E88E5", line_width=2,
                             annotation_text=f"Média: {m_mean:.1f}s", annotation_position="top right")
        fig_hist_m.add_vline(x=m_median, line_dash="dot", line_color="#43A047", line_width=2,
                             annotation_text=f"Mediana: {m_median:.1f}s", annotation_position="top left")
        if m_mode_val is not None:
            fig_hist_m.add_vline(x=m_mode_val, line_dash="solid", line_color="#E53935", line_width=2,
                                 annotation_text=f"Moda: {m_mode_val:.1f}s", annotation_position="bottom right")

        fig_hist_m.update_layout(
            title=f"{m} — {len(m_times)} execuções | Mín: {min(m_times):.1f}s | Máx: {max(m_times):.1f}s",
            xaxis_title="Tempo de resposta (s)",
            yaxis_title="Frequência",
            height=320,
            margin=dict(t=50, b=40),
            showlegend=False,
        )
        st.plotly_chart(fig_hist_m, width="stretch")

    st.markdown("---")

    # ── CHART 3: Histograma — Distribuição tok/s com Média/Mediana ───────────
    st.markdown("### 📊 3. Histograma — Eficiência (tokens/s) com Média e Mediana")
    st.caption("Linhas verticais: média (azul), mediana (verde), moda (vermelho).")

    fig_hist = go.Figure()
    for idx, m in enumerate(models_sorted):
        tps = [int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
               for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0]
        fig_hist.add_trace(go.Histogram(
            x=tps, name=m,
            opacity=0.65,
            nbinsx=25,
            marker_color=colors[idx % len(colors)],
        ))
    # Add vertical lines for overall stats
    overall_mean = mean(toks_per_sec)
    overall_median = median(toks_per_sec)
    fig_hist.add_vline(x=overall_mean, line_dash="dash", line_color="#1E88E5",
                       annotation_text=f"Média: {overall_mean:.1f}")
    fig_hist.add_vline(x=overall_median, line_dash="dot", line_color="#43A047",
                       annotation_text=f"Mediana: {overall_median:.1f}")
    fig_hist.update_layout(
        xaxis_title="Tokens por segundo",
        yaxis_title="Frequência",
        barmode="overlay",
        height=380,
        margin=dict(t=30, b=40),
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig_hist, width="stretch")

    st.markdown("---")

    # ── CHART 4: Heatmap — Tempo IA médio (Modelo × Cenário) ─────────────────
    st.markdown("### 🗺️ 4. Heatmap — Tempo médio da IA (Modelo × Cenário)")
    st.caption("Quão rápido cada modelo resolve cada tipo de falha. Mais escuro = mais lento.")

    cenarios_sorted = sorted(set(r["cenario"] for r in ai_rows_clean))
    z_matrix = []
    for m in models_sorted:
        row = []
        for c in cenarios_sorted:
            vals = [float(r["tempo_ia_s"]) for r in ai_rows_clean
                    if r["modelo"] == m and r["cenario"] == c and float(r["tempo_ia_s"]) > 0]
            row.append(mean(vals) if vals else 0)
        z_matrix.append(row)

    fig_heat = go.Figure(go.Heatmap(
        z=z_matrix,
        x=cenarios_sorted,
        y=models_sorted,
        colorscale="YlOrRd",
        text=[[f"{v:.1f}s" for v in row] for row in z_matrix],
        texttemplate="%{text}",
        textfont={"size": 11},
        colorbar_title="Tempo (s)",
    ))
    fig_heat.update_layout(
        height=300,
        margin=dict(t=30, b=60),
        xaxis_title="Cenário",
        yaxis_title="Modelo",
    )
    st.plotly_chart(fig_heat, width="stretch")

    st.markdown("---")

    # ── CHART 5: Heatmap — Tokens médios (Modelo × Cenário) ──────────────────
    st.markdown("### 🗺️ 5. Heatmap — Tokens gerados (Modelo × Cenário)")
    st.caption("Mais escuro = respostas mais longas. Respostas maiores podem indicar mais detalhamento.")

    z_tok_matrix = []
    for m in models_sorted:
        row = []
        for c in cenarios_sorted:
            vals = [int(r["tokens_estimados"]) for r in ai_rows_clean
                    if r["modelo"] == m and r["cenario"] == c and int(r["tokens_estimados"]) > 0]
            row.append(mean(vals) if vals else 0)
        z_tok_matrix.append(row)

    fig_heat2 = go.Figure(go.Heatmap(
        z=z_tok_matrix,
        x=cenarios_sorted,
        y=models_sorted,
        colorscale="Blues",
        text=[[f"{v:.0f}" for v in row] for row in z_tok_matrix],
        texttemplate="%{text}",
        textfont={"size": 11},
        colorbar_title="Tokens",
    ))
    fig_heat2.update_layout(
        height=300,
        margin=dict(t=30, b=60),
        xaxis_title="Cenário",
        yaxis_title="Modelo",
    )
    st.plotly_chart(fig_heat2, width="stretch")

    st.markdown("---")

    # ── CHART 6: Grouped Bar — Comparativo tok/s por modelo e cenário ────────
    st.markdown("### ⚡ 6. Eficiência por Modelo × Cenário (tok/s)")
    st.caption("Mais alto = modelo respondeu mais rápido nesse cenário.")

    fig_grouped = go.Figure()
    for idx, m in enumerate(models_sorted):
        tps_by_cenario = []
        for c in cenarios_sorted:
            vals = [int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
                    for r in ai_rows_clean
                    if r["modelo"] == m and r["cenario"] == c
                    and float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0]
            tps_by_cenario.append(mean(vals) if vals else 0)
        fig_grouped.add_trace(go.Bar(
            name=m,
            x=cenarios_sorted,
            y=tps_by_cenario,
            marker_color=colors[idx % len(colors)],
            text=[f"{v:.1f}" for v in tps_by_cenario],
            textposition="outside",
        ))
    fig_grouped.update_layout(
        barmode="group",
        xaxis_title="Cenário",
        yaxis_title="Tokens/segundo (média)",
        height=380,
        margin=dict(t=30, b=60),
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig_grouped, width="stretch")

    st.markdown("---")

    # ── CHART 7: Box Plot — Tempo Terraform por Cenário ──────────────────────
    st.markdown("### 🔧 7. Box Plot — Tempo Terraform por Cenário")
    st.caption("Variabilidade do pipeline de IaC. Cenários Docker tendem a ser mais lentos.")

    fig_tf_box = go.Figure()
    for idx, c in enumerate(cenarios_sorted):
        vals = [float(r["tempo_terraform_s"]) for r in ai_rows_clean if r["cenario"] == c]
        fig_tf_box.add_trace(go.Box(
            y=vals, name=c,
            marker_color=colors[idx % len(colors)],
            boxmean=True,
        ))
    fig_tf_box.update_layout(
        yaxis_title="Tempo Terraform (s)",
        height=380,
        margin=dict(t=30, b=40),
        showlegend=False,
    )
    st.plotly_chart(fig_tf_box, width="stretch")

    st.markdown("---")

    # ── CHART 8: Scatter — Tempo IA vs Tokens (correlação) ───────────────────
    st.markdown("### 🔬 8. Correlação — Tempo de IA vs Tokens Gerados")
    st.caption("Esperado: relação linear (mais tokens = mais tempo). Desvios indicam variação na velocidade.")

    fig_scatter = go.Figure()
    for idx, m in enumerate(models_sorted):
        xs = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        ys = [int(r["tokens_estimados"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        fig_scatter.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers",
            name=m,
            marker=dict(size=6, color=colors[idx % len(colors)], opacity=0.6),
        ))
    fig_scatter.update_layout(
        xaxis_title="Tempo IA (s)",
        yaxis_title="Tokens gerados",
        height=400,
        margin=dict(t=30, b=40),
        legend=dict(orientation="h", y=-0.15),
        xaxis=dict(range=[0, time_cap]),
        yaxis=dict(range=[0, token_cap]),
    )
    st.plotly_chart(fig_scatter, width="stretch")

    st.markdown("---")

    # ── CHART 9: Radar — Comparativo geral entre modelos ─────────────────────
    st.markdown("### 🕸️ 9. Radar — Perfil Comparativo dos Modelos")
    st.caption("Normalizado 0-1. Maior área = melhor desempenho geral.")

    # Compute metrics per model (normalized)
    model_metrics = {}
    for m in models_sorted:
        m_rows = [r for r in ai_rows_clean if r["modelo"] == m]
        if not m_rows:
            continue
        m_ai_times = [float(r["tempo_ia_s"]) for r in m_rows if float(r["tempo_ia_s"]) > 0]
        m_tokens = [int(r["tokens_estimados"]) for r in m_rows if int(r["tokens_estimados"]) > 0]
        m_tps = [t / s for t, s in zip(m_tokens, m_ai_times) if s > 0] if m_ai_times else [0]
        model_metrics[m] = {
            "Velocidade (tok/s)": mean(m_tps) if m_tps else 0,
            "Tokens gerados": mean(m_tokens) if m_tokens else 0,
            "Tempo resposta (inverso)": 1.0 / mean(m_ai_times) if m_ai_times and mean(m_ai_times) > 0 else 0,
            "Consistência (1/σ)": 1.0 / stdev(m_ai_times) if len(m_ai_times) > 1 and stdev(m_ai_times) > 0 else 0,
            "Cenários cobertos": len(set(r["cenario"] for r in m_rows)),
        }

    # Normalize each metric to 0-1 (max across models = 1)
    categories = list(list(model_metrics.values())[0].keys()) if model_metrics else []
    max_vals = {}
    for cat in categories:
        max_vals[cat] = max(model_metrics[m][cat] for m in model_metrics) or 1

    fig_radar = go.Figure()
    for idx, m in enumerate(models_sorted):
        if m not in model_metrics:
            continue
        values = [model_metrics[m][cat] / max_vals[cat] for cat in categories]
        values.append(values[0])  # close the polygon
        fig_radar.add_trace(go.Scatterpolar(
            r=values,
            theta=categories + [categories[0]],
            name=m,
            fill="toself",
            opacity=0.5,
            line_color=colors[idx % len(colors)],
        ))
    fig_radar.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        height=450,
        margin=dict(t=40, b=40),
        legend=dict(orientation="h", y=-0.1),
    )
    st.plotly_chart(fig_radar, width="stretch")

    st.markdown("---")

    # --- Tabela histórico + download CSV ---
    st.markdown("### 📋 Histórico completo")
    st.dataframe(data, width="stretch", hide_index=True)

    csv_content = CSV_FILE.read_text(encoding="utf-8")
    if st.download_button(
        "⬇️ Baixar CSV completo",
        data=csv_content,
        file_name="resultados_tcc_mvp.csv",
        mime="text/csv",
        width="stretch",
    ):
        add_activity("⬇️", "CSV de resultados baixado")


# ---------------------------------------------------------------------------
# UI: Tab - Automation (overnight batch run)
# ---------------------------------------------------------------------------
def render_automation_tab(scenarios: list[dict], ollama_url: str, timeout: int, pg_dsn: str = ""):
    st.subheader("🤖 Automação")
    st.markdown(
        "Roda **todos os cenários** com **todos os modelos instalados** N vezes cada, "
        "coletando dados em massa para análise estatística do TCC."
    )

    _bin = find_ollama_binary()
    installed_models = list_ollama_models(_bin) if _bin else []
    # Combine installed + known models; installed ones appear first, marked with ✅
    all_known = sorted(set(installed_models + KNOWN_MODELS))
    model_display = {m: (f"✅ {m}" if m in installed_models else f"⬜ {m} (não instalado)") for m in all_known}

    if not installed_models:
        st.warning("⚠️ Nenhum modelo instalado detectado. Você pode selecionar modelos da lista, mas precisará instalá-los antes de executar.")

    # --- Configuration ---
    st.markdown("### ⚙️ Configuração da automação")
    cfg1, cfg2 = st.columns(2)
    with cfg1:
        iterations = st.number_input(
            "Iterações por modelo",
            min_value=1,
            max_value=500,
            value=100,
            step=10,
            key="auto_iterations",
            help="Quantas vezes rodar TODOS os cenários por modelo",
        )
        selected_models = st.multiselect(
            "Modelos a usar",
            options=all_known,
            default=installed_models,
            format_func=lambda m: model_display[m],
            key="auto_models",
        )

        # ── Critério de parada ──────────────────────────────────────────────
        stop_criteria = st.radio(
            "Critério de parada:",
            options=["Por iterações", "Por data/hora", "O que acontecer primeiro"],
            index=0,
            horizontal=True,
            key="auto_stop_criteria",
        )
        stop_datetime = None
        if stop_criteria in ("Por data/hora", "O que acontecer primeiro"):
            stop_date = st.date_input("Data de parada", key="auto_stop_date")
            stop_time_val = st.time_input("Hora de parada", value=_dt_module.time(8, 0), key="auto_stop_time")
            stop_datetime = _dt_module.datetime.combine(stop_date, stop_time_val)
    with cfg2:
        st.info(
            f"📊 **Total estimado de execuções:**\n\n"
            f"`{len(selected_models)} modelos × {len(scenarios)} cenários × {iterations} iterações`\n\n"
            f"**= {len(selected_models) * len(scenarios) * iterations:,} execuções**"
        )
        if installed_models:
            # rough estimate: ~90s per scenario with AI on CPU
            total_secs = len(selected_models) * len(scenarios) * iterations * 90
            h, remainder = divmod(total_secs, 3600)
            m = remainder // 60
            st.caption(f"⏱️ Tempo estimado (90s/cenário): ~{h}h {m}min")

    if not selected_models:
        st.warning("Selecione ao menos um modelo para prosseguir.")
        return

    st.markdown("---")

    # --- Controls ---
    auto_col1, auto_col2 = st.columns(2)
    with auto_col1:
        start_btn = st.button(
            "🤖 Iniciar Automação",
            key="auto_start",
            type="primary",
            width="stretch",
            disabled=st.session_state.get("auto_running", False),
        )
    with auto_col2:
        stop_btn = st.button(
            "⏹️ Parar Automação",
            key="auto_stop",
            type="secondary",
            width="stretch",
            disabled=not st.session_state.get("auto_running", False),
        )

    if stop_btn:
        st.session_state.auto_stop_requested = True
        add_activity("⏹️", "Automação: parada solicitada pelo usuário")
        st.warning("⏹️ Parada solicitada — aguardando cenário atual terminar...")

    if start_btn:
        st.session_state.auto_running = True
        st.session_state.auto_stop_requested = False
        add_activity("🤖", f"Automação noturna iniciada: {len(selected_models)} modelos × {iterations} iterações × {len(scenarios)} cenários")
        logger.info(f"🤖 Automação iniciada: {len(selected_models)} modelos, {iterations} iter, {len(scenarios)} cenários")

        # total_runs = iterations × models (round-robin: each iteration covers ALL models)
        total_runs = iterations * len(selected_models)
        overall_bar = st.progress(0.0, text="Iniciando automação...")
        runs_done = 0

        completed_normally = True
        last_iteration_idx = 0
        stop_reason = ""

        def time_exceeded() -> bool:
            if stop_criteria == "Por iterações":
                return False
            if stop_datetime and _dt_module.datetime.now() >= stop_datetime:
                return True
            return False

        # ── Round-robin: iterate first, then models ──────────────────────────
        # This ensures uniform data distribution if stopped early.
        # Iteration 1: model A, model B, model C → Iteration 2: model A, model B, ...
        for iteration_idx in range(iterations):
            last_iteration_idx = iteration_idx
            if st.session_state.get("auto_stop_requested"):
                completed_normally = False
                break
            if time_exceeded():
                completed_normally = False
                stop_reason = f"horário final atingido ({stop_datetime.strftime('%H:%M')})" if stop_datetime else ""
                break

            st.markdown(f"### 🔁 Iteração {iteration_idx + 1}/{iterations}")

            for model_idx, auto_model in enumerate(selected_models):
                if st.session_state.get("auto_stop_requested"):
                    completed_normally = False
                    break
                if time_exceeded():
                    completed_normally = False
                    stop_reason = f"horário final atingido ({stop_datetime.strftime('%H:%M')})" if stop_datetime else ""
                    break

                add_activity("🔁", f"Iter {iteration_idx + 1} | [{model_idx+1}/{len(selected_models)}] {auto_model}")

                with st.expander(
                    f"📦 Iter {iteration_idx + 1} — {auto_model}",
                    expanded=False,
                ):
                    for scenario in scenarios:
                        if st.session_state.get("auto_stop_requested"):
                            break
                        if time_exceeded():
                            break
                        execute_scenario(
                            scenario,
                            auto_model,
                            ollama_url,
                            timeout,
                            skip_llm=False,
                            log_container=st,
                            pg_dsn=pg_dsn,
                        )

                runs_done += 1
                pct_overall = runs_done / total_runs
                overall_bar.progress(
                    min(pct_overall, 1.0),
                    text=f"Total: {runs_done}/{total_runs} ({pct_overall*100:.1f}%) — iter {iteration_idx+1} / modelo {auto_model}",
                )

        st.session_state.auto_running = False
        if completed_normally:
            total_exec = runs_done * len(scenarios)
            st.success(f"🎉 Automação concluída! {total_exec:,} execuções com {len(selected_models)} modelo(s) × {last_iteration_idx + 1} iterações.")
            add_activity("🎉", f"Automação concluída: {total_exec:,} execuções totais")
            logger.info(f"🎉 Automação finalizada: {total_exec} execuções")
        else:
            total_exec = runs_done * len(scenarios)
            reason = getattr(st.session_state, "stop_reason", "pelo usuário")
            st.warning(f"⏹️ Automação interrompida após {total_exec:,} execuções.")
            add_activity("⏹️", f"Automação interrompida: {total_exec:,} execuções realizadas")


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
    if st.download_button(
        "⬇️ Baixar todos os relatórios (.md)",
        data=all_reports,
        file_name="relatorios_tcc_mvp.md",
        mime="text/markdown",
        width="stretch",
    ):
        add_activity("⬇️", "Relatórios MD baixados")


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
    _init_activity_log()
    add_activity("🔬", "Dashboard carregado") if not st.session_state.activity_log else None

    ollama_url, model, timeout, is_online, pg_dsn = render_sidebar()

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
    tab_names.append("🤖 Automação")
    tab_names.append("📊 Resultados")
    tab_names.append("📂 Relatórios")
    tab_names.append("📋 Log de Atividades")

    tabs = st.tabs(tab_names)

    # Individual scenario tabs
    for i, scenario in enumerate(scenarios):
        with tabs[i]:
            render_scenario_tab(scenario, model, ollama_url, timeout)

    # Run All tab
    with tabs[len(scenarios)]:
        render_run_all_tab(scenarios, model, ollama_url, timeout, pg_dsn=pg_dsn)

    # Automation tab
    with tabs[len(scenarios) + 1]:
        render_automation_tab(scenarios, ollama_url, timeout, pg_dsn=pg_dsn)

    # Results tab
    with tabs[len(scenarios) + 2]:
        render_results_tab()

    # Reports tab
    with tabs[len(scenarios) + 3]:
        render_reports_tab()

    # Activity Log tab
    with tabs[len(scenarios) + 4]:
        render_activity_log_tab()

    # Footer
    st.divider()
    st.caption(
        "TCC — Análise Estruturada de Logs em Infraestrutura como Código: "
        "Um Método Baseado em IA Generativa para Otimização DevOps"
    )


if __name__ == "__main__":
    main()
