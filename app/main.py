import json
import os
import time
import uuid

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

import config as cfg
from baserow_client import BaserowAuthError, BaserowAPIError, BaserowClient

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/app/uploads")
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


def _sidecar_path(upload_id: str) -> str:
    return os.path.join(UPLOAD_FOLDER, f"{upload_id}.meta.json")


def _load_sidecar(upload_id: str) -> dict:
    path = _sidecar_path(upload_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_sidecar(upload_id: str, data: dict) -> None:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    path = _sidecar_path(upload_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)

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


@app.route("/api/import/upload", methods=["POST"])
def api_import_upload():
    from import_engine import parse_file, detect_c10_mapping
    if "file" not in request.files:
        return jsonify(error="No file provided."), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify(error="Empty filename."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(error=f"Unsupported file type '{ext}'. Use .xlsx or .csv."), 400

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    uid = str(uuid.uuid4())
    safe_name = secure_filename(f.filename)
    filepath = os.path.join(UPLOAD_FOLDER, f"{uid}_{safe_name}")
    f.save(filepath)

    try:
        headers, rows = parse_file(filepath)
    except Exception as e:
        os.unlink(filepath)
        return jsonify(error=f"Could not read file: {e}"), 400

    suggested = detect_c10_mapping(headers)
    _save_sidecar(uid, {
        "upload_id": uid,
        "filename": f.filename,
        "filepath": filepath,
        "headers": headers,
        "rows": rows,
    })
    return jsonify(
        upload_id=uid,
        headers=headers,
        row_count=len(rows),
        suggested_mapping=suggested,
    )


@app.route("/api/import/analyze", methods=["POST"])
def api_import_analyze():
    from import_engine import run_diff
    body = request.get_json(force=True) or {}
    uid = body.get("upload_id")
    if not uid:
        return jsonify(error="upload_id required."), 400
    sidecar = _load_sidecar(uid)
    if not sidecar:
        return jsonify(error="Upload not found. Please re-upload the file."), 404

    mapping = body.get("mapping", {})
    match_key = body.get("match_key", "Email")
    custom_match_col = body.get("custom_match_col", "")

    try:
        results = run_diff(
            sidecar["rows"], mapping, match_key, cfg.load_config(), custom_match_col
        )
    except Exception as e:
        return jsonify(error=f"Analysis failed: {e}"), 500

    summary = {"NEW": 0, "CLEAN_UPDATE": 0, "NEW_ADDITIONAL_POSITION": 0,
               "STALE": 0, "NO_CHANGE": 0}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1

    # Persist results in sidecar
    sidecar["diff_results"] = [r.to_dict() for r in results]
    sidecar["mapping"] = mapping
    sidecar["match_key"] = match_key
    sidecar["custom_match_col"] = custom_match_col
    _save_sidecar(uid, sidecar)

    return jsonify(summary=summary, redirect=f"/import/review/{uid}")


@app.route("/api/import/apply", methods=["POST"])
def api_import_apply():
    from import_engine import apply_changes
    import history as hist
    body = request.get_json(force=True) or {}
    uid = body.get("upload_id")
    if not uid:
        return jsonify(error="upload_id required."), 400
    sidecar = _load_sidecar(uid)
    if not sidecar:
        return jsonify(error="Upload not found. Please re-upload."), 404
    if sidecar.get("applied"):
        return jsonify(error="This import has already been applied."), 409

    diff_results_raw = sidecar.get("diff_results", [])
    approved_indices = set(body.get("approved_indices", []))
    conflict_decisions = body.get("conflict_decisions", {})

    # Reconstruct minimal DiffResult-like dicts for apply_changes
    from models import DiffResult, ImportRow, Contact, Assignment
    approved = []
    for i, r in enumerate(diff_results_raw):
        if i not in approved_indices:
            continue
        # Rebuild lightweight DiffResult from stored dict
        ec = None
        if r.get("existing_contact"):
            ec_d = r["existing_contact"]
            ec = Contact(
                email=ec_d.get("email", ""),
                first_name=ec_d.get("first_name", ""),
                last_name=ec_d.get("last_name", ""),
                mobile=ec_d.get("mobile", ""),
                street=ec_d.get("street", ""),
                city=ec_d.get("city", ""),
                zip_code=ec_d.get("zip_code", ""),
                last_update=ec_d.get("last_update", ""),
                source=ec_d.get("source", ""),
                baserow_row_id=ec_d.get("baserow_row_id"),
            )
        existing_asns = [
            Assignment(
                contact_email=a.get("contact_email", ""),
                unit_name=a.get("unit_name", ""),
                position_name=a.get("position_name", ""),
                baserow_row_id=a.get("baserow_row_id"),
            )
            for a in r.get("existing_assignments", [])
        ]
        dr = DiffResult(
            row=ImportRow(raw=r.get("mapped", {}), mapped=r.get("mapped", {})),
            status=r["status"],
            existing_contact=ec,
            existing_assignments=existing_asns,
            field_changes=r.get("field_changes", {}),
        )
        approved.append(dr)

    c = cfg.load_config()
    try:
        client = _make_client()
        results = apply_changes(approved, conflict_decisions, c, client)
    except Exception as e:
        return jsonify(error=f"Apply failed: {e}"), 500

    results["conflicts_reviewed"] = len([
        i for i in approved_indices
        if diff_results_raw[i]["status"] in ("NEW_ADDITIONAL_POSITION", "STALE")
    ])

    # Log to Import History
    try:
        hist.log_import(
            client,
            int(c["table_history"]),
            sidecar.get("filename", ""),
            results,
            match_key=sidecar.get("match_key", ""),
        )
    except Exception as e:
        results.setdefault("errors", []).append(f"History log failed: {e}")

    # Save results and mark as applied
    sidecar["applied"] = True
    sidecar["results"] = {
        "created": results.get("created", 0),
        "updated": results.get("updated", 0),
        "new_positions": results.get("new_positions", 0),
        "skipped": results.get("skipped", 0),
        "errors": [str(e) for e in results.get("errors", [])],
    }
    _save_sidecar(uid, sidecar)

    return jsonify(results=sidecar["results"], redirect=f"/import/results/{uid}")


@app.route("/import/results/<upload_id>")
def import_results(upload_id):
    sidecar = _load_sidecar(upload_id)
    if not sidecar or "results" not in sidecar:
        flash("Results not found.", "warning")
        return redirect(url_for("import_page"))
    return render_template(
        "results.html",
        filename=sidecar.get("filename", ""),
        results=sidecar["results"],
    )


@app.route("/import/review/<upload_id>")
def import_review(upload_id):
    sidecar = _load_sidecar(upload_id)
    if not sidecar or "diff_results" not in sidecar:
        flash("Import session not found. Please re-upload.", "warning")
        return redirect(url_for("import_page"))
    results = sidecar["diff_results"]
    summary = {"NEW": 0, "CLEAN_UPDATE": 0, "NEW_ADDITIONAL_POSITION": 0,
               "STALE": 0, "NO_CHANGE": 0}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    # Build indexed lists for Jinja (needed for form field indexing)
    auto_rows_indexed = [
        (i, r) for i, r in enumerate(results)
        if r["status"] in ("NEW", "CLEAN_UPDATE", "NEW_ADDITIONAL_POSITION")
    ]
    stale_rows_indexed = [
        (i, r) for i, r in enumerate(results)
        if r["status"] == "STALE"
    ]
    return render_template(
        "review.html",
        upload_id=upload_id,
        filename=sidecar.get("filename", ""),
        diff_results=results,
        summary=summary,
        auto_rows_indexed=auto_rows_indexed,
        stale_rows_indexed=stale_rows_indexed,
    )


@app.route("/manual")
def manual_page():
    try:
        client = _make_client()
        c = cfg.load_config()
        unit_rows = client.get_all_rows(int(c["table_units"]))
        pos_rows = client.get_all_rows(int(c["table_positions"]))
        units_list = sorted([r.get("Unit Name", "") for r in unit_rows if r.get("Unit Name")])
        positions_list = sorted([r.get("Position Name", "") for r in pos_rows if r.get("Position Name")])
    except Exception:
        units_list, positions_list = [], []
    return render_template("manual_entry.html", units_list=units_list, positions_list=positions_list)


@app.route("/api/manual/search")
def api_manual_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(contacts=[])
    c = cfg.load_config()
    try:
        client = _make_client()
        # Search by email contains
        resp_email = client.get_rows(
            int(c["table_contacts"]),
            params={"filter__Email__contains": q, "size": 20},
        )
        # Search by first+last name contains (Baserow doesn't support OR filters
        # via simple params, so we do two searches and merge)
        resp_name = client.get_rows(
            int(c["table_contacts"]),
            params={"search": q, "size": 20},
        )
        seen = set()
        contacts = []
        for row in (resp_email.get("results", []) + resp_name.get("results", [])):
            rid = row.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            # Fetch assignments for this contact
            asn_resp = client.get_rows(
                int(c["table_assignments"]),
                params={
                    "filter__Contact__link_row_has": row.get("Email", ""),
                    "size": 50,
                },
            )
            asns = []
            for a in asn_resp.get("results", []):
                unit_link = a.get("Unit", [])
                pos_link = a.get("Position", [])
                asns.append({
                    "unit_name": unit_link[0]["value"] if (isinstance(unit_link, list) and unit_link) else "",
                    "position_name": pos_link[0]["value"] if (isinstance(pos_link, list) and pos_link) else "",
                    "row_id": a.get("id"),
                })
            contacts.append({
                "row_id": rid,
                "email": row.get("Email", ""),
                "first_name": row.get("First Name", ""),
                "last_name": row.get("Last Name", ""),
                "mobile": row.get("Mobile", ""),
                "street": row.get("Street", ""),
                "city": row.get("City", ""),
                "zip_code": row.get("Zip", ""),
                "source": row.get("Source", ""),
                "last_update": row.get("Last Update", ""),
                "unsubscribed": row.get("Unsubscribed", False),
                "assignments": asns,
                # Full row included so the frontend can populate dynamic fields
                # (e.g. file fields) without knowing the schema in advance
                "_raw_row": {k: v for k, v in row.items() if k != "id"},
            })
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(contacts=contacts[:20])


@app.route("/api/manual/save", methods=["POST"])
def api_manual_save():
    import history as hist
    body = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip()
    if not email:
        return jsonify(error="Email is required."), 400

    c = cfg.load_config()
    try:
        client = _make_client()
    except Exception as e:
        return jsonify(error=str(e)), 500

    contact_payload = {
        "Email": email,
        "First Name": body.get("first_name", ""),
        "Last Name": body.get("last_name", ""),
        "Mobile": body.get("mobile", ""),
        "Street": body.get("street", ""),
        "City": body.get("city", ""),
        "Zip": body.get("zip_code", ""),
        "Source": body.get("source", ""),
    }
    if body.get("last_update"):
        contact_payload["Last Update"] = body["last_update"]
    if body.get("unsubscribed") is not None:
        contact_payload["Unsubscribed"] = bool(body["unsubscribed"])

    row_id = body.get("row_id")
    try:
        if row_id:
            client.update_row(int(c["table_contacts"]), int(row_id), contact_payload)
            new_row_id = int(row_id)
        else:
            created = client.create_row(int(c["table_contacts"]), contact_payload)
            new_row_id = created["id"]
    except Exception as e:
        return jsonify(error=f"Could not save contact: {e}"), 500

    # Handle assignments
    errors = []
    for asn in (body.get("assignments") or []):
        unit = (asn.get("unit") or "").strip()
        position = (asn.get("position") or "").strip()
        if not unit and not position:
            continue
        try:
            client.create_row(int(c["table_assignments"]), {
                "Contact": [email],
                "Unit": [unit] if unit else [],
                "Position": [position] if position else [],
                "Source": "Manual Entry",
            })
        except Exception as e:
            errors.append(str(e))

    # Log to history
    try:
        hist.log_import(
            client, int(c["table_history"]),
            f"Manual: {email}",
            {"created": 0 if row_id else 1, "updated": 1 if row_id else 0,
             "new_positions": len(body.get("assignments", [])), "skipped": 0, "errors": errors},
            match_key="Email", source_format="Manual Entry",
        )
    except Exception:
        pass

    return jsonify(ok=True, row_id=new_row_id, errors=errors)


@app.route("/validate")
def validate_page():
    c = cfg.load_config()
    positions, units = [], []
    try:
        client = _make_client()

        # Fetch positions and units
        pos_rows = client.get_all_rows(int(c["table_positions"]))
        unit_rows = client.get_all_rows(int(c["table_units"]))

        # Fetch all assignments once and tally counts
        asn_rows = client.get_all_rows(int(c["table_assignments"]))
        pos_counts: dict[str, int] = {}
        unit_counts: dict[str, int] = {}
        for a in asn_rows:
            pos_link = a.get("Position", [])
            if isinstance(pos_link, list) and pos_link:
                pname = pos_link[0].get("value", "")
                pos_counts[pname] = pos_counts.get(pname, 0) + 1
            unit_link = a.get("Unit", [])
            if isinstance(unit_link, list) and unit_link:
                uname = unit_link[0].get("value", "")
                unit_counts[uname] = unit_counts.get(uname, 0) + 1

        positions = sorted([
            {
                "name": r.get("Position Name", ""),
                "row_id": r.get("id"),
                "assignment_count": pos_counts.get(r.get("Position Name", ""), 0),
                "raw": {k: v for k, v in r.items() if k != "id"},
            }
            for r in pos_rows if r.get("Position Name")
        ], key=lambda x: x["name"].lower())

        units = sorted([
            {
                "name": r.get("Unit Name", ""),
                "row_id": r.get("id"),
                "assignment_count": unit_counts.get(r.get("Unit Name", ""), 0),
                "raw": {k: v for k, v in r.items() if k != "id"},
            }
            for r in unit_rows if r.get("Unit Name")
        ], key=lambda x: x["name"].lower())

    except Exception as e:
        flash(f"Could not load data: {e}", "danger")

    return render_template("validate.html", positions=positions, units=units)


@app.route("/api/validate/export")
def api_validate_export():
    import csv
    import io
    from flask import Response

    export_type = request.args.get("type", "all")  # positions | units | all
    c = cfg.load_config()
    try:
        client = _make_client()
        pos_rows = client.get_all_rows(int(c["table_positions"]))
        unit_rows = client.get_all_rows(int(c["table_units"]))
        asn_rows = client.get_all_rows(int(c["table_assignments"]))
    except Exception as e:
        return jsonify(error=str(e)), 500

    # Tally counts
    pos_counts: dict[str, int] = {}
    unit_counts: dict[str, int] = {}
    for a in asn_rows:
        pos_link = a.get("Position", [])
        if isinstance(pos_link, list) and pos_link:
            pname = pos_link[0].get("value", "")
            pos_counts[pname] = pos_counts.get(pname, 0) + 1
        unit_link = a.get("Unit", [])
        if isinstance(unit_link, list) and unit_link:
            uname = unit_link[0].get("value", "")
            unit_counts[uname] = unit_counts.get(uname, 0) + 1

    buf = io.StringIO()
    writer = csv.writer(buf)

    if export_type in ("positions", "all"):
        writer.writerow(["Type", "Name", "Assignment Count", "Baserow ID"])
        for r in sorted(pos_rows, key=lambda x: x.get("Position Name", "").lower()):
            name = r.get("Position Name", "")
            writer.writerow(["Position", name, pos_counts.get(name, 0), r.get("id", "")])

    if export_type == "all":
        writer.writerow([])  # blank separator row

    if export_type in ("units", "all"):
        if export_type == "units":
            writer.writerow(["Type", "Name", "Assignment Count", "Baserow ID"])
        for r in sorted(unit_rows, key=lambda x: x.get("Unit Name", "").lower()):
            name = r.get("Unit Name", "")
            writer.writerow(["Unit", name, unit_counts.get(name, 0), r.get("id", "")])

    filename = f"hod_{export_type}_validation.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/history")
def history_page():
    c = cfg.load_config()
    rows = []
    try:
        client = _make_client()
        all_rows = client.get_all_rows(int(c["table_history"]))
        # Sort newest first by Import Date
        rows = sorted(all_rows, key=lambda r: r.get("Import Date") or "", reverse=True)
    except Exception:
        pass
    return render_template("history.html", rows=rows)


# ------------------------------------------------------------------ #
# Generic file upload proxy + dynamic field/row endpoints
# ------------------------------------------------------------------ #

# Field types the app knows how to render as editable inputs.
# Everything else (link_row, formula, rollup, etc.) is skipped.
_EDITABLE_FIELD_TYPES = {
    "text", "long_text", "number", "boolean", "date",
    "url", "email", "phone_number", "file",
    "single_select", "multiple_select", "rating",
}

_TABLE_KEY_MAP = {
    "contacts":    "table_contacts",
    "units":       "table_units",
    "positions":   "table_positions",
    "assignments": "table_assignments",
}


@app.route("/api/files/upload", methods=["POST"])
def api_files_upload():
    """Proxy a file upload to Baserow's user-files endpoint."""
    if "file" not in request.files:
        return jsonify(error="No file provided."), 400
    f = request.files["file"]
    mime = f.mimetype or "application/octet-stream"
    try:
        client = _make_client()
        result = client.upload_file(f.stream, f.filename, mime)
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(result)


@app.route("/api/table-fields/<table_key>")
def api_table_fields(table_key):
    """
    Return editable field definitions for a table.
    Skips primary field, link_row, formula, and other uneditable types.
    """
    cfg_key = _TABLE_KEY_MAP.get(table_key)
    if not cfg_key:
        return jsonify(error=f"Unknown table key: {table_key}"), 400
    c = cfg.load_config()
    table_id = c.get(cfg_key)
    if not table_id:
        return jsonify(error="Table not configured."), 400
    try:
        client = _make_client()
        fields = client.get_fields(int(table_id))
    except Exception as e:
        return jsonify(error=str(e)), 500

    editable = [
        {
            "id":      f["id"],
            "name":    f["name"],
            "type":    f["type"],
            "primary": f.get("primary", False),
            # single_select / multiple_select options
            "options": [
                {"id": o["id"], "value": o["value"], "color": o.get("color", "")}
                for o in f.get("select_options", [])
            ],
        }
        for f in fields
        if f["type"] in _EDITABLE_FIELD_TYPES
    ]
    return jsonify(fields=editable)


@app.route("/api/table-row/<table_key>/<int:row_id>", methods=["PATCH"])
def api_table_row_update(table_key, row_id):
    """Update a single row in any configured table."""
    cfg_key = _TABLE_KEY_MAP.get(table_key)
    if not cfg_key:
        return jsonify(error=f"Unknown table key: {table_key}"), 400
    c = cfg.load_config()
    table_id = c.get(cfg_key)
    if not table_id:
        return jsonify(error="Table not configured."), 400
    body = request.get_json(force=True) or {}
    try:
        client = _make_client()
        updated = client.update_row(int(table_id), row_id, body)
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(updated)


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


def _cleanup_old_uploads(max_age_hours: int = 24) -> None:
    """Delete sidecar and upload files older than max_age_hours."""
    import glob
    cutoff = time.time() - (max_age_hours * 3600)
    for path in glob.glob(os.path.join(UPLOAD_FOLDER, "*.meta.json")):
        if os.path.getmtime(path) < cutoff:
            try:
                data = _load_sidecar(os.path.basename(path).replace(".meta.json", ""))
                if data and data.get("filepath") and os.path.exists(data["filepath"]):
                    os.unlink(data["filepath"])
                os.unlink(path)
            except Exception:
                pass


if __name__ == "__main__":
    _cleanup_old_uploads()
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=5000, debug=debug)
