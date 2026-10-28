# NearBarber

NearBarber is a Flask foundation for a neighborhood barber discovery, appointment, and queue application. Customers can register, browse listed shops, join a queue, and track their tokens. Owners can manage shops, services, and their active queues. Appointment booking is intentionally deferred.

## Stack

Python 3.12+ (verified here with Python 3.14), Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF, Werkzeug, SQLite, Bootstrap 5, vanilla JavaScript, and pytest. SQLAlchemy's SQL Server dialect is supported for a future Azure SQL configuration; a compatible ODBC driver and `pyodbc` must be installed when that database is introduced.

## Structure

- `app/__init__.py`: application factory, extensions, blueprints, error pages, and `init-db` CLI command.
- `app/models/`: eight related entities and their constraints/relationships.
- `app/routes/`: public discovery, authentication, customer, owner, and admin endpoints.
- `app/templates/`, `app/static/`: responsive pages, theme, and small JS entry point.
- `tests/`: isolated SQLite route and model tests.

## Windows PowerShell setup

Install Python 3.12 or newer. From the repository root:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `SECRET_KEY` to a long random value before using the app beyond local development. The example database URL uses SQLite. Do not commit `.env`.

Initialize a fresh local schema and run:

```powershell
flask --app run.py init-db
python run.py
```

Open the local address printed by Flask. Run the tests with:

```powershell
pytest -q
```

`init-db` uses `db.create_all()`. It is reproducible for a fresh database and safe to rerun, but it does not migrate existing schemas. The focused upgrades below handle the Task 1 to Task 2 and Task 3 schema changes; a versioned Alembic migration history is recommended before future schema revisions.

### Existing database upgrade

Task 2 adds shop description and opening hours plus a service availability flag and directory indexes. For an existing Task 1 SQLite or SQL Server database, run this additive, repeatable upgrade before starting the app:

```powershell
python -m flask --app run.py upgrade-shop-schema
```

It adds only missing columns/indexes and does not drop tables or rows. Take a database backup first as normal operational practice. The command currently supports SQLite and SQL Server dialects; other dialects are rejected. New databases created with `init-db` already have the current schema.

Queue token schema changes are applied separately to existing databases:

```powershell
python -m flask --app run.py upgrade-queue-schema
```

This preserves old token rows, backfills their queue date from their creation timestamp, and replaces the old shop-wide token uniqueness constraint with shop/date/token uniqueness. Existing duplicate active tokens for one customer and shop must be resolved before the active-entry index can be created; the command stops with an error rather than deleting records. Run it once before enabling queue routes against an older database. When a customer next joins on a new business date, any still-active tokens from earlier dates for that shop are marked cancelled and logged so they do not block the new day's queue.

## Configuration and SQL Server

`SECRET_KEY`, `DATABASE_URL`, and `FLASK_DEBUG` are read from environment variables (the `run.py` entry point loads `.env`). Local defaults use SQLite and a development-only key; set a real random key in `.env`. For Azure SQL, use a SQLAlchemy URL such as:

```text
mssql+pyodbc://USER:PASSWORD@SERVER/DATABASE?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes
```

URL-encode special characters and keep credentials in environment configuration. Cloud configuration and connectivity have not been exercised here.

## Implemented now

- App factory, session and CSRF extensions, blueprints, friendly 404/500 pages, and repeatable schema creation.
- Customer and owner registration/login, password hashing, unique email constraint, generic login failures, logout, role guards, and safe same-host post-login redirects.
- Owner-only shop creation, editing, public availability control, and service add/edit/deactivation with server-side validation and per-resource ownership checks.
- Public directory search by case-insensitive partial locality match; only open shops and active services are visible publicly.
- Customer queue token creation, customer-owned token history/cancellation, and owner queue controls with status-transition checks and activity logs.
- Home page, shop directory/detail pages, customer and owner dashboards, and responsive barber-themed presentation.
- SQLAlchemy models for users, shops, services, appointments, queue counters/tokens, reviews, and activity logs.

## Deferred

Search/geolocation, appointment creation and cancellation, live queue updates, review eligibility/moderation, admin workflows, general-purpose migration history, and Azure deployment/integrations.

## Queue workflow

Only customer accounts can join an available shop's queue, using one of that shop's active services. Each customer may have one active token per shop (`waiting` or `in_service`); a filtered unique database index enforces this if requests race. Token numbers restart at 1 for each shop's UTC calendar date and are constrained unique by `(shop_id, queue_date, token_number)`. UTC is used because shops do not yet store a timezone.

Statuses use the existing vocabulary: `waiting`, `in_service`, `completed`, `cancelled`, and legacy `skipped`. Implemented transitions are `waiting → in_service`, `waiting → cancelled`, and `in_service → completed`. Owners can start only the next waiting token, and only one token can be in service at a time. Customers can cancel only their own waiting token.

The displayed wait is the sum of service durations for active tokens ahead. The in-service customer is included at their full service duration, giving a conservative estimate; the current in-service customer's own wait is zero. Completed and cancelled tokens do not count. Estimates can change and are not appointment times or guarantees. Token and activity-log writes share a transaction.

SQLite allocation begins with `BEGIN IMMEDIATE`, which serializes writers at database scope. On SQL Server, the service uses `UPDLOCK`, `HOLDLOCK`, and a unique per-shop counter row, with unique token and active-entry indexes as final guards. Azure SQL concurrency has not been verified; test simultaneous requests against the configured SQL Server driver before production use.

## Security and demo limits

All forms use Flask-WTF CSRF tokens in normal operation; tests disable CSRF only for route-post convenience and separately assert that CSRF is enabled by default. Use HTTPS and set `FLASK_DEBUG=0` in deployment. The development fallback key must be replaced. Admin registration is deliberately not public, and no initial admin credentials are provided. The UI depends on Bootstrap and fonts from CDNs. This is a student project foundation, not production-ready software.
