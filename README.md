# HoD Contact Manager

A Dockerized web application for importing Scout volunteer contact data from Excel/CSV files into a Baserow database. Handles deduplication, conflict detection, and relationship linking across a normalized schema.

## Prerequisites

- Docker and Docker Compose
- A running [Baserow](https://baserow.io/) instance (self-hosted)
- A Baserow database with the following tables (field names must match exactly):

### Required Baserow Tables

**Contacts** (primary field: Email)
- Email, First Name, Last Name, Mobile, Street, City, Zip, Unsubscribed, Last Update, Source
- Any additional file fields you add (e.g. photo) will appear automatically in the Manual Entry form.

**Units** (primary field: Unit Name)
- Unit Name (and any other fields you want to keep)

**Positions** (primary field: Position Name)
- Position Name (and any other fields you want to keep — file fields like an insignia image will appear in the Validate editor)

**Contact Assignments** (link table)
- Contact (link_row → Contacts), Unit (link_row → Units), Position (link_row → Positions)
- Source, Direct Contact Leader (boolean), Trained (boolean), Registration Expiration, Last Update

**Import History** — can be created automatically by the setup wizard.

## Quick Start

```bash
git clone <repo-url> hod-contacts
cd hod-contacts

# Optional: pre-set Baserow credentials via environment
export BASEROW_URL=http://yourserver:8081
export BASEROW_EMAIL=admin@example.com
export BASEROW_PASSWORD=yourpassword

docker compose up --build -d
```

Then open **http://localhost:5500** (or `http://yourserver:5500` on your network).

The app will redirect to the setup wizard on first run.

## Configuration

The port defaults to **5500**. To change it:

```bash
PORT=8090 docker compose up -d
```

Or edit `docker-compose.yml` directly.

### Persistent Data

- `./data/config.json` — saved connection settings and table IDs (survives restarts)
- `./uploads/` — temporary files during import processing (auto-cleaned after 24 h on startup)

Back up `./data/config.json` to preserve your configuration.

## First-Run Setup Wizard

1. Navigate to `http://localhost:5500`
2. Enter your Baserow URL, email, and password
3. Select the database from the dropdown
4. Map each role to the corresponding Baserow table
5. For Import History, select an existing table or choose "Create new table"
6. Click "Validate & Save" — the app verifies the tables and writes `config.json`

## Import Workflow

1. Click **Import** in the navbar
2. Upload an Excel (`.xlsx`) or CSV file
3. Column mapping is auto-detected for C10 exports; adjust as needed
4. Select the match key (Email recommended)
5. Click **Analyze** — the app compares every row against existing Baserow data in parallel
6. Review the diff summary:
   - **New** — contacts that don't exist yet (auto-applied)
   - **Clean Update** — existing contacts with newer data (auto-applied)
   - **New Position** — existing contacts with a new unit/position combo (review required)
   - **Stale** — incoming data is older than what's in Baserow (skipped by default)
   - **No Change** — identical to existing data (skipped)
7. Approve or deselect rows, resolve conflicts, click **Apply Changes**

## Manual Entry

The **Manual Entry** page lets you add or edit a single contact:

- Search by email or name to find and pre-fill an existing contact (searches run in parallel)
- Edit any field and save
- Add position assignments from Unit/Position dropdowns
- If the Contacts table has any **file fields** (e.g. a photo column), file upload widgets appear automatically

## Validate

The **Validate** page provides a data quality report for Positions and Units:

- See every position/unit with its assignment count
- Filter to show only unused entries (zero assignments)
- Click any row to open an inline editor — edit all fields including file uploads
- Export positions, units, or both to CSV

## History

The **History** page shows all past imports with counts of changes and any errors. Click a row to see full details.

## Dynamic Field Support

The app dynamically reads the schema of each Baserow table at runtime. Any **file field** you add to Contacts, Units, or Positions will appear as an upload widget in the relevant editor without requiring a code change.

## Development

To run locally without Docker (requires Python 3.11+):

```bash
pip install -r requirements.txt
CONFIG_PATH=./data/config.json UPLOAD_FOLDER=./uploads FLASK_ENV=development python app/main.py
```
