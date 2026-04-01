"""
import_engine.py — file parsing, column mapping, diff logic, and apply logic.

Structured in four sections:
  1. File parsing & C10 detection
  2. Diff engine (classify rows against existing Baserow data)
  3. Apply logic (write approved changes to Baserow)
  4. Helpers shared across sections
"""

import json
import os
from datetime import datetime
from typing import Optional

from models import Assignment, Contact, DiffResult, ImportRow

# ============================================================ #
# Target field names (canonical names used throughout the app)  #
# ============================================================ #

TARGET_FIELDS = [
    "Email",
    "First Name",
    "Last Name",
    "Mobile",
    "Street",
    "City",
    "Zip",
    "Unit",
    "Position",
    "Source",
    "Direct Contact Leader",
    "Trained",
    "Registration Expiration",
    "Last Update",
    "Unsubscribed",
]

# C10 export column name → target field (case-insensitive match on source side)
_C10_MAP = {
    "email": "Email",
    "email address": "Email",
    "first": "First Name",
    "first name": "First Name",
    "first_name": "First Name",
    "last": "Last Name",
    "last name": "Last Name",
    "last_name": "Last Name",
    "cell": "Mobile",
    "mobile": "Mobile",
    "phone": "Mobile",
    "address": "Street",
    "street": "Street",
    "city": "City",
    "zip": "Zip",
    "zip code": "Zip",
    "postal": "Zip",
    "unit": "Unit",
    "position": "Position",
    "source": "Source",
    "direct_contact_leader": "Direct Contact Leader",
    "direct contact leader": "Direct Contact Leader",
    "trained": "Trained",
    "registration_expiration_date": "Registration Expiration",
    "registration expiration": "Registration Expiration",
    "registration expiration date": "Registration Expiration",
    "last update": "Last Update",
    "last_update": "Last Update",
    "unsub": "Unsubscribed",
    "unsubscribed": "Unsubscribed",
    "chartered_org_name": "Source",  # fallback mapping for C10 org field
    "chartered org name": "Source",
}


# ============================================================ #
# Section 1 — File parsing                                      #
# ============================================================ #

def parse_file(filepath: str) -> tuple[list[str], list[dict]]:
    """
    Read an Excel or CSV file and return (headers, rows).
    All cell values are returned as normalized strings.
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".xlsx", ".xls"):
        return _parse_excel(filepath)
    elif ext == ".csv":
        return _parse_csv(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _parse_excel(filepath: str) -> tuple[list[str], list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        return [], []
    headers = [_normalize_cell(h) for h in header_row]
    rows = []
    for raw_row in rows_iter:
        if all(c is None or str(c).strip() == "" for c in raw_row):
            continue  # skip blank rows
        row = {}
        for h, cell in zip(headers, raw_row):
            row[h] = _normalize_cell(cell)
        rows.append(row)
    return headers, rows


def _parse_csv(filepath: str) -> tuple[list[str], list[dict]]:
    import pandas as pd
    df = pd.read_csv(filepath, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    headers = list(df.columns)
    rows = []
    for _, row in df.iterrows():
        rows.append({h: str(v).strip() for h, v in row.items()})
    return headers, rows


def _normalize_cell(value) -> str:
    """Convert any cell value to a clean string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, float):
        # Avoid "1.0" for integer-like floats
        if value == int(value):
            return str(int(value))
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def detect_c10_mapping(headers: list[str]) -> dict[str, str]:
    """
    Return {source_header: target_field} for headers matching C10 column names.
    Matching is case-insensitive.  Only returns headers that have a known mapping.
    """
    mapping = {}
    for h in headers:
        key = h.lower().strip()
        if key in _C10_MAP:
            mapping[h] = _C10_MAP[key]
    return mapping


# ============================================================ #
# Section 2 — Diff engine                                       #
# ============================================================ #

def normalize_email(email: str) -> str:
    return email.strip().lower()


def parse_date(value) -> Optional[datetime]:
    """Try to parse value as a date. Returns None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m-%d-%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def apply_mapping(raw_row: dict, mapping: dict) -> dict:
    """
    Transform {source_col: value} → {target_field: value} using the mapping dict.
    Unmapped source cols are dropped. Missing target fields get "".
    """
    mapped = {target: "" for target in TARGET_FIELDS}
    for source_col, target_field in mapping.items():
        if target_field in mapped and source_col in raw_row:
            mapped[target_field] = raw_row[source_col]
    return mapped


def fetch_all_contacts(client, table_id: int) -> dict[str, Contact]:
    """Returns {normalized_email: Contact} for every row in the Contacts table."""
    rows = client.get_all_rows(table_id)
    contacts = {}
    for row in rows:
        email = normalize_email(row.get("Email", ""))
        if not email:
            continue
        contacts[email] = Contact(
            email=email,
            first_name=row.get("First Name", ""),
            last_name=row.get("Last Name", ""),
            mobile=row.get("Mobile", ""),
            street=row.get("Street", ""),
            city=row.get("City", ""),
            zip_code=row.get("Zip", ""),
            unsubscribed=bool(row.get("Unsubscribed", False)),
            last_update=row.get("Last Update", "") or "",
            source=row.get("Source", ""),
            baserow_row_id=row.get("id"),
        )
    return contacts


def fetch_all_assignments(client, table_id: int) -> dict[str, list[Assignment]]:
    """Returns {normalized_email: [Assignment,...]} from the Assignments table."""
    rows = client.get_all_rows(table_id)
    by_email: dict[str, list[Assignment]] = {}
    for row in rows:
        # link_row fields come back as [{id, value}, ...] — extract text value
        contact_link = row.get("Contact", [])
        if isinstance(contact_link, list) and contact_link:
            contact_email = normalize_email(contact_link[0].get("value", ""))
        else:
            continue

        unit_link = row.get("Unit", [])
        unit_name = unit_link[0].get("value", "") if (isinstance(unit_link, list) and unit_link) else ""

        pos_link = row.get("Position", [])
        pos_name = pos_link[0].get("value", "") if (isinstance(pos_link, list) and pos_link) else ""

        asn = Assignment(
            contact_email=contact_email,
            unit_name=unit_name,
            position_name=pos_name,
            source=row.get("Source", ""),
            direct_contact_leader=bool(row.get("Direct Contact Leader", False)),
            trained=bool(row.get("Trained", False)),
            registration_expiration=row.get("Registration Expiration", "") or "",
            last_update=row.get("Last Update", "") or "",
            baserow_row_id=row.get("id"),
        )
        by_email.setdefault(contact_email, []).append(asn)
    return by_email


def _contact_field_changes(existing: Contact, mapped: dict) -> dict:
    """
    Compare existing Contact fields against incoming mapped values.
    Returns {field_name: (old_value, new_value)} for fields that differ.
    """
    field_map = {
        "First Name": ("first_name", mapped.get("First Name", "")),
        "Last Name":  ("last_name",  mapped.get("Last Name", "")),
        "Mobile":     ("mobile",     mapped.get("Mobile", "")),
        "Street":     ("street",     mapped.get("Street", "")),
        "City":       ("city",       mapped.get("City", "")),
        "Zip":        ("zip_code",   mapped.get("Zip", "")),
        "Source":     ("source",     mapped.get("Source", "")),
    }
    changes = {}
    for field_label, (attr, incoming_val) in field_map.items():
        old_val = getattr(existing, attr, "")
        # Normalize both sides: strip, lowercase for comparison only
        if str(old_val).strip().lower() != str(incoming_val).strip().lower():
            changes[field_label] = (old_val, incoming_val)
    return changes


def classify_row(
    mapped: dict,
    match_key: str,
    existing_contacts: dict,
    existing_assignments: dict,
    custom_match_col: str = "",
) -> DiffResult:
    """Classify a single mapped row against existing Baserow data."""

    # Resolve match key → email / lookup key
    if match_key == "Email":
        key = normalize_email(mapped.get("Email", ""))
    elif match_key == "First+Last":
        first = mapped.get("First Name", "").strip().lower()
        last = mapped.get("Last Name", "").strip().lower()
        # Linear scan to find matching contact
        key = ""
        for em, c in existing_contacts.items():
            if c.first_name.strip().lower() == first and c.last_name.strip().lower() == last:
                key = em
                break
        if not key:
            # Treat as new if no name match found
            row = ImportRow(raw=mapped, mapped=mapped)
            return DiffResult(row=row, status="NEW")
    else:
        key = normalize_email(mapped.get(custom_match_col, ""))

    if not key:
        row = ImportRow(raw=mapped, mapped=mapped)
        return DiffResult(row=row, status="NEW")

    if key not in existing_contacts:
        row = ImportRow(raw=mapped, mapped=mapped)
        return DiffResult(row=row, status="NEW")

    existing = existing_contacts[key]
    incoming_unit = mapped.get("Unit", "").strip()
    incoming_pos = mapped.get("Position", "").strip()
    existing_asns = existing_assignments.get(key, [])

    position_exists = any(
        a.unit_name.strip().lower() == incoming_unit.lower()
        and a.position_name.strip().lower() == incoming_pos.lower()
        for a in existing_asns
    )

    incoming_date = parse_date(mapped.get("Last Update", ""))
    existing_date = parse_date(existing.last_update)

    row = ImportRow(raw=mapped, mapped=mapped)

    if not position_exists and (incoming_unit or incoming_pos):
        return DiffResult(
            row=row,
            status="NEW_ADDITIONAL_POSITION",
            existing_contact=existing,
            existing_assignments=existing_asns,
        )

    if incoming_date and existing_date and incoming_date < existing_date:
        return DiffResult(
            row=row,
            status="STALE",
            existing_contact=existing,
            existing_assignments=existing_asns,
        )

    field_changes = _contact_field_changes(existing, mapped)
    if field_changes:
        return DiffResult(
            row=row,
            status="CLEAN_UPDATE",
            existing_contact=existing,
            existing_assignments=existing_asns,
            field_changes=field_changes,
        )

    return DiffResult(
        row=row,
        status="NO_CHANGE",
        existing_contact=existing,
        existing_assignments=existing_asns,
    )


def run_diff(
    rows: list[dict],
    mapping: dict,
    match_key: str,
    config: dict,
    custom_match_col: str = "",
) -> list[DiffResult]:
    """
    Orchestrate the full diff:
    1. Connect to Baserow
    2. Fetch all existing contacts and assignments
    3. Classify every row
    """
    from baserow_client import BaserowClient
    client = BaserowClient(
        config["baserow_url"], config["baserow_email"], config["baserow_password"]
    )
    existing_contacts = fetch_all_contacts(client, int(config["table_contacts"]))
    existing_assignments = fetch_all_assignments(client, int(config["table_assignments"]))

    results = []
    for raw_row in rows:
        mapped = apply_mapping(raw_row, mapping)
        result = classify_row(
            mapped, match_key, existing_contacts, existing_assignments, custom_match_col
        )
        results.append(result)
    return results


# ============================================================ #
# Section 3 — Apply logic                                       #
# ============================================================ #

def apply_changes(
    approved: list[DiffResult],
    conflict_decisions: dict,
    config: dict,
    client,
) -> dict:
    """
    Write all approved changes to Baserow.
    Returns {created, updated, new_positions, skipped, errors: [str]}.
    """
    errors = []
    counts = {"created": 0, "updated": 0, "new_positions": 0, "skipped": 0}

    table_contacts = int(config["table_contacts"])
    table_units = int(config["table_units"])
    table_positions = int(config["table_positions"])
    table_assignments = int(config["table_assignments"])

    # Pass 1 — ensure all referenced Units and Positions exist
    existing_units = {
        r.get("Unit Name", "").strip(): r.get("id")
        for r in client.get_all_rows(table_units)
    }
    existing_positions = {
        r.get("Position Name", "").strip(): r.get("id")
        for r in client.get_all_rows(table_positions)
    }

    needed_units = {d.row.mapped.get("Unit", "").strip() for d in approved}
    needed_positions = {d.row.mapped.get("Position", "").strip() for d in approved}

    for unit_name in needed_units:
        if unit_name and unit_name not in existing_units:
            try:
                row = client.create_row(table_units, {"Unit Name": unit_name})
                existing_units[unit_name] = row["id"]
            except Exception as e:
                errors.append(f"Could not create unit '{unit_name}': {e}")

    for pos_name in needed_positions:
        if pos_name and pos_name not in existing_positions:
            try:
                row = client.create_row(table_positions, {"Position Name": pos_name})
                existing_positions[pos_name] = row["id"]
            except Exception as e:
                errors.append(f"Could not create position '{pos_name}': {e}")

    # Pass 2 — batch-create NEW contacts
    new_diffs = [d for d in approved if d.status == "NEW"]
    if new_diffs:
        contact_items = [_build_contact_payload(d.row.mapped) for d in new_diffs]
        try:
            created_rows = client.batch_create_rows(table_contacts, contact_items)
            email_to_id = {}
            for i, row in enumerate(created_rows):
                email = normalize_email(new_diffs[i].row.mapped.get("Email", ""))
                email_to_id[email] = row["id"]
            counts["created"] += len(created_rows)
        except Exception as e:
            errors.append(f"Batch create contacts failed: {e}")
            email_to_id = {}

        # Create assignments for new contacts
        for diff in new_diffs:
            email = normalize_email(diff.row.mapped.get("Email", ""))
            try:
                _create_assignment(client, table_assignments, diff.row.mapped, email)
                counts["new_positions"] += 1
            except Exception as e:
                errors.append(f"Assignment for {email}: {e}")

    # Pass 3 — PATCH CLEAN_UPDATE contacts
    for diff in approved:
        if diff.status != "CLEAN_UPDATE":
            continue
        if not diff.existing_contact or not diff.existing_contact.baserow_row_id:
            counts["skipped"] += 1
            continue
        patch = {}
        field_to_attr = {
            "First Name": "first_name", "Last Name": "last_name",
            "Mobile": "mobile", "Street": "street",
            "City": "city", "Zip": "zip_code", "Source": "source",
        }
        for field_label in diff.field_changes:
            if field_label in field_to_attr:
                patch[field_label] = diff.row.mapped.get(field_label, "")
        if mapped_lu := diff.row.mapped.get("Last Update"):
            patch["Last Update"] = mapped_lu
        try:
            client.update_row(table_contacts, diff.existing_contact.baserow_row_id, patch)
            counts["updated"] += 1
        except Exception as e:
            errors.append(f"Update contact {diff.existing_contact.email}: {e}")

    # Pass 4 — create assignments for NEW_ADDITIONAL_POSITION
    for i, diff in enumerate(approved):
        if diff.status != "NEW_ADDITIONAL_POSITION":
            continue
        decision = conflict_decisions.get(str(i), "add")
        if decision == "skip":
            counts["skipped"] += 1
            continue
        email = normalize_email(diff.row.mapped.get("Email", ""))
        try:
            if decision.startswith("replace:"):
                old_id = int(decision.split(":")[1])
                asn_patch = _build_assignment_payload(diff.row.mapped, email)
                client.update_row(table_assignments, old_id, asn_patch)
                counts["new_positions"] += 1
            else:
                _create_assignment(client, table_assignments, diff.row.mapped, email)
                counts["new_positions"] += 1
        except Exception as e:
            errors.append(f"Assignment (new position) for {email}: {e}")

    # Pass 5 — force-apply STALE if user chose to
    for i, diff in enumerate(approved):
        if diff.status != "STALE":
            continue
        decision = conflict_decisions.get(str(i), "skip")
        if decision != "force":
            counts["skipped"] += 1
            continue
        if not diff.existing_contact or not diff.existing_contact.baserow_row_id:
            counts["skipped"] += 1
            continue
        patch = _build_contact_payload(diff.row.mapped)
        try:
            client.update_row(table_contacts, diff.existing_contact.baserow_row_id, patch)
            counts["updated"] += 1
        except Exception as e:
            errors.append(f"Force-update stale {diff.existing_contact.email}: {e}")

    counts["errors"] = errors
    return counts


def _build_contact_payload(mapped: dict) -> dict:
    payload = {}
    simple_fields = ["Email", "First Name", "Last Name", "Mobile",
                     "Street", "City", "Zip", "Source", "Last Update"]
    for f in simple_fields:
        if mapped.get(f):
            payload[f] = mapped[f]
    if mapped.get("Unsubscribed", "").lower() in ("true", "1", "yes"):
        payload["Unsubscribed"] = True
    return payload


def _build_assignment_payload(mapped: dict, contact_email: str) -> dict:
    payload = {
        "Contact": [contact_email],
    }
    if mapped.get("Unit"):
        payload["Unit"] = [mapped["Unit"]]
    if mapped.get("Position"):
        payload["Position"] = [mapped["Position"]]
    for f in ("Source", "Registration Expiration", "Last Update"):
        if mapped.get(f):
            payload[f] = mapped[f]
    dcl = mapped.get("Direct Contact Leader", "").lower()
    if dcl:
        payload["Direct Contact Leader"] = dcl in ("true", "1", "yes")
    trained = mapped.get("Trained", "").lower()
    if trained:
        payload["Trained"] = trained in ("true", "1", "yes")
    return payload


def _create_assignment(client, table_id: int, mapped: dict, contact_email: str) -> dict:
    payload = _build_assignment_payload(mapped, contact_email)
    return client.create_row(table_id, payload)
