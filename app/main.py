import os

from flask import Flask, redirect, request, url_for

import config as cfg

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# Paths that do not require configuration to be complete
_SETUP_EXEMPT = {"/setup", "/favicon.ico"}


@app.before_request
def require_config():
    path = request.path
    if path.startswith("/static") or path.startswith("/api/setup"):
        return
    if path in _SETUP_EXEMPT:
        return
    if not cfg.is_configured():
        return redirect(url_for("setup_page"))


# ------------------------------------------------------------------ #
# Basic routes (stubs — expanded in later phases)
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return redirect(url_for("import_page"))


# These stubs are replaced with real implementations in Phase 2+
@app.route("/setup")
def setup_page():
    return "<h2>Setup wizard coming in Phase 2</h2>"


@app.route("/settings")
def settings_page():
    return "<h2>Settings page coming in Phase 2</h2>"


@app.route("/import")
def import_page():
    return "<h2>Import page coming in Phase 3</h2>"


@app.route("/manual")
def manual_page():
    return "<h2>Manual entry coming in Phase 6</h2>"


@app.route("/history")
def history_page():
    return "<h2>History page coming in Phase 6</h2>"


if __name__ == "__main__":
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=5000, debug=debug)
