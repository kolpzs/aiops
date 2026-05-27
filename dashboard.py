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
    "validacao_resultado", "validacao_detalhe",
    "hw_id", "compute_unit", "hw_cpu", "hw_gpu", "hw_npu", "hw_ram_gb", "hw_os",
]

HARDWARE_FILE = ROOT / "hardware.json"


# ---------------------------------------------------------------------------
# Hardware detection and catalog
# ---------------------------------------------------------------------------

def _run_cmd(args: list[str], timeout: int = 5, shell: bool = False) -> str:
    """Run a shell command and return stdout, empty string on failure."""
    import platform
    try:
        kwargs = dict(capture_output=True, text=True, timeout=timeout, shell=shell,
                      encoding="utf-8", errors="replace")
        # Windows: suppress console popup window
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(args, **kwargs)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def detect_cpu() -> str:
    """Return CPU model name string (Linux, macOS, Windows)."""
    import platform
    # Linux: lscpu
    out = _run_cmd(["lscpu"])
    for line in out.splitlines():
        if "Model name" in line:
            return line.split(":", 1)[1].strip()
    # Windows: wmic
    if platform.system() == "Windows":
        out_w = _run_cmd(["wmic", "cpu", "get", "Name", "/value"])
        for line in out_w.splitlines():
            if line.startswith("Name="):
                return line.split("=", 1)[1].strip()
        # Fallback: PowerShell
        out_ps = _run_cmd(["powershell", "-NoProfile", "-Command",
                           "(Get-CimInstance Win32_Processor).Name"], timeout=10)
        if out_ps:
            return out_ps.splitlines()[0].strip()
    # macOS
    out2 = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
    if out2:
        return out2
    return platform.processor() or "Unknown CPU"


def detect_gpu() -> str | None:
    """Return GPU name string, or None if not found (Linux, macOS, Windows)."""
    import platform
    # NVIDIA (works on all OS with drivers installed)
    out = _run_cmd(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if out:
        return out.splitlines()[0].strip()
    # Windows: wmic for any GPU
    if platform.system() == "Windows":
        out_w = _run_cmd(["wmic", "path", "Win32_VideoController", "get", "Name", "/value"])
        for line in out_w.splitlines():
            if line.startswith("Name="):
                name = line.split("=", 1)[1].strip()
                if name and "Microsoft" not in name:
                    return name
        # Fallback: PowerShell
        out_ps = _run_cmd(["powershell", "-NoProfile", "-Command",
                           "(Get-CimInstance Win32_VideoController | Where-Object {$_.Name -notlike '*Microsoft*'}).Name"],
                          timeout=10)
        if out_ps:
            return out_ps.splitlines()[0].strip()
    # AMD via rocm-smi (Linux)
    out2 = _run_cmd(["rocm-smi", "--showproductname"])
    if out2:
        for line in out2.splitlines():
            if "Card" in line or "GPU" in line:
                return line.strip()
    # macOS Metal
    out3 = _run_cmd(["system_profiler", "SPDisplaysDataType"])
    if out3:
        for line in out3.splitlines():
            if "Chipset Model" in line:
                return line.split(":", 1)[1].strip()
    return None


def detect_npu() -> str | None:
    """Return NPU description if found, else None.
    
    Checks for:
    - Intel NPU (Meteor Lake / Core Ultra / Arrow Lake) on Linux and Windows
    - AMD Ryzen AI NPU via xdna driver (Linux)
    - Qualcomm NPU via /dev/qaic (Linux)
    """
    import platform
    _os = platform.system()

    # --- Windows: check Device Manager for Intel NPU ---
    if _os == "Windows":
        # Filter by device class to avoid matching keyboards/mice
        # Intel AI Boost NPU is class "System" or "SoftwareDevice"
        out = _run_cmd(["powershell", "-NoProfile", "-Command",
                        "Get-PnpDevice -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.FriendlyName -match 'NPU|Neural|AI Boost' -and "
                        "$_.Class -notmatch 'Keyboard|Mouse|HIDClass|Monitor|USB' } | "
                        "Select-Object -First 1 -ExpandProperty FriendlyName"],
                       timeout=10)
        if out:
            name = out.splitlines()[0].strip()
            # Extra guard: reject names with known non-NPU brands
            if not any(x in name.lower() for x in ["corsair", "logitech", "razer", "steelseries"]):
                return name

    # --- Linux ---
    if _os == "Linux":
        import os as _os_mod
        # Intel NPU (accel driver, kernel 6.7+)
        accel_dir = "/dev/accel"
        if _os_mod.path.isdir(accel_dir) and _os_mod.listdir(accel_dir):
            cpu = detect_cpu()
            return f"Intel NPU ({cpu})"
        # AMD Ryzen AI
        xdna = _run_cmd(["dmesg"])
        if "xdna" in xdna.lower() or "ryzen ai" in xdna.lower():
            return "AMD Ryzen AI NPU"
        # Qualcomm
        if _os_mod.path.exists("/dev/qaic0"):
            return "Qualcomm Cloud AI"

    return None


def detect_ram_gb() -> int:
    """Return total RAM in GB (Linux, macOS, Windows)."""
    import platform
    # Linux
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    return round(kb / 1024 / 1024)
    except Exception:
        pass
    # Windows
    if platform.system() == "Windows":
        out = _run_cmd(["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"])
        for line in out.splitlines():
            if line.startswith("TotalPhysicalMemory="):
                try:
                    return round(int(line.split("=", 1)[1].strip()) / 1024 ** 3)
                except ValueError:
                    pass
        # Fallback: PowerShell
        out_ps = _run_cmd(["powershell", "-NoProfile", "-Command",
                           "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)"],
                          timeout=10)
        if out_ps:
            try:
                return int(out_ps.strip())
            except ValueError:
                pass
    # macOS
    out2 = _run_cmd(["sysctl", "-n", "hw.memsize"])
    if out2:
        return round(int(out2) / 1024 ** 3)
    return 0


def detect_os() -> str:
    """Return OS description string."""
    import platform
    sys = platform.system()
    if sys == "Linux":
        try:
            with open("/etc/os-release") as f:
                info = dict(line.strip().split("=", 1) for line in f if "=" in line)
            name = info.get("PRETTY_NAME", "").strip('"')
            if name:
                return name
        except Exception:
            pass
    elif sys == "Darwin":
        ver = _run_cmd(["sw_vers", "-productVersion"])
        return f"macOS {ver}" if ver else "macOS"
    elif sys == "Windows":
        return f"Windows {platform.version()}"
    return platform.platform()


def detect_compute_unit(npu: str | None, gpu: str | None) -> str:
    """Return 'GPU' or 'CPU' based on what Ollama actually uses.
    
    Ollama does NOT support NPU inference — it uses CUDA (GPU) or CPU.
    NPU is stored separately as informational data for the TCC research.
    """
    if gpu:
        return "GPU"
    return "CPU"


def detect_hardware_snapshot() -> dict:
    """Auto-detect full hardware profile of the current machine."""
    cpu = detect_cpu()
    gpu = detect_gpu()
    npu = detect_npu()
    ram = detect_ram_gb()
    os_name = detect_os()
    unit = detect_compute_unit(npu, gpu)
    return {
        "cpu": cpu,
        "gpu": gpu or "",
        "npu": npu or "",
        "ram_gb": ram,
        "os": os_name,
        "compute_unit": unit,
    }


def load_hardware_catalog() -> dict:
    """Load the hardware catalog JSON (creates empty if not found)."""
    if HARDWARE_FILE.exists():
        try:
            return json.loads(HARDWARE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"machines": {}}


def save_hardware_catalog(catalog: dict) -> None:
    """Persist the hardware catalog JSON."""
    HARDWARE_FILE.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")


def _auto_label(detected: dict) -> str:
    """Generate a human-readable label from hostname + short CPU name."""
    import socket
    hostname = socket.gethostname() or "machine"
    cpu = detected.get("cpu", "")
    # Extract short CPU name: e.g. "Intel Core i7-13620H" -> "i7-13620H"
    import re as _re
    m = _re.search(r"(i[3579]-\w+|Ryzen\s+\w+\s+\w+|Core Ultra \d+ \w+|M[123]\s*\w*|Xeon\s+\w+)", cpu, _re.I)
    short_cpu = m.group(0).strip() if m else cpu.split()[-1]
    return f"{hostname} ({short_cpu})"


def get_or_create_hw_entry(catalog: dict, detected: dict) -> str:
    """Find an existing catalog entry matching the detected hardware, or create one.
    
    Matches by CPU + OS fingerprint. Auto-creates with a descriptive label
    (hostname + CPU model) and full hardware details if no match found.
    Returns the machine id (hw_id).
    """
    import datetime
    import socket as _socket
    hostname = _socket.gethostname()
    # Match by CPU + OS fingerprint
    for hw_id, entry in catalog["machines"].items():
        if entry.get("cpu") == detected["cpu"] and entry.get("os") == detected["os"]:
            # Update compute_unit and gpu only if changed (avoid file write → reload loop)
            changed = False
            new_cu = detected.get("compute_unit", entry.get("compute_unit", "cpu"))
            if entry.get("compute_unit") != new_cu:
                entry["compute_unit"] = new_cu
                changed = True
            if detected.get("gpu") and entry.get("gpu") != detected["gpu"]:
                entry["gpu"] = detected["gpu"]
                changed = True
            if detected.get("npu") and entry.get("npu") != detected.get("npu"):
                entry["npu"] = detected["npu"]
                changed = True
            # Store hostname for fast cache lookup on reconnect
            if not entry.get("hostname"):
                entry["hostname"] = hostname
                changed = True
            if changed:
                save_hardware_catalog(catalog)
            return hw_id
    # Create a new auto entry with descriptive label
    idx = len(catalog["machines"]) + 1
    hw_id = f"machine-{idx:02d}"
    label = _auto_label(detected)
    catalog["machines"][hw_id] = {
        "label": label,
        "hostname": hostname,
        "cpu": detected["cpu"],
        "gpu": detected.get("gpu", ""),
        "npu": detected.get("npu", ""),
        "ram_gb": detected["ram_gb"],
        "os": detected["os"],
        "compute_unit": detected.get("compute_unit", "cpu"),
        "notes": "Auto-detectado na inicialização",
        "registered_at": datetime.datetime.now().isoformat(),
    }
    save_hardware_catalog(catalog)
    return hw_id


def build_ollama_env(compute_unit: str) -> dict:
    """Return os.environ copy with Ollama GPU settings for the desired compute unit.
    
    - GPU  → default (CUDA/ROCm/Metal auto, all layers on GPU)
    - CPU  → OLLAMA_NUM_GPU=0 (forces CPU-only inference)
    - NPU  → Ollama does not natively support NPU yet; falls back to GPU if present,
             otherwise CPU. Sets OLLAMA_NPU_HINT=1 as informational marker.
    """
    env = os.environ.copy()
    if compute_unit == "CPU":
        env["OLLAMA_NUM_GPU"] = "0"
    elif compute_unit == "NPU":
        # NPU not yet natively supported by Ollama — use GPU if available
        env.pop("OLLAMA_NUM_GPU", None)
        env["OLLAMA_NPU_HINT"] = "1"
    else:
        # GPU — remove any forced CPU setting, let Ollama auto-detect
        env.pop("OLLAMA_NUM_GPU", None)
    return env


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
    result = subprocess.run(cmd, cwd=scenario_path, capture_output=True, text=True,
                           env=env, check=False, encoding="utf-8", errors="replace")
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
            encoding="utf-8", errors="replace",
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
        encoding="utf-8", errors="replace",
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
        1. CAUSA RAIZ — explique tecnicamente o que falhou.
        2. CORREÇÃO — descreva a estratégia de correção.
        3. TRECHO DE CÓDIGO SUGERIDO — OBRIGATÓRIO: inclua o arquivo main.tf completo e corrigido dentro de um bloco de código cercado por ```hcl e ```. O bloco DEVE conter todo o código Terraform necessário para corrigir o problema, incluindo terraform {{}}, provider, e todos os resources. NÃO omita nenhuma parte do código.
        4. AVALIAÇÃO DE SEGURANÇA — avalie se a correção é segura.
        5. RELAÇÃO COM OS CRITÉRIOS DO EXPERIMENTO

        REGRAS OBRIGATÓRIAS:
        - Você DEVE incluir um bloco ```hcl com o código Terraform completo corrigido. Sem este bloco, a resposta é inválida.
        - Preserve os mesmos providers do código original (ex: kreuzwerker/docker). NÃO troque para hashicorp/docker.
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


import tempfile as _tempfile


def extract_code_from_ai(ai_response: str) -> str | None:
    """Extract the suggested Terraform code block from the AI response.
    
    Looks for the code block under 'TRECHO DE CÓDIGO SUGERIDO' section,
    then falls back to any fenced code block containing HCL-like content.
    """
    if not ai_response:
        return None

    HCL_KEYWORDS = ("resource", "terraform", "locals", "variable", "provider", "docker", "data", "output", "module")

    # Strategy 1: find code block after "TRECHO DE CÓDIGO SUGERIDO" or similar headers
    pattern = r"(?:TRECHO DE CÓDIGO SUGERIDO|CÓDIGO SUGERIDO|CODIGO SUGERIDO|CÓDIGO CORRIGIDO|CODIGO CORRIGIDO).*?```(?:\w*)\n(.*?)```"
    match = re.search(pattern, ai_response, re.DOTALL | re.IGNORECASE)
    if match:
        code = match.group(1).strip()
        if code and any(kw in code for kw in HCL_KEYWORDS):
            return code

    # Strategy 2: find the largest HCL code block in the response
    blocks = re.findall(r"```(?:hcl|terraform|tf|HCL|Terraform)?\n(.*?)```", ai_response, re.DOTALL)
    if blocks:
        # Return the longest block that looks like valid HCL
        hcl_blocks = [b.strip() for b in blocks
                      if any(kw in b for kw in HCL_KEYWORDS)]
        if hcl_blocks:
            return max(hcl_blocks, key=len)

    # Strategy 3: find ANY code block (no language tag) that looks like HCL
    all_blocks = re.findall(r"```\n(.*?)```", ai_response, re.DOTALL)
    if all_blocks:
        hcl_blocks = [b.strip() for b in all_blocks
                      if any(kw in b for kw in HCL_KEYWORDS)]
        if hcl_blocks:
            return max(hcl_blocks, key=len)

    return None


def validate_ai_suggestion(scenario: dict, ai_response: str) -> tuple[str, str]:
    """Validate the AI's suggested code by running terraform init+validate on it.
    
    Returns (result, detail) where result is one of:
    - 'aprovado'       : terraform validate passed on the suggested code
    - 'reprovado'      : terraform validate failed (AI suggestion has errors)
    - 'parcial'        : init passed but validate failed (partial fix)
    - 'sem_codigo'     : no code block could be extracted from AI response
    - 'erro_validacao' : unexpected error during validation process
    """
    code = extract_code_from_ai(ai_response)
    if not code:
        return ("sem_codigo", "Nenhum bloco de codigo extraido da resposta da IA")

    # Create a temporary directory with the suggested code
    try:
        with _tempfile.TemporaryDirectory(prefix="aiops_val_") as tmpdir:
            main_tf = Path(tmpdir) / "main.tf"
            main_tf.write_text(code, encoding="utf-8")

            env = os.environ.copy()
            env["TF_IN_AUTOMATION"] = "1"

            # Step 1: terraform init
            init_result = subprocess.run(
                ["terraform", "init", "-input=false", "-no-color", "-backend=false"],
                cwd=tmpdir, capture_output=True, text=True,
                env=env, check=False, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if init_result.returncode != 0:
                err = (init_result.stderr or init_result.stdout or "").strip()[:200]
                return ("reprovado", f"init falhou: {err}")

            # Step 2: terraform validate
            val_result = subprocess.run(
                ["terraform", "validate", "-no-color"],
                cwd=tmpdir, capture_output=True, text=True,
                env=env, check=False, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if val_result.returncode == 0:
                return ("aprovado", "Codigo sugerido pela IA passou em terraform validate")
            else:
                err = (val_result.stderr or val_result.stdout or "").strip()[:200]
                return ("parcial", f"validate falhou: {err}")

    except subprocess.TimeoutExpired:
        return ("erro_validacao", "Timeout durante validacao do codigo sugerido")
    except Exception as exc:
        return ("erro_validacao", f"Erro inesperado: {exc}")


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
    relatorio_path TEXT,
    validacao_resultado TEXT,
    validacao_detalhe TEXT,
    hw_id TEXT,
    compute_unit TEXT,
    hw_cpu TEXT,
    hw_gpu TEXT,
    hw_npu TEXT,
    hw_ram_gb INTEGER,
    hw_os TEXT
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
    hw_snapshot: dict | None = None,
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

    # --- Automatic validation of AI-suggested code ---
    validacao_resultado = ""
    validacao_detalhe = ""
    if ai_response:
        log_container.write("Validando codigo sugerido pela IA...")
        add_activity("VAL", f"[{scenario['slug']}] Validando sugestao da IA")
        validacao_resultado, validacao_detalhe = validate_ai_suggestion(scenario, ai_response)
        if validacao_resultado == "aprovado":
            log_container.success(f"Validacao: APROVADO — {validacao_detalhe}")
            add_activity("VAL", f"[{scenario['slug']}] Validacao: APROVADO")
        elif validacao_resultado == "sem_codigo":
            log_container.warning(f"Validacao: SEM CODIGO — {validacao_detalhe}")
            add_activity("VAL", f"[{scenario['slug']}] Validacao: sem codigo extraido")
        else:
            log_container.error(f"Validacao: {validacao_resultado.upper()} — {validacao_detalhe}")
            add_activity("VAL", f"[{scenario['slug']}] Validacao: {validacao_resultado}")

    report_path = save_report(scenario, status_str, scenario["tf_code"], exec_log, ai_response)
    log_container.info(f"Relatorio: `{report_path.relative_to(ROOT)}`")
    add_activity("REL", f"[{scenario['slug']}] Relatorio salvo: {report_path.name}")

    # Cleanup Docker resources
    if uses_docker:
        log_container.write("Limpando recursos Docker...")
        add_activity("CLN", f"[{scenario['slug']}] Limpando recursos Docker")
        cleanup_msg = terraform_cleanup(scenario["path"])
        log_container.write(cleanup_msg)

    hw = hw_snapshot or {}
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
        "validacao_resultado": validacao_resultado,
        "validacao_detalhe": validacao_detalhe,
        "hw_id": hw.get("hw_id", ""),
        "compute_unit": hw.get("compute_unit", ""),
        "hw_cpu": hw.get("cpu", ""),
        "hw_gpu": hw.get("gpu", ""),
        "hw_npu": hw.get("npu", ""),
        "hw_ram_gb": hw.get("ram_gb", ""),
        "hw_os": hw.get("os", ""),
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
def render_sidebar(hw_snapshot: dict | None = None):
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

        # Hardware badge in sidebar
        if hw_snapshot:
            st.divider()
            st.header("💻 Hardware Detectado")
            cu = hw_snapshot.get("compute_unit", "cpu").upper()
            badge_color = {"NPU": "🟣", "GPU": "🟢", "CPU": "🔵"}.get(cu, "⚪")
            label_disp = hw_snapshot.get("label", hw_snapshot.get("hw_id", "?"))
            st.markdown(f"{badge_color} **Compute:** {cu} | **{label_disp}** (`{hw_snapshot.get('hw_id','?')}`)")
            st.caption(f"**CPU:** {hw_snapshot.get('cpu','?')[:40]}")
            if hw_snapshot.get("gpu"):
                st.caption(f"**GPU:** {hw_snapshot['gpu'][:40]}")
            if hw_snapshot.get("npu"):
                st.caption(f"**NPU:** {hw_snapshot['npu'][:40]}")
            st.caption(f"**RAM:** {hw_snapshot.get('ram_gb','?')} GB | **OS:** {hw_snapshot.get('os','?')[:30]}")
            if cu == "GPU":
                st.info("Para forçar CPU: reinicie Ollama com `OLLAMA_NUM_GPU=0`", icon="ℹ️")

    return ollama_url, model, timeout, is_online, pg_dsn


# ---------------------------------------------------------------------------
# UI: Tab - Individual scenario
# ---------------------------------------------------------------------------
def render_scenario_tab(scenario: dict, model: str, ollama_url: str, timeout: int, hw_snapshot: dict | None = None):
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
            execute_scenario(scenario, model, ollama_url, timeout, skip_llm, st, hw_snapshot=hw_snapshot)


# ---------------------------------------------------------------------------
# UI: Tab - Run All
# ---------------------------------------------------------------------------
def render_run_all_tab(scenarios: list[dict], model: str, ollama_url: str, timeout: int, pg_dsn: str = "", hw_snapshot: dict | None = None):
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
                        scenario, model, ollama_url, timeout, skip_llm, st, pg_dsn=pg_dsn, hw_snapshot=hw_snapshot,
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
# PDF Export helper
# ---------------------------------------------------------------------------
def _generate_results_pdf(data, ai_rows_clean, models_sorted, colors):
    """Build a PDF report from the results tab data and charts."""
    import io
    import plotly.graph_objects as go
    from statistics import mean, median, mode, stdev
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, "Resultados - TCC MVP AIOps", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # ── Summary table ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 9, "Estatisticas por Modelo", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)

    col_w = [52, 22, 22, 22, 22, 22, 22]
    headers = ["Modelo", "Execucoes", "Min(s)", "Max(s)", "Media(s)", "Mediana(s)", "Moda(s)"]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, align="C")
    pdf.ln()
    for m in models_sorted:
        times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        if not times:
            continue
        try:
            m_mode = f"{mode(times):.1f}"
        except Exception:
            m_mode = "N/A"
        row_vals = [m, str(len(times)), f"{min(times):.1f}", f"{max(times):.1f}",
                    f"{mean(times):.1f}", f"{median(times):.1f}", m_mode]
        for i, v in enumerate(row_vals):
            pdf.cell(col_w[i], 7, v, border=1, align="C")
        pdf.ln()

    pdf.ln(6)

    # ── Charts ─────────────────────────────────────────────────────────────────
    def _fig_to_bytes(fig):
        return fig.to_image(format="png", width=900, height=400, engine="kaleido")

    # Box plot — response time
    fig_box = go.Figure()
    for idx, m in enumerate(models_sorted):
        times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        fig_box.add_trace(go.Box(y=times, name=m, marker_color=colors[idx % len(colors)], boxmean="sd"))
    fig_box.update_layout(title="Box Plot - Tempo de Resposta por Modelo", yaxis_title="Tempo (s)", height=400)

    # Violin plot — tokens
    fig_violin = go.Figure()
    for idx, m in enumerate(models_sorted):
        toks = [int(r["tokens_estimados"]) for r in ai_rows_clean if r["modelo"] == m and int(r["tokens_estimados"]) > 0]
        fig_violin.add_trace(go.Violin(y=toks, name=m, box_visible=True, meanline_visible=True,
                                       fillcolor=colors[idx % len(colors)], opacity=0.7, line_color="black"))
    fig_violin.update_layout(title="Violin Plot - Tokens por Modelo", yaxis_title="Tokens estimados", height=400)

    for fig, title in [(fig_box, "Box Plot - Tempo de Resposta"), (fig_violin, "Violin Plot - Tokens")]:
        img_bytes = _fig_to_bytes(fig)
        img_buf = io.BytesIO(img_bytes)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
        # Save temp image
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        pdf.image(tmp_path, x=10, y=None, w=190)
        import os as _os
        _os.unlink(tmp_path)

    # Per-model histograms
    for idx, m in enumerate(models_sorted):
        m_times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        if not m_times:
            continue
        fig_h = go.Figure()
        fig_h.add_trace(go.Histogram(x=m_times, nbinsx=30, marker_color=colors[idx % len(colors)], opacity=0.75))
        m_mean = mean(m_times)
        m_median = median(m_times)
        fig_h.add_vline(x=m_mean, line_dash="dash", line_color="#1E88E5", line_width=2,
                        annotation_text=f"Media: {m_mean:.1f}s")
        fig_h.add_vline(x=m_median, line_dash="dot", line_color="#43A047", line_width=2,
                        annotation_text=f"Mediana: {m_median:.1f}s")
        fig_h.update_layout(title=f"Histograma - {m}", xaxis_title="Tempo (s)", yaxis_title="Frequencia", height=350)
        img_bytes = _fig_to_bytes(fig_h)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 9, f"Histograma - {m}", new_x="LMARGIN", new_y="NEXT")
        pdf.image(tmp_path, x=10, y=None, w=190)
        import os as _os
        _os.unlink(tmp_path)

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
def render_results_tab():
    try:
        import plotly.graph_objects as go
        import plotly.express as px
        from statistics import mean, median, mode, stdev
        HAS_PLOTLY = True
    except ImportError:
        HAS_PLOTLY = False

    try:
        from fpdf import FPDF
        import kaleido  # noqa: F401
        HAS_PDF = True
    except ImportError:
        HAS_PDF = False

    st.subheader("Resultados e Análise Estatística")

    data = load_csv_results()
    if not data:
        st.info("Nenhuma execução registrada ainda. Execute cenários para gerar dados.")
        return

    ai_rows = [r for r in data if r.get("ia_executada") == "sim"]
    all_rows = data

    # --- Metrics row ---
    st.markdown("### Metricas Gerais")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.metric("Total execucoes", len(all_rows))
    with m2:
        st.metric("Com IA", len(ai_rows))
    with m3:
        models_used = set(r["modelo"] for r in ai_rows)
        st.metric("Modelos", len(models_used))
    with m4:
        scenarios_used = set(r["cenario"] for r in ai_rows)
        st.metric("Cenarios", len(scenarios_used))
    with m5:
        if ai_rows:
            avg_ai = mean([float(r["tempo_ia_s"]) for r in ai_rows])
            st.metric("Media IA (s)", f"{avg_ai:.1f}")
        else:
            st.metric("Media IA (s)", "N/A")
    with m6:
        if ai_rows:
            avg_tok = mean([int(r["tokens_estimados"]) for r in ai_rows])
            st.metric("Media tokens", f"{avg_tok:.0f}")
        else:
            st.metric("Media tokens", "N/A")

    # --- Validation metrics ---
    validated_rows = [r for r in ai_rows if r.get("validacao_resultado")]
    if validated_rows:
        st.markdown("### Validacao Automatica")
        v1, v2, v3, v4 = st.columns(4)
        approved = sum(1 for r in validated_rows if r["validacao_resultado"] == "aprovado")
        failed = sum(1 for r in validated_rows if r["validacao_resultado"] in ("reprovado", "parcial"))
        no_code = sum(1 for r in validated_rows if r["validacao_resultado"] == "sem_codigo")
        errors = sum(1 for r in validated_rows if r["validacao_resultado"] == "erro_validacao")
        with v1:
            pct = (approved / len(validated_rows) * 100) if validated_rows else 0
            st.metric("Aprovados", f"{approved} ({pct:.0f}%)")
        with v2:
            st.metric("Reprovados/Parcial", str(failed))
        with v3:
            st.metric("Sem codigo", str(no_code))
        with v4:
            st.metric("Erros validacao", str(errors))

        # Per-model validation table
        if len(models_used) > 1:
            st.markdown("#### Taxa de aprovacao por modelo")
            model_val = {}
            for r in validated_rows:
                m = r["modelo"]
                if m not in model_val:
                    model_val[m] = {"total": 0, "aprovado": 0}
                model_val[m]["total"] += 1
                if r["validacao_resultado"] == "aprovado":
                    model_val[m]["aprovado"] += 1
            val_table = []
            for m, v in sorted(model_val.items()):
                pct = (v["aprovado"] / v["total"] * 100) if v["total"] else 0
                val_table.append({"Modelo": m, "Total": v["total"], "Aprovados": v["aprovado"], "Taxa (%)": f"{pct:.1f}"})
            st.table(val_table)

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

    # ── Estatísticas Descritivas por IA ─────────────────────────────────────
    st.markdown("---")
    st.markdown("### Estatísticas Descritivas")

    def calc_stats(values, label, model_name="Global"):
        if not values:
            return {}
        try:
            m = mode(values)
        except Exception:
            m = "N/A"
        return {
            "IA / Métrica": f"{model_name} — {label}",
            "N": len(values),
            "Média": f"{mean(values):.2f}",
            "Mediana": f"{median(values):.2f}",
            "Moda": f"{m:.2f}" if isinstance(m, (int, float)) else str(m),
            "Desvio Padrão": f"{stdev(values):.2f}" if len(values) > 1 else "N/A",
            "Mín": f"{min(values):.2f}",
            "Máx": f"{max(values):.2f}",
        }

    stats_table = []
    # Global rows
    stats_table.append(calc_stats([float(r["tempo_ia_s"]) for r in ai_rows if float(r["tempo_ia_s"]) > 0], "Tempo IA (s)"))
    stats_table.append(calc_stats([int(r["tokens_estimados"]) for r in ai_rows if int(r["tokens_estimados"]) > 0], "Tokens gerados"))
    stats_table.append(calc_stats([int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
                    for r in ai_rows if float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0], "Tokens/segundo"))
    stats_table.append(calc_stats([float(r["tempo_terraform_s"]) for r in ai_rows], "Tempo Terraform (s)"))
    # Per-model rows
    for m_name in sorted(set(r["modelo"] for r in ai_rows)):
        m_rows = [r for r in ai_rows if r["modelo"] == m_name]
        stats_table.append(calc_stats([float(r["tempo_ia_s"]) for r in m_rows if float(r["tempo_ia_s"]) > 0], "Tempo IA (s)", m_name))
        stats_table.append(calc_stats([int(r["tokens_estimados"]) for r in m_rows if int(r["tokens_estimados"]) > 0], "Tokens gerados", m_name))
        stats_table.append(calc_stats([int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
                        for r in m_rows if float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0], "Tokens/segundo", m_name))

    st.dataframe([s for s in stats_table if s], width="stretch", hide_index=True)

    # ── Estatísticas por Modelo (resumo compacto) ────────────────────────────
    st.markdown("### Estatísticas por Modelo")
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
    st.markdown("### 1. Box Plot — Tempo de Resposta da IA por Modelo")
    st.caption("Mostra mediana, quartis e outliers para cada modelo.")

    fig_box = go.Figure()
    models_sorted = sorted(set(r["modelo"] for r in ai_rows_clean))

    # Each chart gets its OWN color palette so they look visually distinct
    # Model index stays consistent within each chart for readability
    palettes = {
        "box_time":    ["#E53935", "#D81B60", "#8E24AA", "#3949AB", "#00897B", "#F4511E"],
        "violin":      ["#1565C0", "#0277BD", "#00838F", "#2E7D32", "#558B2F", "#F57F17"],
        "hist_model":  ["#6A1B9A", "#4527A0", "#283593", "#1565C0", "#0277BD", "#00695C"],
        "hist_tks":    ["#E65100", "#BF360C", "#4E342E", "#37474F", "#1A237E", "#880E4F"],
        "bar":         ["#F9A825", "#F57F17", "#E65100", "#BF360C", "#880E4F", "#4A148C"],
        "box_tf":      ["#00695C", "#1B5E20", "#33691E", "#827717", "#FF6F00", "#E65100"],
        "scatter":     ["#7B1FA2", "#512DA8", "#1976D2", "#0288D1", "#00796B", "#388E3C"],
        "radar":       ["#C62828", "#283593", "#1B5E20", "#F57F17", "#4A148C", "#006064"],
    }
    # Default for any chart not listed above (fallback)
    colors = palettes["box_time"]

    for idx, m in enumerate(models_sorted):
        times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        fig_box.add_trace(go.Box(
            y=times, name=m,
            marker_color=palettes["box_time"][idx % len(palettes["box_time"])],
            boxmean="sd",
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
    st.markdown("### 2. Violin Plot — Distribuição de Tokens por Modelo")
    st.caption("Densidade de probabilidade + box plot interno. Revela bimodalidades.")

    fig_violin = go.Figure()
    for idx, m in enumerate(models_sorted):
        toks = [int(r["tokens_estimados"]) for r in ai_rows_clean if r["modelo"] == m and int(r["tokens_estimados"]) > 0]
        fig_violin.add_trace(go.Violin(
            y=toks, name=m,
            box_visible=True,
            meanline_visible=True,
            fillcolor=palettes["violin"][idx % len(palettes["violin"])],
            opacity=0.8,
            line=dict(color=palettes["violin"][idx % len(palettes["violin"])], width=2),
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
    st.markdown("### 2.5. Histograma — Distribuição do Tempo de Resposta por Modelo")
    st.caption("Barras mostram frequência. Linhas verticais: média (tracejada), mediana (pontilhada), moda (sólida).")

    for idx, m in enumerate(models_sorted):
        m_times = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        if not m_times:
            continue
        fig_hist_m = go.Figure()
        fig_hist_m.add_trace(go.Histogram(
            x=m_times, name=m,
            nbinsx=30,
            marker_color=palettes["hist_model"][idx % len(palettes["hist_model"])],
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
    st.markdown("### 3. Histograma — Eficiência (tokens/s) com Média e Mediana")
    st.caption("Linhas verticais: média (azul), mediana (verde), moda (vermelho).")

    fig_hist = go.Figure()
    for idx, m in enumerate(models_sorted):
        tps = [int(r["tokens_estimados"]) / float(r["tempo_ia_s"])
               for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0 and int(r["tokens_estimados"]) > 0]
        fig_hist.add_trace(go.Histogram(
            x=tps, name=m,
            opacity=0.65,
            nbinsx=25,
            marker_color=palettes["hist_tks"][idx % len(palettes["hist_tks"])],
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
    st.markdown("### 4. Heatmap — Tempo médio da IA (Modelo × Cenário)")
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
    st.markdown("### 5. Heatmap — Tokens gerados (Modelo × Cenário)")
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
        colorscale="Viridis",
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
    st.markdown("### 6. Eficiência por Modelo × Cenário (tok/s)")
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
            marker_color=palettes["bar"][idx % len(palettes["bar"])],
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
    st.markdown("### 7. Box Plot — Tempo Terraform por Cenário")
    st.caption("Variabilidade do pipeline de IaC. Cenários Docker tendem a ser mais lentos.")

    fig_tf_box = go.Figure()
    for idx, c in enumerate(cenarios_sorted):
        vals = [float(r["tempo_terraform_s"]) for r in ai_rows_clean if r["cenario"] == c]
        fig_tf_box.add_trace(go.Box(
            y=vals, name=c,
            marker_color=palettes["box_tf"][idx % len(palettes["box_tf"])],
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
    st.markdown("### 8. Correlação — Tempo de IA vs Tokens Gerados")
    st.caption("Esperado: relação linear (mais tokens = mais tempo). Desvios indicam variação na velocidade.")

    fig_scatter = go.Figure()
    for idx, m in enumerate(models_sorted):
        xs = [float(r["tempo_ia_s"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        ys = [int(r["tokens_estimados"]) for r in ai_rows_clean if r["modelo"] == m and float(r["tempo_ia_s"]) > 0]
        fig_scatter.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers",
            name=m,
            marker=dict(size=6, color=palettes["scatter"][idx % len(palettes["scatter"])], opacity=0.6),
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
    st.markdown("### 9. Radar — Perfil Comparativo dos Modelos")
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

    # Bright neon colors with solid fill on dark bg for radar readability
    radar_colors = ["#00E5FF", "#FF6D00", "#76FF03", "#EA80FC", "#FFD740", "#FF1744"]

    fig_radar = go.Figure()
    for idx, m in enumerate(models_sorted):
        if m not in model_metrics:
            continue
        values = [model_metrics[m][cat] / max_vals[cat] for cat in categories]
        values.append(values[0])  # close the polygon
        col = radar_colors[idx % len(radar_colors)]
        fig_radar.add_trace(go.Scatterpolar(
            r=values,
            theta=categories + [categories[0]],
            name=m,
            fill="toself",
            opacity=0.35,
            line=dict(color=col, width=3),
            fillcolor=col,
        ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 1], gridcolor="rgba(255,255,255,0.2)", tickfont=dict(size=10)),
            angularaxis=dict(gridcolor="rgba(255,255,255,0.15)"),
            bgcolor="rgba(30,30,40,0.85)",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        height=480,
        margin=dict(t=50, b=50),
        legend=dict(orientation="h", y=-0.12, font=dict(size=12)),
    )
    st.plotly_chart(fig_radar, width="stretch")

    st.markdown("---")

    # --- Tabela histórico + download CSV + PDF ---
    st.markdown("### Historico completo")
    st.dataframe(data, width="stretch", hide_index=True)

    dl_col1, dl_col2 = st.columns(2)

    with dl_col1:
        csv_content = CSV_FILE.read_text(encoding="utf-8")
        if st.download_button(
            "Baixar CSV completo",
            data=csv_content,
            file_name="resultados_tcc_mvp.csv",
            mime="text/csv",
            width="stretch",
        ):
            add_activity("", "CSV de resultados baixado")

    with dl_col2:
        if HAS_PDF and HAS_PLOTLY:
            if st.button("Gerar PDF dos Resultados", width="stretch"):
                with st.spinner("Gerando PDF..."):
                    pdf_bytes = _generate_results_pdf(data, ai_rows_clean, models_sorted, colors)
                st.download_button(
                    "Baixar PDF",
                    data=pdf_bytes,
                    file_name="resultados_tcc_mvp.pdf",
                    mime="application/pdf",
                    width="stretch",
                )
        else:
            st.info("Instale fpdf2 e kaleido para exportar PDF.")


# ---------------------------------------------------------------------------
# UI: Tab - Automation (overnight batch run)
# ---------------------------------------------------------------------------
def render_automation_tab(scenarios: list[dict], ollama_url: str, timeout: int, pg_dsn: str = "", hw_snapshot: dict | None = None):
    st.subheader("🤖 Automação")
    st.markdown(
        "Roda **todos os cenários** com **todos os modelos instalados** N vezes cada, "
        "coletando dados em massa para análise estatística do TCC."
    )

    # Show completion banner from previous run (stored before st.rerun() to avoid WS overflow)
    _last = st.session_state.pop("auto_last_result", None)
    if _last:
        if _last.get("completed"):
            st.success(
                f"🎉 Automação concluída! {_last['total_exec']:,} execuções com "
                f"{_last['models_count']} modelo(s) × {_last['iterations']} iterações."
            )
        else:
            st.warning(f"⏹️ Automação interrompida após {_last.get('total_exec', 0):,} execuções.")

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

        try:
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
                                hw_snapshot=hw_snapshot,
                            )

                    runs_done += 1
                    pct_overall = runs_done / total_runs
                    overall_bar.progress(
                        min(pct_overall, 1.0),
                        text=f"Total: {runs_done}/{total_runs} ({pct_overall*100:.1f}%) — iter {iteration_idx+1} / modelo {auto_model}",
                    )

        except Exception as e:
            completed_normally = False
            stop_reason = f"erro: {e}"
            logger.error(f"❌ Erro na automação: {e}")
            st.error(f"❌ Erro durante automação: {e}")
        finally:
            st.session_state.auto_running = False

        if completed_normally:
            total_exec = runs_done * len(scenarios)
            add_activity("🎉", f"Automação concluída: {total_exec:,} execuções totais")
            logger.info(f"🎉 Automação finalizada: {total_exec} execuções")
            st.session_state["auto_last_result"] = {
                "completed": True,
                "total_exec": total_exec,
                "models_count": len(selected_models),
                "iterations": last_iteration_idx + 1,
            }
        else:
            total_exec = runs_done * len(scenarios)
            add_activity("⏹️", f"Automação interrompida: {total_exec:,} execuções realizadas")
            st.session_state["auto_last_result"] = {
                "completed": False,
                "total_exec": total_exec,
                "models_count": len(selected_models),
                "iterations": last_iteration_idx + 1,
            }
        # Trigger a clean rerun to clear accumulated UI state from the long automation.
        # This prevents a giant WebSocket flush that causes browser disconnect / white screen.
        st.rerun()


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
# UI: Tab - Hardware
# ---------------------------------------------------------------------------
def render_hardware_tab():
    st.subheader("💻 Hardware Registrado")

    hw_snapshot = st.session_state.get("hw_snapshot", {})
    catalog = st.session_state.get("hw_catalog", load_hardware_catalog())

    # Current machine banner
    if hw_snapshot:
        cu = hw_snapshot.get("compute_unit", "cpu").upper()
        badge = {"NPU": "🟣", "GPU": "🟢", "CPU": "🔵"}.get(cu, "⚪")
        label_disp = hw_snapshot.get("label", hw_snapshot.get("hw_id", "?"))
        st.info(
            f"{badge} **{label_disp}** (`{hw_snapshot.get('hw_id','?')}`) — "
            f"Compute: **{cu}** | CPU: {hw_snapshot.get('cpu','?')[:50]} | "
            f"RAM: {hw_snapshot.get('ram_gb','?')} GB | OS: {hw_snapshot.get('os','?')[:30]}",
            icon="💻",
        )

    # Catalog table
    machines = catalog.get("machines", {})
    if machines:
        st.markdown("### Catálogo de Máquinas")
        rows = []
        for hw_id, m in machines.items():
            rows.append({
                "ID": hw_id,
                "Label": m.get("label", hw_id),
                "CPU": m.get("cpu", "?")[:50],
                "GPU": m.get("gpu", "—"),
                "NPU": m.get("npu", "—") or "—",
                "RAM (GB)": m.get("ram_gb", "?"),
                "OS": m.get("os", "?")[:30],
                "Compute": m.get("compute_unit", "cpu").upper(),
                "Registrada em": m.get("registered_at", "?")[:10],
            })
        st.dataframe(rows, width="stretch")
    else:
        st.caption("Nenhuma máquina registrada ainda.")

    st.divider()
    st.markdown("### Registrar nova máquina manualmente")
    with st.form("form_hw"):
        c1, c2 = st.columns(2)
        with c1:
            new_label = st.text_input("Label (ex: notebook-pessoal)", placeholder="notebook-pessoal")
            new_cpu = st.text_input("CPU", placeholder="Intel Core i7-13620H")
            new_gpu = st.text_input("GPU (opcional)", placeholder="NVIDIA RTX 4050 6GB")
            new_npu = st.text_input("NPU (opcional)", placeholder="Intel AI Boost")
        with c2:
            new_ram = st.number_input("RAM (GB)", min_value=1, max_value=1024, value=16)
            new_os = st.text_input("Sistema Operacional", placeholder="Ubuntu 24.04")
            new_compute = st.selectbox("Compute padrão", ["gpu", "cpu", "npu"])
            new_notes = st.text_area("Notas (opcional)", placeholder="Uso em casa, kernel 6.17")

        submitted = st.form_submit_button("✅ Registrar", type="primary")
        if submitted and new_label and new_cpu:
            import datetime
            new_id = new_label.lower().replace(" ", "-")
            catalog.setdefault("machines", {})[new_id] = {
                "label": new_label,
                "cpu": new_cpu,
                "gpu": new_gpu,
                "npu": new_npu,
                "ram_gb": int(new_ram),
                "os": new_os,
                "compute_unit": new_compute,
                "notes": new_notes,
                "registered_at": datetime.datetime.now().isoformat(),
            }
            save_hardware_catalog(catalog)
            st.session_state["hw_catalog"] = catalog
            st.success(f"✅ Máquina `{new_id}` registrada!")
            add_activity("💻", f"Máquina '{new_id}' registrada no catálogo")
            st.rerun()
        elif submitted:
            st.warning("Label e CPU são obrigatórios.")


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
    if not st.session_state.get("_dashboard_loaded"):
        add_activity("🔬", "Dashboard carregado")
        st.session_state["_dashboard_loaded"] = True

    # --- Hardware detection (once per session) ---
    # On WebSocket reconnect (after long automation), session_state is cleared.
    # We check hardware.json by hostname first to avoid re-running WMI/PowerShell,
    # which can hang and crash the Streamlit server.
    if "hw_snapshot" not in st.session_state:
        import socket as _socket
        hostname = _socket.gethostname()
        catalog = load_hardware_catalog()

        # Fast path: find cached entry by hostname (skips all WMI queries)
        cached_id, cached_entry = None, None
        for _hw_id, _entry in catalog["machines"].items():
            if (_entry.get("hostname") == hostname or
                    _entry.get("label", "").startswith(hostname + " ")):
                cached_id, cached_entry = _hw_id, _entry
                break

        if cached_entry:
            detected = {
                "cpu": cached_entry.get("cpu", "Unknown"),
                "gpu": cached_entry.get("gpu", ""),
                "npu": cached_entry.get("npu", ""),
                "ram_gb": cached_entry.get("ram_gb", 0),
                "os": cached_entry.get("os", "Unknown"),
                "compute_unit": cached_entry.get("compute_unit", "CPU"),
                "hw_id": cached_id,
                "label": cached_entry.get("label", cached_id),
            }
            st.session_state["hw_snapshot"] = detected
            st.session_state["hw_catalog"] = catalog
            add_activity("💻", f"Hardware carregado: {detected.get('label','?')}")
        else:
            # Slow path: run full hardware detection (WMI/PowerShell) for new machines
            try:
                detected = detect_hardware_snapshot()
                hw_id = get_or_create_hw_entry(catalog, detected)
                detected["hw_id"] = hw_id
                detected["label"] = catalog["machines"][hw_id].get("label", hw_id)
                st.session_state["hw_snapshot"] = detected
                st.session_state["hw_catalog"] = catalog
                add_activity("💻", f"Hardware detectado: {detected.get('label','?')}")
            except Exception as _hw_err:
                st.session_state["hw_snapshot"] = {
                    "cpu": "Unknown", "gpu": "", "npu": "", "ram_gb": 0,
                    "os": "Unknown", "compute_unit": "CPU", "hw_id": "unknown",
                    "label": "unknown",
                }
                st.session_state["hw_catalog"] = {"machines": {}}
                add_activity("⚠️", f"Falha na detecção de hardware: {_hw_err}")

    hw_snapshot = st.session_state["hw_snapshot"]

    ollama_url, model, timeout, is_online, pg_dsn = render_sidebar(hw_snapshot)

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
    tab_names.append("💻 Hardware")

    tabs = st.tabs(tab_names)

    # Individual scenario tabs
    for i, scenario in enumerate(scenarios):
        with tabs[i]:
            render_scenario_tab(scenario, model, ollama_url, timeout, hw_snapshot)

    # Run All tab
    with tabs[len(scenarios)]:
        render_run_all_tab(scenarios, model, ollama_url, timeout, pg_dsn=pg_dsn, hw_snapshot=hw_snapshot)

    # Automation tab
    with tabs[len(scenarios) + 1]:
        render_automation_tab(scenarios, ollama_url, timeout, pg_dsn=pg_dsn, hw_snapshot=hw_snapshot)

    # Results tab
    with tabs[len(scenarios) + 2]:
        render_results_tab()

    # Reports tab
    with tabs[len(scenarios) + 3]:
        render_reports_tab()

    # Activity Log tab
    with tabs[len(scenarios) + 4]:
        render_activity_log_tab()

    # Hardware tab
    with tabs[len(scenarios) + 5]:
        render_hardware_tab()

    # Footer
    st.divider()
    st.caption(
        "TCC — Análise Estruturada de Logs em Infraestrutura como Código: "
        "Um Método Baseado em IA Generativa para Otimização DevOps"
    )


if __name__ == "__main__":
    main()
