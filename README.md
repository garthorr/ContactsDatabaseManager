# HoD Contact Manager

A Dockerized web application for importing Scout volunteer contact data from Excel/CSV files into a Baserow database. Handles deduplication, conflict detection, and relationship linking across a normalized schema.

## Prerequisites

- Docker and Docker Compose
- A running [Baserow](https://baserow.io/) instance (self-hosted)
- A Baserow database with the following tables (field names must match exactly):

### Required Baserow Tables

**Contacts** (primary field: Email)
- Email, First Name, Last Name, Mobile, Street, City, Zip, Unsubscribed, Last Update, Source

**Units** (primary field: Unit Name)
- Unit Name (and any other unit fields you want to keep)

**Positions** (primary field: Position Name)
- Position Name

**Contact Assignments** (link table)
- Contact (link_row → Contacts), Unit (link_row → Units), Position (link_row → Positions)
- Source, Direct Contact Leader (boolean), Trained (boolean), Registration Expiration, Last Update

**Import History** — can be created automatically by the setup wizard.

## Quick Start

```bash
git clone <repo-url> hod-contacts
cd hod-contacts

# Optional: pre-set Baserow credentials via environment
export BASEROW_URL=http://100.106.75.96:8081
export BASEROW_EMAIL=admin@example.com
export BASEROW_PASSWORD=yourpassword

docker compose up --build -d
```

Then open **http://localhost:5500** (or `http://servOrr:5500` on your network).

The app will redirect to the setup wizard on first run.

## Configuration

The port defaults to **5500**. To change it:

```bash
PORT=8090 docker compose up -d
```

Or edit `docker-compose.yml` directly.

### Persistent Data

- `./data/config.json` — saved connection settings and table IDs (survives restarts)
- `./uploads/` — temporary files during import processing (auto-cleaned after 24h)

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
5. Click **Analyze** — the app compares every row against existing Baserow data
6. Review the diff summary:
   - **New** — contacts that don't exist yet (auto-applied)
   - **Clean Update** — existing contacts with newer data (auto-applied)
   - **New Position** — existing contacts with a new unit/position combo (review required)
   - **Stale** — incoming data is older than what's in Baserow (skipped by default)
   - **No Change** — identical to existing data (skipped)
7. Approve or deselect rows, resolve conflicts, click **Apply Changes**

## Manual Entry

The **Manual Entry** page lets you add or edit a single contact:

- Search by email or name to find and pre-fill an existing contact
- Edit any field and save
- Add position assignments from searchable Unit/Position dropdowns

## History

The **History** page shows all past imports with counts of changes and any errors. Click a row to see full details.

## Development

To run locally without Docker (requires Python 3.11+):

```bash
pip install -r requirements.txt
CONFIG_PATH=./data/config.json UPLOAD_FOLDER=./uploads FLASK_ENV=development python app/main.py
```
