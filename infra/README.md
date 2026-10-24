# Local infrastructure

Zetaris runs in Docker from `the Zetaris platform folder`. It cannot read files on the laptop. So the data layer is two local services:

- MinIO (S3 API) holds the derived Parquet. Zetaris reads it as S3.
- Postgres holds the institutional tables. Zetaris reads it over JDBC.

`infra/zetaris/` (sources, semantic layer, data quality rules) is built separately in Cursor.

## Start

```
docker compose -f infra/compose.yml up -d
```

The `minio-init` container creates the bucket `vogdesk` and exits. That is expected.

Stop with `docker compose -f infra/compose.yml down`. Add `-v` to also delete the data volumes.

## Ports

Both services bind to 127.0.0.1 only.

| Service | Host port | Why |
| --- | --- | --- |
| Postgres | 5433 | 5432 is used by `another local Postgres`, 55000 by `zetaris-postgres-1`. |
| MinIO S3 API | 9000 | Free on this machine. |
| MinIO console | 9001 | Free on this machine. |

Inside Docker the services keep their normal ports (5432 and 9000).

## Network

Both services also join the external network `zetaris_default`. This lets the Zetaris containers reach them by service name (`postgres`, `s3`). The network existed when this was written. If `docker network ls` does not show it, the name of the Zetaris network must be checked and the `networks` block in `compose.yml` changed. Compose fails on start if an external network is missing.

## Load MinIO

```
.venv/bin/python scripts/load_minio.py
```

This uploads `data/derived/*.parquet` to `s3://vogdesk/derived/` and `data/raw/gfs/*.parquet` to `s3://vogdesk/raw/gfs/`. Use `--dry-run` to list files without uploading. The script needs `boto3`. It reads `.env` if `python-dotenv` is installed.

The MinIO console is at http://localhost:9001.

## Postgres for Zetaris

Database `vogdesk`, schema `institutional`.

- From inside Docker (Zetaris): `jdbc:postgresql://postgres:5432/vogdesk`
- From the host: `jdbc:postgresql://localhost:5433/vogdesk`

Tables: `stations`, `action_plan_levels`, `school_calendar`, `forecasts`, `verifications`, and the view `forecast_verification`.

`init.sql` runs only when the data volume is empty. To re-run it from scratch, run `down -v` and then `up -d`. The script is idempotent, so running it by hand against an existing database is also safe.

`action_plan_levels.actions_summary` is empty on purpose. The project owner pastes the official action text from the plan.

## Credentials

The defaults (`vogdesk` / `vogdesk-local-only`) are for local development only. Real values, if ever needed, go in `.env`. Never commit them.
Variables: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.


## Note on the S3 server

The compose file ran MinIO at first. On 23 Oct 2026 MinIO's Docker Hub repository was gone and quay.io refused anonymous pulls, so the service now runs RustFS (`rustfs/rustfs:latest`), which speaks the same S3 API and accepts the same credential variables. The service is named `s3`; Zetaris reaches it at `http://s3:9000` on the `zetaris_default` network. `scripts/load_minio.py` creates the `vogdesk` bucket if it does not exist.
