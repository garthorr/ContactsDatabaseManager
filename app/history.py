from datetime import datetime


def log_import(
    client,
    history_table_id: int,
    filename: str,
    results: dict,
    match_key: str = "",
    source_format: str = "File Import",
) -> None:
    """Record an import event in the Import History table."""
    errors_list = results.get("errors", [])
    error_text = "\n".join(str(e) for e in errors_list) if errors_list else ""
    status = "Success"
    if errors_list:
        total_changes = (results.get("created", 0) + results.get("updated", 0)
                         + results.get("new_positions", 0))
        status = "Partial" if total_changes > 0 else "Failed"

    row = {
        "Filename": filename,
        "Import Date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "Source Format": source_format,
        "Match Key Used": match_key,
        "New Contacts": results.get("created", 0),
        "Updated Contacts": results.get("updated", 0),
        "New Assignments": results.get("new_positions", 0),
        "Updated Assignments": 0,
        "Conflicts Reviewed": results.get("conflicts_reviewed", 0),
        "Skipped": results.get("skipped", 0),
        "Errors": len(errors_list),
        "Error Details": error_text,
        "Status": status,
    }
    client.create_row(history_table_id, row)
