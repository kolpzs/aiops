"""App que nunca vai rodar — dependência quebrada no requirements.txt."""

from flask import Flask
from flask_inexistente_xyz import MagicPlugin  # noqa: este import nunca será alcançado

app = Flask(__name__)

@app.route("/")
def index():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
