import os
import time

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import config as cfg
from baserow_client import BaserowAuthError, BaserowAPIError, BaserowClient

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# Make is_configured available in all templates automatically
@app.context_processor
def inject_globals():
    return {"is_configured": cfg.is_configured()}

# Paths that do not require configuration to be complete
_SETUP_EXEMPT_PREFIXES = ("/static", "/api/setup", "/setup")


@app.before_request
def require_config():
    path = request.path
    for prefix in _SETUP_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return
    if path in ("/favicon.ico",):
        return
    if not cfg.is_configured():
        return redirect(url_for("setup_page"))


def _make_client() -> BaserowClient:
    c = cfg.load_config()
    return BaserowClient(c["baserow_url"], c["baserow_email"], c["baserow_password"])


# ------------------------------------------------------------------ #
# Main pages
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return redirect(url_for("import_page"))


@app.route("/import")
def import_page():
    return render_template("upload.html")


@app.route("/manual")
def manual_page():
    return render_template("manual_entry.html", units_list=[], positions_list=[])


@app.route("/history")
def history_page():
    return render_template("history.html", rows=[])


# ------------------------------------------------------------------ #
# Setup wizard — GET
# ------------------------------------------------------------------ #

@app.route("/setup")
def setup_page():
    prefill = {
        "url": os.environ.get("BASEROW_URL", ""),
        "email": os.environ.get("BASEROW_EMAIL", ""),
        "password": os.environ.get("BASEROW_PASSWORD", ""),
    }
    return render_template("setup.html", prefill=prefill)


# ------------------------------------------------------------------ #
# Setup wizard — API endpoints
# ------------------------------------------------------------------ #

@app.route("/api/setup/connect", methods=["POST"])
def api_setup_connect():
    from flask import session
    body = request.get_json(force=True) or {}
    url = body.get("url", "").strip().rstrip("/")
    email = body.get("email", "").strip()
    password = body.get("password", "")
    if not url or not email or not password:
        return jsonify(error="URL, email, and password are required."), 400
    try:
        client = BaserowClient(url, email, password)
        apps = client.get_applications()
    except BaserowAuthError as e:
        return jsonify(error=str(e)), 401
    except Exception as e:
        return jsonify(error=f"Connection error: {e}"), 400

    # Persist credentials in session so fetch-tables can reuse them
    session["setup_url"] = url
    session["setup_email"] = email
    session["setup_password"] = password

    databases = [
        {"id": a["id"], "name": a["name"]}
        for a in apps
        if a.get("type") == "database"
    ]
    return jsonify(databases=databases)


@app.route("/api/setup/fetch-tables", methods=["POST"])
def api_setup_fetch_tables():
    from flask import session
    body = request.get_json(force=True) or {}
    database_id = body.get("database_id")
    if not database_id:
        return jsonify(error="database_id is required."), 400
    url = session.get("setup_url", "")
    email = session.get("setup_email", "")
    password = session.get("setup_password", "")
    if not url:
        return jsonify(error="No connection context. Please restart setup."), 400
    try:
        client = BaserowClient(url, email, password)
        tables = client.get_tables(int(database_id))
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(tables=[{"id": t["id"], "name": t["name"]} for t in tables])


@app.route("/api/setup/save", methods=["POST"])
def api_setup_save():
    body = request.get_json(force=True) or {}
    required = ["url", "email", "password", "database_id",
                "contacts", "units", "positions", "assignments", "history"]
    for key in required:
        if not body.get(key):
            return jsonify(error=f"Missing required field: {key}"), 400

    url = body["url"].rstrip("/")
    email = body["email"]
    password = body["password"]
    database_id = int(body["database_id"])

    try:
        client = BaserowClient(url, email, password)
    except BaserowAuthError as e:
        return jsonify(error=str(e)), 401
    except Exception as e:
        return jsonify(error=f"Connection error: {e}"), 400

    history_table_id = body["history"]
    warnings = []

    # Create Import History table if requested
    if history_table_id == "__create__":
        try:
            history_table_id = _create_history_table(client, database_id)
        except Exception as e:
            return jsonify(error=f"Failed to create Import History table: {e}"), 500

    # Validate table schemas
    EXPECTED_FIELDS = {
        "contacts": {"Email", "First Name", "Last Name", "Mobile",
                     "Street", "City", "Zip", "Unsubscribed", "Last Update", "Source"},
        "units":    {"Unit Name"},
        "positions": {"Position Name"},
        "assignments": {"Contact", "Unit", "Position"},
        "history":  {"Filename", "Import Date"},
    }
    role_to_table_id = {
        "contacts": body["contacts"],
        "units": body["units"],
        "positions": body["positions"],
        "assignments": body["assignments"],
        "history": history_table_id,
    }
    for role, tid in role_to_table_id.items():
        if role not in EXPECTED_FIELDS:
            continue
        try:
            fields = client.get_fields(int(tid))
            field_names = {f["name"] for f in fields}
            missing = EXPECTED_FIELDS[role] - field_names
            if missing:
                warnings.append(
                    f"{role.capitalize()} table is missing fields: {', '.join(sorted(missing))}"
                )
        except Exception as e:
            warnings.append(f"Could not validate {role} table: {e}")

    # Save config
    config_data = {
        "baserow_url": url,
        "baserow_email": email,
        "baserow_password": password,
        "database_id": database_id,
        "table_contacts": int(body["contacts"]),
        "table_units": int(body["units"]),
        "table_positions": int(body["positions"]),
        "table_assignments": int(body["assignments"]),
        "table_history": int(history_table_id),
    }
    cfg.save_config(config_data)
    return jsonify(ok=True, warnings=warnings)


def _create_history_table(client: BaserowClient, database_id: int) -> int:
    table = client.create_table(database_id, "Import History")
    table_id = table["id"]

    # Rename the auto-created primary field to "Import ID"
    fields = client.get_fields(table_id)
    if fields:
        primary_field = next((f for f in fields if f.get("primary")), fields[0])
        client.update_field(primary_field["id"], {"name": "Import ID"})

    field_specs = [
        {"name": "Filename",           "type": "text"},
        {"name": "Import Date",        "type": "date",
         "date_format": "ISO", "date_include_time": True},
        {"name": "Source Format",      "type": "text"},
        {"name": "Match Key Used",     "type": "text"},
        {"name": "New Contacts",       "type": "number", "number_decimal_places": 0},
        {"name": "Updated Contacts",   "type": "number", "number_decimal_places": 0},
        {"name": "New Assignments",    "type": "number", "number_decimal_places": 0},
        {"name": "Updated Assignments","type": "number", "number_decimal_places": 0},
        {"name": "Conflicts Reviewed", "type": "number", "number_decimal_places": 0},
        {"name": "Skipped",            "type": "number", "number_decimal_places": 0},
        {"name": "Errors",             "type": "number", "number_decimal_places": 0},
        {"name": "Error Details",      "type": "long_text"},
        {"name": "Status",             "type": "text"},
    ]
    for spec in field_specs:
        client.create_field(table_id, spec)

    return table_id


# ------------------------------------------------------------------ #
# Settings page
# ------------------------------------------------------------------ #

@app.route("/settings")
def settings_page():
    c = cfg.load_config()
    # Build display names for each mapped table
    table_names = {}
    if cfg.is_configured():
        try:
            client = _make_client()
            tables = client.get_tables(c["database_id"])
            id_to_name = {t["id"]: t["name"] for t in tables}
            for role_key in ("table_contacts", "table_units", "table_positions",
                             "table_assignments", "table_history"):
                tid = c.get(role_key)
                table_names[role_key] = id_to_name.get(tid, str(tid)) if tid else "—"
        except Exception:
            pass
    return render_template("settings.html", cfg=c, table_names=table_names)


@app.route("/api/settings/test", methods=["POST"])
def api_settings_test():
    if not cfg.is_configured():
        return jsonify(ok=False, error="Not configured.")
    t0 = time.monotonic()
    try:
        client = _make_client()
        client.get_applications()
        latency = round((time.monotonic() - t0) * 1000)
        return jsonify(ok=True, latency_ms=latency)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/api/settings/refresh-tables", methods=["POST"])
def api_settings_refresh_tables():
    if not cfg.is_configured():
        return jsonify(ok=False, error="Not configured.")
    try:
        client = _make_client()
        c = cfg.load_config()
        client.get_tables(c["database_id"])  # just verify it works
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ------------------------------------------------------------------ #
# Error handlers
# ------------------------------------------------------------------ #

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return render_template("base.html"), 404


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.exception("Unhandled exception")
    if request.path.startswith("/api/"):
        return jsonify(error=str(e)), 500
    flash(f"An unexpected error occurred: {e}", "danger")
    return redirect(url_for("index")), 302


if __name__ == "__main__":
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=5000, debug=debug)
