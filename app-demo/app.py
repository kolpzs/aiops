"""
Aplicação Flask de demonstração para o TCC MVP.
Usada pelos cenários 04-06 para simular falhas de aplicação em containers.
"""

import os
import sys
import time

from flask import Flask, jsonify

app = Flask(__name__)

# Variável de ambiente obrigatória
DB_HOST = os.environ.get("DATABASE_HOST")
if DB_HOST is None:
    print("FATAL: variável de ambiente DATABASE_HOST não definida!", file=sys.stderr)
    print("A aplicação não pode iniciar sem conexão ao banco de dados.", file=sys.stderr)
    time.sleep(3)  # tempo para Terraform detectar a falha
    sys.exit(1)

# Simula conexão com o banco
print(f"Conectando ao banco de dados em {DB_HOST}...")


@app.route("/")
def index():
    return jsonify({"status": "ok", "db_host": DB_HOST})


@app.route("/health")
def health():
    return jsonify({"healthy": True})


if __name__ == "__main__":
    port = int(os.environ.get("APP_PORT", "5000"))
    print(f"Iniciando servidor na porta {port}...")
    app.run(host="0.0.0.0", port=port)
