import json
import os

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/data/config.json")

REQUIRED_KEYS = [
    "baserow_url",
    "baserow_email",
    "baserow_password",
    "database_id",
    "table_contacts",
    "table_units",
    "table_positions",
    "table_assignments",
    "table_history",
]


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def get(key: str, default=None):
    return load_config().get(key, default)


def is_configured() -> bool:
    cfg = load_config()
    return all(cfg.get(k) for k in REQUIRED_KEYS)
