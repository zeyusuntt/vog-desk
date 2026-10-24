-- Layer 1: register the sources where they sit. Zetaris reads Parquet from the local S3 store
-- (service `s3` on the shared Docker network) and the institutional tables from Postgres over JDBC.
-- Placeholders ${...} are filled at run time from .env by infra/zetaris/setup.py; nothing here is a secret.

CREATE LIGHTNING DATABASE VOG_S3 DESCRIBE BY "vog-desk derived Parquet on local S3, AQS AirNow HVO-episodes Hilo-soundings GFS-points";

CREATE LIGHTNING FILESTORE TABLE aqs_hawaii_so2 FROM VOG_S3 FORMAT PARQUET OPTIONS (
  path "s3a://vogdesk/derived/aqs_hawaii_so2.parquet",
  awsAccessKeyId "${S3_ACCESS_KEY}", awsSecretAccessKey "${S3_SECRET_KEY}",
  isS3BucketPublic "false", useS3PathStyleAccess "true", s3Endpoint "${S3_ENDPOINT}"
);

CREATE LIGHTNING FILESTORE TABLE airnow_hawaii FROM VOG_S3 FORMAT PARQUET OPTIONS (
  path "s3a://vogdesk/derived/airnow_hawaii.parquet",
  awsAccessKeyId "${S3_ACCESS_KEY}", awsSecretAccessKey "${S3_SECRET_KEY}",
  isS3BucketPublic "false", useS3PathStyleAccess "true", s3Endpoint "${S3_ENDPOINT}"
);

CREATE LIGHTNING FILESTORE TABLE episodes_master FROM VOG_S3 FORMAT PARQUET OPTIONS (
  path "s3a://vogdesk/derived/episodes_master.parquet",
  awsAccessKeyId "${S3_ACCESS_KEY}", awsSecretAccessKey "${S3_SECRET_KEY}",
  isS3BucketPublic "false", useS3PathStyleAccess "true", s3Endpoint "${S3_ENDPOINT}"
);

CREATE LIGHTNING FILESTORE TABLE raob_launches FROM VOG_S3 FORMAT PARQUET OPTIONS (
  path "s3a://vogdesk/derived/raob_launches.parquet",
  awsAccessKeyId "${S3_ACCESS_KEY}", awsSecretAccessKey "${S3_SECRET_KEY}",
  isS3BucketPublic "false", useS3PathStyleAccess "true", s3Endpoint "${S3_ENDPOINT}"
);

CREATE LIGHTNING FILESTORE TABLE gfs_points FROM VOG_S3 FORMAT PARQUET OPTIONS (
  path "s3a://vogdesk/raw/gfs/",
  awsAccessKeyId "${S3_ACCESS_KEY}", awsSecretAccessKey "${S3_SECRET_KEY}",
  isS3BucketPublic "false", useS3PathStyleAccess "true", s3Endpoint "${S3_ENDPOINT}"
);

CREATE DATASOURCE VOG_PG DESCRIBE BY "vog-desk institutional tables, stations action-plan-levels school-calendar forecasts verifications" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://vogpg:5432/${PG_DB}?currentSchema=institutional",
  username "${PG_USER}",
  password "${PG_PASSWORD}",
  fetchsize "1000"
);

-- Postgres tables live in schema `institutional`; table registration only scans `public`, so each
-- table is exposed as a datasource view whose SQL runs on Postgres itself.
CREATE DATASOURCE VIEW stations FROM VOG_PG AS SELECT * FROM institutional.stations;
CREATE DATASOURCE VIEW action_plan_levels FROM VOG_PG AS SELECT * FROM institutional.action_plan_levels;
CREATE DATASOURCE VIEW school_calendar FROM VOG_PG AS SELECT * FROM institutional.school_calendar;
CREATE DATASOURCE VIEW forecasts FROM VOG_PG AS SELECT * FROM institutional.forecasts;
CREATE DATASOURCE VIEW verifications FROM VOG_PG AS SELECT * FROM institutional.verifications;
CREATE DATASOURCE VIEW forecast_verification FROM VOG_PG AS SELECT * FROM institutional.forecast_verification;
