#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "cenarios"
REPORTS_DIR = ROOT / "relatorios"
SUMMARY_FILE = REPORTS_DIR / "resumo_execucao.json"

RESEARCH_PROBLEM = (
    "Como um metodo estruturado de analise de logs via IA Generativa pode auxiliar "
    "na identificacao da causa raiz e na proposicao de correcoes para falhas de "
    "provisionamento em ferramentas de infraestrutura como codigo?"
)

GENERAL_OBJECTIVE = (
    "Propor um metodo estruturado de interpretacao de logs, utilizando Inteligencia "
    "Artificial Generativa, para identificar a causa raiz e sugerir correcoes em "
    "falhas de provisionamento de infraestrutura como codigo, visando otimizar o "
    "fluxo de trabalho em operacoes DevOps."
)

HYPOTHESES = [
    (
        "H1",
        "A aplicacao de um metodo estruturado de IA Generativa na leitura de logs "
        "de infraestrutura como codigo reduz o esforco de troubleshooting em "
        "comparacao com a analise manual.",
    ),
    (
        "H2",
        "Modelos de linguagem devidamente contextualizados com o codigo e o log "
        "tecnico conseguem identificar a causa raiz da falha e sugerir uma "
        "refatoracao segura e aderente ao contexto.",
    ),
]

EVALUATION_CRITERIA = [
    (
        "Assertividade",
        "Se a resposta identifica corretamente a causa raiz e a etapa em que a falha ocorreu.",
    ),
    (
        "Aderencia a documentacao",
        "Se a explicacao e a correcao proposta fazem sentido para o comportamento esperado do Terraform.",
    ),
    (
        "Seguranca",
        "Se a sugestao evita atalhos inseguros, nao mascara erros e nao recomenda exposicao de segredos.",
    ),
]


@dataclass(frozen=True)
class StepResult:
    step: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Scenario:
    slug: str
    title: str
    description: str
    steps: list[str]
    path: Path
    tcc_relation: str
    presentation_note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa cenarios Terraform com falhas controladas e gera relatorios."
    )
    parser.add_argument(
        "--scenario",
        help="Executa apenas um cenario especifico (ex.: 02-ciclo).",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5-coder:1.5b",
        help="Modelo do Ollama a ser usado quando a etapa de IA estiver habilitada.",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434/api/generate",
        help="Endpoint HTTP do Ollama.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=180,
        help="Timeout em segundos para cada chamada ao Ollama.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Gera o relatorio sem consultar o modelo local.",
    )
    return parser.parse_args()


def load_scenarios(selected: str | None) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for directory in sorted(SCENARIOS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        config_path = directory / "cenario.json"
        if not config_path.exists():
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        scenarios.append(
            Scenario(
                slug=directory.name,
                title=config["title"],
                description=config["description"],
                steps=config["steps"],
                path=directory,
                tcc_relation=config["tcc_relation"],
                presentation_note=config["presentation_note"],
            )
        )

    if selected is None:
        return scenarios

    filtered = [scenario for scenario in scenarios if scenario.slug == selected]
    if not filtered:
        available = ", ".join(s.slug for s in scenarios)
        raise SystemExit(
            f"Cenario '{selected}' nao encontrado. Disponiveis: {available or 'nenhum'}."
        )
    return filtered


def terraform_command(step: str) -> list[str]:
    commands = {
        "init": ["terraform", "init", "-input=false", "-no-color"],
        "validate": ["terraform", "validate", "-no-color"],
        "plan": ["terraform", "plan", "-input=false", "-no-color"],
        "apply": ["terraform", "apply", "-input=false", "-auto-approve", "-no-color"],
    }
    try:
        return commands[step]
    except KeyError as exc:
        raise ValueError(f"Etapa Terraform nao suportada: {step}") from exc


def run_step(scenario: Scenario, step: str) -> StepResult:
    command = terraform_command(step)
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    completed = subprocess.run(
        command,
        cwd=scenario.path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return StepResult(
        step=step,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_scenario(scenario: Scenario) -> tuple[list[StepResult], str]:
    results: list[StepResult] = []
    overall_status = "unexpected-success"
    for step in scenario.steps:
        result = run_step(scenario, step)
        results.append(result)
        if result.returncode != 0:
            overall_status = f"failure-captured:{step}"
            break
    return results, overall_status


def format_execution_log(results: list[StepResult]) -> str:
    blocks = []
    for result in results:
        blocks.append(
            textwrap.dedent(
                f"""\
                $ {' '.join(result.command)}
                [exit_code={result.returncode}]
                --- STDOUT ---
                {result.stdout.rstrip() or '(vazio)'}
                --- STDERR ---
                {result.stderr.rstrip() or '(vazio)'}
                """
            ).strip()
        )
    return "\n\n".join(blocks)


def build_prompt(scenario: Scenario, terraform_code: str, execution_log: str) -> str:
    criteria_text = "\n".join(
        f"- {name}: {description}" for name, description in EVALUATION_CRITERIA
    )
    hypotheses_text = "\n".join(f"- {code}: {text}" for code, text in HYPOTHESES)
    return textwrap.dedent(
        f"""\
        Atue como um Engenheiro DevOps Senior.

        Voce recebeu um cenario de laboratorio de TCC com Terraform e o log da execucao.
        Analise apenas o contexto abaixo e responda em portugues.

        Contexto da pesquisa:
        - Problema: {RESEARCH_PROBLEM}
        - Objetivo geral: {GENERAL_OBJECTIVE}
        - Hipoteses:
        {hypotheses_text}
        - Criterios qualitativos de avaliacao:
        {criteria_text}

        Entregue exatamente estas secoes:
        1. CAUSA RAIZ
        2. CORRECAO
        3. TRECHO DE CODIGO SUGERIDO
        4. AVALIACAO DE SEGURANCA
        5. RELACAO COM OS CRITERIOS DO EXPERIMENTO

        Regras:
        - Nao invente dependencias externas.
        - Nao sugira expor segredos, desabilitar validacoes ou usar atalhos inseguros.
        - Baseie a resposta somente no codigo e no log fornecidos.
        - Se faltar contexto para uma correcao completa, diga isso explicitamente.

        [CENARIO]
        {scenario.slug} - {scenario.title}

        [DESCRICAO]
        {scenario.description}

        [RELACAO COM O TCC]
        {scenario.tcc_relation}

        [CODIGO TERRAFORM]
        {terraform_code}

        [LOG DE EXECUCAO]
        {execution_log}
        """
    ).strip()


def call_ollama(prompt: str, model: str, url: str, timeout: int) -> str:
    payload = json.dumps(
        {
            "model": model,
            "stream": False,
            "prompt": prompt,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"O Ollama demorou mais que {timeout}s para responder. "
            "Tente aumentar `--ollama-timeout` ou usar um modelo menor."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Nao foi possivel conectar ao Ollama. Inicie o servico com `ollama serve` "
            "ou rode com `--skip-llm`."
        ) from exc

    if "response" not in body:
        raise RuntimeError(f"Resposta inesperada do Ollama: {body}")
    return str(body["response"]).strip()


def check_ollama(url: str, timeout: int) -> None:
    version_url = url.removesuffix("/api/generate") + "/api/version"
    request = urllib.request.Request(version_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama respondeu com status HTTP {response.status}.")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Nao foi possivel conectar ao Ollama. Inicie o servico com `ollama serve` "
            "ou rode com `--skip-llm`."
        ) from exc


def failure_step_from_status(status: str) -> str:
    if ":" not in status:
        return "nao-detectado"
    return status.split(":", maxsplit=1)[1]


def write_report(
    scenario: Scenario,
    status: str,
    terraform_code: str,
    execution_log: str,
    llm_response: str | None,
) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{scenario.slug}.md"
    llm_section = llm_response or "Etapa de IA nao executada nesta rodada."
    criteria_table = "\n".join(
        f"| {name} | {description} |" for name, description in EVALUATION_CRITERIA
    )
    hypotheses_list = "\n".join(f"- **{code}**: {text}" for code, text in HYPOTHESES)
    report_body = "\n".join(
        [
            f"# Relatorio - {scenario.slug}",
            "",
            f"**Titulo:** {scenario.title}  ",
            f"**Status:** {status}",
            f"**Etapa da falha:** {failure_step_from_status(status)}",
            "",
            "## Enquadramento no TCC",
            "",
            f"**Problema de pesquisa:** {RESEARCH_PROBLEM}",
            "",
            f"**Objetivo geral:** {GENERAL_OBJECTIVE}",
            "",
            "**Hipoteses relacionadas:**",
            hypotheses_list,
            "",
            "## Papel deste cenario no MVP",
            "",
            scenario.description,
            "",
            f"**Relacao com a metodologia:** {scenario.tcc_relation}",
            "",
            f"**Nota para apresentacao:** {scenario.presentation_note}",
            "",
            "## Criterios qualitativos de avaliacao",
            "",
            "| Criterio | Como avaliar |",
            "| --- | --- |",
            criteria_table,
            "",
            "## Codigo Terraform",
            "",
            "```hcl",
            terraform_code.rstrip(),
            "```",
            "",
            "## Log capturado",
            "",
            "```text",
            execution_log.rstrip(),
            "```",
            "",
            "## Analise da IA",
            "",
            llm_section,
            "",
        ]
    )
    report_path.write_text(report_body, encoding="utf-8")
    return report_path


def write_execution_summary(executions: list[dict[str, str]]) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    payload = {
        "research_problem": RESEARCH_PROBLEM,
        "general_objective": GENERAL_OBJECTIVE,
        "hypotheses": [{"code": code, "text": text} for code, text in HYPOTHESES],
        "criteria": [{"name": name, "description": description} for name, description in EVALUATION_CRITERIA],
        "scenarios": executions,
    }
    SUMMARY_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return SUMMARY_FILE


def main() -> int:
    args = parse_args()
    scenarios = load_scenarios(args.scenario)
    if not shutil_which("terraform"):
        raise SystemExit("Terraform nao encontrado no PATH.")
    if not args.skip_llm:
        check_ollama(args.ollama_url, timeout=min(args.ollama_timeout, 30))

    failures = 0
    executions: list[dict[str, str]] = []
    for scenario in scenarios:
        terraform_code = (scenario.path / "main.tf").read_text(encoding="utf-8")
        results, status = run_scenario(scenario)
        execution_log = format_execution_log(results)

        llm_response: str | None = None
        if not args.skip_llm:
            prompt = build_prompt(scenario, terraform_code, execution_log)
            llm_response = call_ollama(
                prompt,
                args.model,
                args.ollama_url,
                args.ollama_timeout,
            )

        report_path = write_report(
            scenario=scenario,
            status=status,
            terraform_code=terraform_code,
            execution_log=execution_log,
            llm_response=llm_response,
        )
        executions.append(
            {
                "slug": scenario.slug,
                "title": scenario.title,
                "status": status,
                "failure_step": failure_step_from_status(status),
                "report_path": str(report_path),
                "tcc_relation": scenario.tcc_relation,
            }
        )

        print(f"[{scenario.slug}] {status} -> {report_path}")
        if not status.startswith("failure-captured"):
            failures += 1

    summary_path = write_execution_summary(executions)
    print(f"[resumo] -> {summary_path}")

    return 1 if failures else 0


def shutil_which(command: str) -> str | None:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path) / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(2)
