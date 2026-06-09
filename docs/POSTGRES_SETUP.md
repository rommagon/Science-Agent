# PostgreSQL Setup for acitrack

This guide explains how to set up PostgreSQL for acitrack, both locally (Docker) and for production (AWS RDS).

## Overview

acitrack supports both SQLite and PostgreSQL storage:
- **SQLite** (default): Lightweight, file-based storage for development and small deployments
- **PostgreSQL**: Scalable, production-ready database with advanced querying capabilities

The storage backend is automatically selected based on the `DATABASE_URL` environment variable:
- If `DATABASE_URL` starts with `postgresql://`, PostgreSQL is used
- Otherwise, SQLite is used (fallback)

## Local Development (Docker Compose)

### 1. Start PostgreSQL

Start the local PostgreSQL container:

```bash
docker-compose up -d
```

This starts a PostgreSQL 15 container with:
- **Database**: `acitrack`
- **User**: `acitrack`
- **Password**: `acitrack`
- **Port**: `5432`
- **Data volume**: `postgres_data` (persisted)

Check the container is running:

```bash
docker-compose ps
```

### 2. Set up the database URL

Export the DATABASE_URL environment variable:

```bash
export DATABASE_URL="postgresql://acitrack:acitrack@localhost:5432/acitrack"
```

Or add it to your `.env` file:

```bash
echo 'DATABASE_URL="postgresql://acitrack:acitrack@localhost:5432/acitrack"' >> .env
```

### 3. Run database migrations

Install migration dependencies:

```bash
pip install alembic psycopg2-binary
```

Run Alembic migrations to create tables:

```bash
alembic upgrade head
```

This creates the following tables:
- `papers` - publications metadata
- `runs` - run history
- `run_papers` - junction table linking runs and papers
- `relevancy_events` - relevancy scoring events
- `tri_model_events` - tri-model scoring events

### 4. (Optional) Migrate existing SQLite data

If you have existing data in SQLite (`data/db/acitrack.db`), migrate it to PostgreSQL:

```bash
python scripts/migrate_sqlite_to_postgres.py
```

This copies all data from SQLite to PostgreSQL:
- publications → papers
- pub_runs → run_papers
- runs → runs
- relevancy_scoring_events → relevancy_events
- tri_model_scoring_events → tri_model_events

### 5. Run acitrack

Now run acitrack normally - it will automatically use PostgreSQL:

```bash
python run.py --daily --lookback-hours 48
```

Or for tri-model:

```bash
python run_tri_model_daily.py --run-date 2026-01-22
```

### 6. Stop PostgreSQL

Stop the container:

```bash
docker-compose down
```

To also remove the data volume:

```bash
docker-compose down -v
```

## Production (AWS RDS)

### 1. Create RDS PostgreSQL instance

Create a PostgreSQL RDS instance via AWS Console or CLI:

```bash
aws rds create-db-instance \
    --db-instance-identifier acitrack-db \
    --db-instance-class db.t3.micro \
    --engine postgres \
    --engine-version 15.4 \
    --master-username acitrack \
    --master-user-password <YOUR_PASSWORD> \
    --allocated-storage 20 \
    --vpc-security-group-ids <YOUR_SG_ID> \
    --db-name acitrack
```

### 2. Get the RDS endpoint

Get the endpoint from AWS Console or CLI:

```bash
aws rds describe-db-instances \
    --db-instance-identifier acitrack-db \
    --query 'DBInstances[0].Endpoint.Address' \
    --output text
```

### 3. Set DATABASE_URL

Export the DATABASE_URL with your RDS credentials:

```bash
export DATABASE_URL="postgresql://acitrack:<PASSWORD>@<RDS_ENDPOINT>:5432/acitrack"
```

For example:

```bash
export DATABASE_URL="postgresql://acitrack:mypassword@acitrack-db.abc123.us-east-1.rds.amazonaws.com:5432/acitrack"
```

### 4. Run migrations

Run Alembic migrations against RDS:

```bash
alembic upgrade head
```

### 5. (Optional) Migrate data

If migrating from SQLite:

```bash
python scripts/migrate_sqlite_to_postgres.py
```

### 6. Deploy

Deploy your application with the `DATABASE_URL` environment variable set.

## Verification

### Check database connection

Test the connection:

```bash
python scripts/smoke_test_postgres.py
```

This verifies:
- Connection to PostgreSQL
- Table creation
- Insert/query operations
- Event storage

### Query the database

Connect with psql:

```bash
# Local
psql postgresql://acitrack:acitrack@localhost:5432/acitrack

# RDS
psql "postgresql://acitrack:<PASSWORD>@<RDS_ENDPOINT>:5432/acitrack"
```

Example queries:

```sql
-- Count papers
SELECT COUNT(*) FROM papers;

-- Recent runs
SELECT run_id, started_at, new_count, total_deduped
FROM runs
ORDER BY started_at DESC
LIMIT 10;

-- Top scored papers (tri-model)
SELECT publication_id, title, final_relevancy_score, confidence
FROM tri_model_events
ORDER BY final_relevancy_score DESC
LIMIT 10;

-- Relevancy scoring stats by run
SELECT run_id, mode, COUNT(*) as events, AVG(relevancy_score) as avg_score
FROM relevancy_events
GROUP BY run_id, mode
ORDER BY run_id DESC;
```

## Schema Information

### Tables

1. **papers** - Publications metadata
   - Primary key: `id`
   - Indexes: `published_at`, `source`, `run_id`, `created_at`

2. **runs** - Run history
   - Primary key: `run_id`
   - Indexes: `(mode, window_end)`, `started_at`

3. **run_papers** - Run ↔ Paper associations
   - Primary key: `(run_id, pub_id)`
   - Indexes: `run_id`, `status`, `source`, `published_at`

4. **relevancy_events** - Relevancy scoring events
   - Primary key: `id` (auto-increment)
   - Unique constraint: `(run_id, publication_id, prompt_version)`
   - Indexes: `run_id`, `publication_id`, `created_at`, `mode`

5. **tri_model_events** - Tri-model scoring events
   - Primary key: `id` (auto-increment)
   - Unique constraint: `(run_id, publication_id)`
   - Indexes: `run_id`, `publication_id`, `created_at`, `mode`, `final_relevancy_score`

## Troubleshooting

### Connection refused

If you get "connection refused":

1. Check Docker is running: `docker ps`
2. Check PostgreSQL is healthy: `docker-compose ps`
3. Verify DATABASE_URL is correct: `echo $DATABASE_URL`
4. Check PostgreSQL logs: `docker-compose logs postgres`

### Migration errors

If Alembic migration fails:

1. Check DATABASE_URL is set: `echo $DATABASE_URL`
2. Verify PostgreSQL is accessible: `psql $DATABASE_URL -c "SELECT 1"`
3. Check for existing tables: `psql $DATABASE_URL -c "\dt"`
4. If tables exist, either drop them or use `alembic stamp head` to mark as migrated

### Slow queries

If queries are slow:

1. Check indexes exist: `\d+ papers` in psql
2. Run ANALYZE to update statistics: `ANALYZE;`
3. Consider adding more indexes for your query patterns
4. Monitor with `EXPLAIN ANALYZE <query>`

### Data migration issues

If migration script fails:

1. Check both databases are accessible
2. Verify SQLite database exists: `ls -lh data/db/acitrack.db`
3. Check PostgreSQL tables exist: `psql $DATABASE_URL -c "\dt"`
4. Run with verbose logging for details

## Reverting to SQLite

To revert to SQLite:

```bash
unset DATABASE_URL
# or remove from .env file
```

The application will automatically fall back to SQLite storage.

## Notes

- Both runners (`run.py`, `run_tri_model_daily.py`) support PostgreSQL
- Storage operations are non-blocking - if database fails, pipeline continues
- Connection pooling is enabled (min=1, max=10 connections)
- All JSON fields use TEXT type with JSON validation at application level
- Timestamps use ISO8601 format for compatibility
