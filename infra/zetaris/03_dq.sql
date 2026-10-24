-- Layer 3: data quality rules on the semantic layer. One line each; the desk reads the rule names in
-- its evidence items so a flagged value is never cited as a plain number.

-- Analyzer range: the Pahala and Ocean View analyzers top out near 1,000 ppb. A value above that is
-- censored ("at least"), not a measurement. Ocean View read 1,206 ppb on 3 Sep 2025 and DOH marked it.
REGISTER DQ analyzer_range TABLE lightning.metastore.vog.pahala_exposure.so2_hourly AS
so2_ppb IS NOT NULL AND so2_ppb >= 0 AND so2_ppb <= 1000;

-- Publication order: a value can never be public before the hour it describes has ended.
REGISTER DQ published_after_observed TABLE lightning.metastore.vog.pahala_exposure.so2_hourly AS
available_utc >= time_utc + INTERVAL 1 HOURS;

-- Preliminary values (AirNow) must be flagged as such; validated AQS values must not be.
REGISTER DQ preliminary_flag_matches_source TABLE lightning.metastore.vog.pahala_exposure.so2_hourly AS
(data_source = 'airnow' AND preliminary = true) OR (data_source = 'aqs' AND preliminary = false);

-- An episode boundary cannot be known before it happened.
REGISTER DQ boundary_known_after_event TABLE lightning.metastore.vog.pahala_exposure.episodes_known AS
(start_known_utc IS NULL OR start_known_utc >= start_utc) AND (end_known_utc IS NULL OR end_utc IS NULL OR end_known_utc >= end_utc);

-- Only the 18Z run is allowed, and only the three leads the features use.
REGISTER DQ gfs_run_is_18z TABLE lightning.metastore.vog.pahala_exposure.gfs_points AS
hour(run_init_utc) = 18 AND fxx IN (10, 16, 22);

-- Inversion base search window from PREREG: 500 to 4,000 m (4,000 means none found).
REGISTER DQ inversion_in_search_window TABLE lightning.metastore.vog.pahala_exposure.sounding_levels AS
inversion_base_m IS NULL OR (inversion_base_m >= 500 AND inversion_base_m <= 4000);

-- Action plan levels: contiguous bands in ppm, 15-minute averaging.
REGISTER DQ action_levels_in_ppm TABLE lightning.metastore.vog.pahala_exposure.action_plan_levels AS
so2_ppm_min >= 0 AND (so2_ppm_max IS NULL OR so2_ppm_max > so2_ppm_min) AND averaging = '15-minute average';

-- Required columns (declared nullable in the DDL because Spark infers Parquet columns as nullable).
REGISTER DQ required_columns TABLE lightning.metastore.vog.pahala_exposure.so2_hourly AS
site_id IS NOT NULL AND time_utc IS NOT NULL AND data_source IS NOT NULL AND preliminary IS NOT NULL AND available_utc IS NOT NULL;
REGISTER DQ required_columns TABLE lightning.metastore.vog.pahala_exposure.gfs_points AS
run_init_utc IS NOT NULL AND valid_utc IS NOT NULL AND var IS NOT NULL AND point IS NOT NULL AND available_utc IS NOT NULL;

LIST DQ USL lightning.metastore.vog.pahala_exposure;
RUN DQ TABLE lightning.metastore.vog.pahala_exposure.so2_hourly;
RUN DQ TABLE lightning.metastore.vog.pahala_exposure.episodes_known;
RUN DQ TABLE lightning.metastore.vog.pahala_exposure.gfs_points;
RUN DQ TABLE lightning.metastore.vog.pahala_exposure.sounding_levels;
RUN DQ TABLE lightning.metastore.vog.pahala_exposure.action_plan_levels;
