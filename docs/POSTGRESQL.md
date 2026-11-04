# Local PostgreSQL

The project supports both SQLite and PostgreSQL through `DATABASE_URL`. SQLite
remains the zero-configuration fallback. PostgreSQL is used in GitHub Actions.

## Standalone Windows setup

When Docker is unavailable, use the official binary archive linked from
[PostgreSQL's Windows downloads](https://www.postgresql.org/download/windows/).
Extract the archive so `tmp/postgres/runtime/pgsql/bin/pg_ctl.exe` exists. The
verified local version is PostgreSQL 17.11. Install `requirements-ml.txt`, which
includes the PostgreSQL driver, and run:

```powershell
python scripts/local_postgres.py start
python scripts/local_postgres.py manage migrate
python scripts/local_postgres.py manage test
python scripts/local_postgres.py manage runserver 127.0.0.1:8000 --noreload
```

The helper starts a local-only server on `127.0.0.1:54329`, creates a separate
`lostlink` database, and generates a random SCRAM password. It installs no Windows
service. Database files, credentials and logs stay in ignored `tmp/postgres/`.
The wrapper supplies `DATABASE_URL` to its Django child process; ordinary
`python manage.py` commands still use SQLite unless the environment overrides it.
`--bin-dir PATH` can point to an existing PostgreSQL binary directory.

Use `python scripts/local_postgres.py status` to inspect the server and
`python scripts/local_postgres.py stop` to stop it without deleting data.
Keep `tmp/postgres/data/` and `tmp/postgres/connection.json` together. Do not remove
`tmp/postgres/` as ordinary build output; it contains the local database.

## Copy existing SQLite data into a new PostgreSQL database

Stop application writes first. Only import into an empty PostgreSQL application
database. Keep the original SQLite file and media directory as a backup:

```powershell
python -X utf8 manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --output tmp/postgres/sqlite-backup.json
python scripts/local_postgres.py manage migrate
python scripts/local_postgres.py manage loaddata tmp/postgres/sqlite-backup.json
```

The fixture contains private application data and must remain local. Compare
record counts and re-exported records before switching the app. Uploads continue
to use the existing `media/` directory.

## Docker alternative

`docker compose up -d db` provides PostgreSQL on the same port; stop the standalone
server first. Set `DATABASE_URL` using the Compose database credentials before
running migrations or the app. These are development setups, not hosted production
deployments.
