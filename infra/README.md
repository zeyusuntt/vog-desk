# Local infrastructure

Zetaris runs in Docker from its own platform folder, outside this repository. It cannot read files on the laptop, so the data layer is two local services:

- An S3 store (RustFS, service `s3`) holds the derived Parquet. Zetaris reads it as S3.
- Postgres holds the institutional tables. Zetaris reads it over JDBC.

`infra/zetaris/` holds the sources, the semantic layer and the data quality rules, applied by `infra/zetaris/setup.py`.

## Start

```
docker compose -f infra/compose.yml up -d
```

`scripts/load_minio.py` creates the bucket `vogdesk` on first use.

Stop with `docker compose -f infra/compose.yml down`. Add `-v` to also delete the data volumes.

## Ports

Both services bind to 127.0.0.1 only.

| Service | Host port | Why |
| --- | --- | --- |
| Postgres | 5433 | 5432 is often taken by another local Postgres. |
| S3 API | 9000 | |
| S3 console | 9001 | |

Inside Docker the services keep their normal ports (5432 and 9000).

## Network

Both services also join the external network `zetaris_default`. This lets the Zetaris containers reach them by service name (`vogpg`, `s3`). The network existed when this was written. If `docker network ls` does not show it, the name of the Zetaris network must be checked and the `networks` block in `compose.yml` changed. Compose fails on start if an external network is missing.

## Load the S3 store

```
.venv/bin/python scripts/load_minio.py
```

This uploads `data/derived/*.parquet` to `s3://vogdesk/derived/` and `data/raw/gfs/*.parquet` to `s3://vogdesk/raw/gfs/`. Use `--dry-run` to list files without uploading. The script needs `boto3`. It reads `.env` if `python-dotenv` is installed.

The store's console is at http://localhost:9001.

## Postgres for Zetaris

Database `vogdesk`, schema `institutional`.

- From inside Docker (Zetaris): `jdbc:postgresql://vogpg:5432/vogdesk`
- From the host: `jdbc:postgresql://localhost:5433/vogdesk`

Tables: `stations`, `action_plan_levels`, `school_calendar`, `forecasts`, `verifications`, and the view `forecast_verification`.

`init.sql` runs only when the data volume is empty. To re-run it from scratch, run `down -v` and then `up -d`. The script is idempotent, so running it by hand against an existing database is also safe.

`action_plan_levels.actions_summary` is empty on purpose. The project owner pastes the official action text from the plan.

## Credentials

The defaults (`vogdesk` / `vogdesk-local-only`) are for local development only. Real values, if ever needed, go in `.env`. Never commit them.
Variables: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.


## Note on the S3 server

The compose file ran MinIO at first. On 23 Oct 2026 MinIO's Docker Hub repository was gone and quay.io refused anonymous pulls, so the service now runs RustFS (`rustfs/rustfs:latest`), which speaks the same S3 API and accepts the same credential variables. The service is named `s3`; Zetaris reaches it at `http://s3:9000` on the `zetaris_default` network. `scripts/load_minio.py` creates the `vogdesk` bucket if it does not exist.
