# Zetaris reflection

Written for the judges' question at the opening ceremony: how the unified semantic layer sped us up, how we would use it at work, and what Zetaris should improve. Everything here happened on 23 October 2026 on the local Docker install (2.4.3.1, Apple Silicon under Rosetta), with the REST API as the only interface.

## What we built

Eight sources that never meet in one database: EPA AQS validated hourly SO2 (Parquet), AirNow preliminary hourly SO2 and station wind (Parquet), the USGS fountaining-episode table joined to HVO notice publication times (Parquet), Hilo radiosonde inversion heights (Parquet), NOAA GFS point forecasts (two Parquet files), and the school's institutional tables in Postgres (stations, action-plan levels, forecasts, verifications). Zetaris reads the Parquet from a local S3 store and the tables over JDBC. On top sits one USL, `pahala_exposure`, with eight governed tables, each carrying `available_utc`: the moment a row became public. Eleven data quality rules sit on those tables. The agents never see a base table; one tool, `run_sql`, accepts a read-only statement over the USL and rewrites every table reference to `available_utc <= <issue time>`.

## Where it sped us up

- One query language over Parquet and Postgres meant the agents' evidence code is a single module. We kept a Python fallback for judges without Zetaris, and a test checks that the two return identical rows on the same night. They do.
- The USL turned our pre-registration into something a machine enforces. The no-lookahead rule used to be a paragraph in a document. Now it is a column on every table and a rewrite in one function, and the integration tests run against Zetaris itself.
- Data quality rules caught something we had not written down. `analyzer_upper_range` flagged exactly two hours, Ocean View at 01:00 and 02:00 HST on 3 September 2025 (1,206 and 1,166 ppb), the same night the Department of Health marks with an asterisk in its own list. The first version of the rule also flagged 4,468 small negative readings. We looked, decided they are zero noise from validated analyzers (mostly at the Volcanoes NP station), split the rule in two, and documented that the features clip them at zero.
- The duty lead's re-query step is a Zetaris query with a different as-of filter or station, so "check the upwind station again" is one line of SQL.

## How we would use it at work

An air-quality or emergency-management office has the same mix: federal validated data that arrives months late, state preliminary data that arrives hourly, agency notices on their own clock, numerical forecasts in their own files, and an institutional database holding the rules people act on. Nobody wants to copy all of that into one warehouse before the first forecast. A semantic layer can leave the data where it is, stamp every row with the time it became knowable, and hold a data quality rule such as "this analyzer saturates at 1,000 ppb". We would use it as the contract between forecasters and data engineers. The USL defines what the agents may read, and the rules record what the engineers promise about it.

## What Zetaris should improve

In the order we ran into them.

1. **Service name `postgres`.** A service with that name on the shared Docker network shadows the platform's own database. Our compose file joined the Zetaris network with a Postgres service called `postgres`; Zetaris's internal connections were round-robined to our container and failed authentication until we renamed ours `vogpg`. The install guide should warn about the reserved service names, or the platform should pin its own hosts by container name.
2. **Schema scan.** `REGISTER DATASOURCE TABLES` only scans the `public` schema. Our tables live in `institutional`; registration returned success with nothing registered. `CREATE DATASOURCE VIEW` with schema-qualified SQL works and is what we use, but the registration call should either take a schema or report that it found no tables.
3. **Reserved column names.** COMPILE USL rejects `source` and `level` as "Invalid DDL statement" with no hint. A list of reserved words, or the offending token in the error, would have saved an hour of bisection.
4. **Nullability in ACTIVATE.** The check is stricter than Parquet allows. Every Parquet column is nullable to Spark, so a `NOT NULL` column in the USL DDL cannot be activated from Parquet without `COALESCE`. The error message is excellent and even suggests the fix. Still, a NOT NULL constraint ends up written as a data quality rule. Either accept `WHERE col IS NOT NULL` as evidence of non-nullability, or say so in the USL guide.
5. **Descriptions.** They reject colons and parentheses. Minor, but the first thing a new user types in a `DESCRIBE BY` clause is a sentence.
6. **REST route.** It needs `source: "SqlEditor"` and a `queryId`, and returns `{headers, data}` with every value as a string. None of this is in the public getting-started docs; we learned it from the repository's own helper scripts. Typed values (or a `types` array next to `headers`) would remove a parsing layer from every client.
7. **Latency.** Through the REST route on this laptop, a count over the 175,000-row SO2 table takes 4 to 8 s and the full DQ run on it 27 to 35 s. That is fine for a nightly desk and slow for an interactive agent loop. A documented way to cache or materialise a USL table in the local store would help.
8. **Discovery.** `SHOW LIGHTNING TABLES` and `SHOW TABLES IN <datasource>` are disabled or fail to parse on this release, so you have to remember what you registered. `SHOW DATASOURCE VIEWS` returned an empty list even with six views in use.

None of these stopped us. The same query returned the same answer every time, and the information cutoff lives in the data, where a test can check it.
