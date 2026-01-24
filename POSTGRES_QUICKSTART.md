# PostgreSQL Quick Start

Get up and running with PostgreSQL locally in 5 minutes.

## Prerequisites

- Docker installed and running
- Python 3.9+ with pip

## Quick Start

### 1. Install dependencies

```bash
pip install alembic psycopg2-binary
```

### 2. Start PostgreSQL

```bash
docker-compose up -d
```

### 3. Set DATABASE_URL

```bash
export DATABASE_URL="postgresql://acitrack:acitrack@localhost:5432/acitrack"
```

### 4. Run migrations

```bash
alembic upgrade head
```

### 5. Test the connection

```bash
python scripts/smoke_test_postgres.py
```

You should see:

```
✅ All tests passed!
```

### 6. Run acitrack

```bash
# Daily run
python run.py --daily --lookback-hours 48

# Tri-model daily
python run_tri_model_daily.py --run-date 2026-01-22
```

## What just happened?

- **Docker Compose** started a local PostgreSQL 15 database
- **Alembic** created 5 tables with indexes:
  - `papers` - publications
  - `runs` - run history
  - `run_papers` - run ↔ paper associations
  - `relevancy_events` - relevancy scoring
  - `tri_model_events` - tri-model scoring
- **Smoke test** verified everything works
- **Runners** automatically use PostgreSQL (detected via DATABASE_URL)

## Migrating existing data

If you have existing SQLite data:

```bash
python scripts/migrate_sqlite_to_postgres.py
```

This copies all data from `data/db/acitrack.db` to PostgreSQL.

## Stopping PostgreSQL

```bash
docker-compose down
```

To also delete data:

```bash
docker-compose down -v
```

## Switching back to SQLite

```bash
unset DATABASE_URL
```

The runners will automatically fall back to SQLite.

## What's next?

- Read [POSTGRES_SETUP.md](POSTGRES_SETUP.md) for production AWS RDS setup
- Check database with: `psql postgresql://acitrack:acitrack@localhost:5432/acitrack`
- Monitor with: `docker-compose logs -f postgres`

## Troubleshooting

**"Connection refused"**
- Check Docker is running: `docker ps`
- Restart: `docker-compose restart`

**"Role does not exist"**
- Recreate container: `docker-compose down -v && docker-compose up -d`
- Wait 10 seconds, then retry migrations

**"Table already exists"**
- Skip migration: `alembic stamp head`
- Or drop and recreate: `docker-compose down -v && docker-compose up -d && alembic upgrade head`
