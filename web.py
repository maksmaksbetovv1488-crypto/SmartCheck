from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    return "SmartCheck Bot is running successfully!"


def run_flask():
    """Запускает Flask на 0.0.0.0:10000 (нужно для Render)."""
    app.run(host="0.0.0.0", port=10000)
