-- Layer 2: the Unified Semantic Layer `pahala_exposure`. One governed table per concept the desk
-- reasons about, each carrying `available_utc`, the time its rows became public (rules frozen in
-- docs/PREREG.md and mirrored in src/vogdesk/asof.py). Agents query these tables only, through
-- run_sql, which adds `available_utc <= as_of` for every table that has the column.
--
-- Lifecycle: design (DDL) -> COMPILE USL ... DEPLOY -> ACTIVATE each table with its SELECT ->
-- data quality rules (03_dq.sql). Query as lightning.metastore.vog.pahala_exposure.<table>.

CREATE NAMESPACE IF NOT EXISTS lightning.metastore;
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.vog;

COMPILE USL IF NOT EXISTS pahala_exposure DEPLOY NAMESPACE lightning.metastore.vog DDL
CREATE TABLE so2_hourly (
    site_id varchar(12),
    time_utc timestamp,
    so2_ppb double,
    data_source varchar(8),
    preliminary boolean,
    available_utc timestamp
);
CREATE TABLE station_wind (
    site_id varchar(12),
    time_utc timestamp,
    parameter varchar(8),
    value double,
    available_utc timestamp
);
CREATE TABLE episodes_known (
    episode int NOT NULL PRIMARY KEY,
    start_utc timestamp,
    end_utc timestamp,
    max_fountain_m double,
    volume_mm3 double,
    start_known_utc timestamp,
    end_known_utc timestamp,
    start_known_source varchar(10),
    end_known_source varchar(10)
);
CREATE TABLE gfs_points (
    issue_date_hst varchar(10),
    run_init_utc timestamp,
    fxx int,
    valid_utc timestamp,
    var varchar(8),
    level varchar(20),
    point varchar(12),
    grid_lat double,
    grid_lon double,
    value double,
    available_utc timestamp
);
CREATE TABLE sounding_levels (
    valid_utc timestamp NOT NULL PRIMARY KEY,
    inversion_base_m double,
    available_utc timestamp
);
CREATE TABLE stations (
    site_id varchar(12) NOT NULL PRIMARY KEY,
    name varchar(40),
    latitude double,
    longitude double,
    agency varchar(40),
    role varchar(30)
);
CREATE TABLE action_plan_levels (
    plan_level int NOT NULL PRIMARY KEY,
    color varchar(10),
    so2_ppm_min double,
    so2_ppm_max double,
    averaging varchar(30),
    actions_summary varchar(2000),
    plan_source varchar(120)
);
CREATE TABLE forecast_verification (
    case_id varchar(40) NOT NULL PRIMARY KEY,
    issue_date_hst date,
    issue_utc timestamp,
    probability double,
    action_level varchar(20),
    decision_kind varchar(20),
    forecast_kind varchar(20),
    observed_max_ppb double,
    n_hours int,
    event boolean,
    outcome varchar(20),
    brier double,
    created_utc timestamp
);
-- END USL

UPDATE USL lightning.metastore.vog.pahala_exposure SET DESCRIPTION 'vog-desk semantic layer for Pahala SO2 exposure forecasts. Every table carries available_utc, the moment its rows became public, so a forecast issued at time T can be reproduced with available_utc <= T. Sources, S3 Parquet from EPA AQS, AirNow, USGS HVO and NOAA GFS, Hilo soundings, plus Postgres institutional tables.';

-- One hourly SO2 series per station: AQS validated where AQS has the hour, AirNow preliminary otherwise.
ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.so2_hourly AS
SELECT a.site_id, a.time_utc, a.so2_ppb, 'aqs' AS data_source, false AS preliminary,
       a.time_utc + INTERVAL 2 HOURS AS available_utc
FROM VOG_S3.aqs_hawaii_so2 a WHERE a.so2_ppb IS NOT NULL
UNION ALL
SELECT n.site_id, n.time_utc, n.value AS so2_ppb, 'airnow' AS data_source, true AS preliminary,
       n.time_utc + INTERVAL 2 HOURS AS available_utc
FROM VOG_S3.airnow_hawaii n
LEFT ANTI JOIN VOG_S3.aqs_hawaii_so2 a
  ON a.site_id = n.site_id AND a.time_utc = n.time_utc AND a.so2_ppb IS NOT NULL
WHERE n.parameter = 'SO2' AND n.value IS NOT NULL;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.station_wind AS
SELECT site_id, time_utc, parameter, value, time_utc + INTERVAL 2 HOURS AS available_utc
FROM VOG_S3.airnow_hawaii WHERE parameter IN ('WS', 'WD', 'RWS', 'RWD') AND value IS NOT NULL;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.episodes_known AS
SELECT COALESCE(CAST(episode AS int), 0) AS episode, start_utc, end_utc, max_fountain_m, volume_mm3,
       start_known_utc, end_known_utc, start_known_source, end_known_source
FROM VOG_S3.episodes_master WHERE episode IS NOT NULL;

-- The 18Z run of day D becomes usable 6 h after initialisation (00Z D+1), before the 18:00 HST issue.
ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.gfs_points AS
SELECT DISTINCT issue_date_hst, to_timestamp(run_init_utc, "yyyy-MM-dd'T'HH:mm'Z'") AS run_init_utc, CAST(fxx AS int) AS fxx,
       CAST(valid_utc AS timestamp) AS valid_utc, var, level, point, grid_lat, grid_lon, value,
       to_timestamp(run_init_utc, "yyyy-MM-dd'T'HH:mm'Z'") + INTERVAL 6 HOURS AS available_utc
FROM VOG_S3.gfs_points WHERE status = 200;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.sounding_levels AS
SELECT COALESCE(valid_utc, TIMESTAMP '1970-01-01 00:00:00') AS valid_utc, inversion_base_m, available_utc
FROM VOG_S3.raob_launches WHERE valid_utc IS NOT NULL;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.stations AS
SELECT COALESCE(site_id, '') AS site_id, name, latitude, longitude, agency, role FROM VOG_PG.stations WHERE site_id IS NOT NULL;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.action_plan_levels AS
SELECT COALESCE(CAST(level AS int), -1) AS plan_level, color, CAST(so2_ppm_min AS double) AS so2_ppm_min,
       CAST(so2_ppm_max AS double) AS so2_ppm_max, averaging, actions_summary, source AS plan_source
FROM VOG_PG.action_plan_levels WHERE level IS NOT NULL;

ACTIVATE USL TABLE lightning.metastore.vog.pahala_exposure.forecast_verification AS
SELECT COALESCE(case_id, '') AS case_id, issue_date_hst, issue_utc, probability, action_level, decision_kind, forecast_kind,
       observed_max_ppb, CAST(n_hours AS int) AS n_hours, event, outcome, brier, forecast_created_utc AS created_utc
FROM VOG_PG.forecast_verification WHERE case_id IS NOT NULL;
