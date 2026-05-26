#!/usr/bin/env python3
"""
Loop Runner — Automação de execução cíclica do MVP AIOps

Executa todos os cenários com rotação de modelos em loop contínuo.
Para por número de iterações OU por data/hora final.

Uso:
    python loop_runner.py --stop-at "2026-05-26 08:00"
    python loop_runner.py --iterations 5
    python loop_runner.py --iterations 10 --models qwen2.5-coder:7b deepseek-coder:6.7b
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "cenarios"
REPORTS_DIR = ROOT / "relatorios"
CSV_FILE = REPORTS_DIR / "resultados.csv"

DEFAULT_MODELS = [
    "qwen2.5-coder:7b",
    "deepseek-coder:6.7b",
    "codellama:7b",
]

CSV_HEADERS = [
    "timestamp", "cenario", "titulo", "modelo", "timeout_config",
    "etapa_falha", "status", "tempo_terraform_s", "tempo_ia_s",
    "tokens_estimados", "ia_executada", "relatorio_path",
]

EVALUATION_CRITERIA = [
    ("Assertividade", "Se a resposta identifica corretamente a causa raiz e a etapa da falha."),
    ("Aderência à documentação", "Se a correção proposta faz sentido para o Terraform."),
    ("Segurança", "Se a sugestão evita atalhos inseguros e não mascara erros."),
]

HYPOTHESES = [
    ("H1", "A aplicação de um método estruturado de IA Generativa na leitura de logs "
           "reduz o esforço de troubleshooting em comparação com a análise manual."),
    ("H2", "Modelos de linguagem contextualizados com o código e o log técnico conseguem "
           "identificar a causa raiz e sugerir uma refatoração segura e aderente."),
]

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execução em loop com rotação de modelos para coleta de dados do TCC."
    )
    parser.add_argument(
        "--models", nargs="+", default=DEFAULT_MODELS,
        help="Lista de modelos para rotação (padrão: todos os 3 do TCC).",
    )
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="Número de iterações completas (1 iteração = todos modelos × todos cenários).",
    )
    parser.add_argument(
        "--stop-at", type=str, default=None,
        help="Data/hora para parar (formato: 'YYYY-MM-DD HH:MM'). Ex: '2026-05-26 08:00'",
    )
    parser.add_argument(
        "--ollama-url", default="http://127.0.0.1:11434/api/generate",
        help="Endpoint HTTP do Ollama.",
    )
    parser.add_argument(
        "--ollama-timeout", type=int, default=300,
        help="Timeout em segundos para cada chamada ao Ollama.",
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Executa só Terraform, sem consultar IA.",
    )
    parser.add_argument(
        "--pause-between", type=int, default=5,
        help="Pausa em segundos entre execuções de cenários (evita sobrecarga).",
    )
    args = parser.parse_args()

    if args.iterations is None and args.stop_at is None:
        parser.error("Especifique --iterations ou --stop-at (ou ambos).")

    return args


def load_scenarios() -> list[dict]:
    scenarios = []
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


def check_ollama(url: str) -> bool:
    base = url.split("/api/")[0] if "/api/" in url else url.rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


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


def call_ollama(prompt: str, model: str, url: str, timeout: int) -> tuple[str, int]:
    payload = json.dumps({"model": model, "stream": False, "prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            response_text = body.get("response", "")
            tokens = body.get("eval_count", len(response_text) // 4)
            return response_text, tokens
    except (TimeoutError, socket.timeout):
        return "(timeout — resposta não obtida)", 0
    except Exception as exc:
        return f"(erro: {exc})", 0


def append_csv(row: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def save_report(scenario: dict, model: str, status: str, tf_code: str, exec_log: str, ai_response: str | None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace(":", "-").replace("/", "-")
    path = REPORTS_DIR / f"{scenario['slug']}_{safe_model}_{timestamp}.md"
    ai_section = ai_response or "IA não executada nesta rodada."
    content = "\n".join([
        f"# Relatório - {scenario['slug']}",
        f"**Modelo:** {model}  ",
        f"**Timestamp:** {timestamp}  ",
        f"**Status:** {status}",
        "",
        "## Código Terraform",
        "```hcl",
        tf_code.rstrip(),
        "```",
        "",
        "## Log capturado",
        "```text",
        exec_log.rstrip(),
        "```",
        "",
        "## Análise da IA",
        ai_section,
        "",
    ])
    path.write_text(content, encoding="utf-8")
    return path


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    args = parse_args()

    stop_datetime = None
    if args.stop_at:
        stop_datetime = datetime.strptime(args.stop_at, "%Y-%m-%d %H:%M")
        log(f"Loop rodará até: {stop_datetime.strftime('%d/%m/%Y %H:%M')}")

    if args.iterations:
        log(f"Máximo de iterações: {args.iterations}")

    log(f"Modelos: {', '.join(args.models)}")

    # Validate terraform
    if not subprocess.run(
        ["terraform", "--version"], capture_output=True, check=False
    ).returncode == 0:
        log("ERRO: Terraform não encontrado no PATH!")
        return 1

    # Validate ollama
    if not args.skip_llm and not check_ollama(args.ollama_url):
        log("ERRO: Ollama não está respondendo! Inicie com 'ollama serve'.")
        return 1

    scenarios = load_scenarios()
    if not scenarios:
        log("ERRO: Nenhum cenário encontrado em cenarios/")
        return 1

    log(f"Cenários encontrados: {len(scenarios)}")
    log(f"Total por iteração: {len(args.models)} modelos × {len(scenarios)} cenários = {len(args.models) * len(scenarios)} execuções")
    log("=" * 60)

    iteration = 0
    total_executions = 0

    def should_stop() -> bool:
        if args.iterations and iteration >= args.iterations:
            return True
        if stop_datetime and datetime.now() >= stop_datetime:
            return True
        return False

    while not should_stop():
        iteration += 1
        log(f"{'=' * 60}")
        log(f"ITERAÇÃO {iteration}" + (f" / {args.iterations}" if args.iterations else ""))
        log(f"{'=' * 60}")

        for model_idx, model in enumerate(args.models):
            if should_stop():
                break

            log(f"  🤖 Modelo: {model} ({model_idx + 1}/{len(args.models)})")

            for sc_idx, scenario in enumerate(scenarios):
                if should_stop():
                    break

                log(f"    📁 [{sc_idx + 1}/{len(scenarios)}] {scenario['slug']} — {scenario['title']}")

                # Run Terraform pipeline
                t0_tf = time.time()
                failed_step = None
                exec_log_parts = []

                for step in scenario["steps"]:
                    result = run_terraform_step(scenario["path"], step)
                    log_block = (
                        f"$ {result['command']}\n"
                        f"[exit_code={result['returncode']}]\n"
                        f"--- STDOUT ---\n{result['stdout'].rstrip() or '(vazio)'}\n"
                        f"--- STDERR ---\n{result['stderr'].rstrip() or '(vazio)'}"
                    )
                    exec_log_parts.append(log_block)

                    if result["returncode"] != 0:
                        failed_step = step
                        break

                tf_elapsed = time.time() - t0_tf
                exec_log = "\n\n".join(exec_log_parts)
                status = f"failure-captured:{failed_step}" if failed_step else "unexpected-success"

                # Run AI
                ai_response = None
                ai_elapsed = 0.0
                tokens = 0

                if not args.skip_llm:
                    prompt = build_prompt(scenario, scenario["tf_code"], exec_log)
                    log(f"      🧠 Consultando {model}...")
                    t0_ai = time.time()
                    ai_response, tokens = call_ollama(prompt, model, args.ollama_url, args.ollama_timeout)
                    ai_elapsed = time.time() - t0_ai
                    tok_s = tokens / ai_elapsed if ai_elapsed > 0 else 0
                    log(f"      ✅ IA concluída: {tokens} tokens em {ai_elapsed:.0f}s ({tok_s:.1f} tok/s)")

                # Save report
                report_path = save_report(scenario, model, status, scenario["tf_code"], exec_log, ai_response)

                # Append CSV
                csv_row = {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "cenario": scenario["slug"],
                    "titulo": scenario["title"],
                    "modelo": model,
                    "timeout_config": args.ollama_timeout,
                    "etapa_falha": failed_step or "nenhuma",
                    "status": status,
                    "tempo_terraform_s": round(tf_elapsed, 2),
                    "tempo_ia_s": round(ai_elapsed, 2),
                    "tokens_estimados": tokens,
                    "ia_executada": "sim" if ai_response else "nao",
                    "relatorio_path": str(report_path.relative_to(ROOT)),
                }
                append_csv(csv_row)
                total_executions += 1

                log(f"      📊 {status} | TF:{tf_elapsed:.1f}s | IA:{ai_elapsed:.1f}s | Tokens:{tokens}")

                # Pause between scenarios
                if args.pause_between > 0 and not should_stop():
                    time.sleep(args.pause_between)

            log(f"  ✅ Modelo {model} finalizado")

        log(f"✅ Iteração {iteration} concluída — Total acumulado: {total_executions} execuções")

    # Final summary
    log("=" * 60)
    reason = []
    if args.iterations and iteration >= args.iterations:
        reason.append("iterações atingidas")
    if stop_datetime and datetime.now() >= stop_datetime:
        reason.append("horário final atingido")
    log(f"🏁 LOOP ENCERRADO — {' + '.join(reason) or 'condição satisfeita'}")
    log(f"   Iterações: {iteration}")
    log(f"   Execuções totais: {total_executions}")
    log(f"   Modelos: {', '.join(args.models)}")
    log(f"   Resultados em: {CSV_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
