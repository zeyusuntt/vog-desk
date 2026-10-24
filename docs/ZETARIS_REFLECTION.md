# Zetaris reflection

Written for the judges' question at the opening ceremony: how the unified semantic layer sped us up, how we would use it at work, and what Zetaris should improve. Everything here happened on 23 October 2026 on the local Docker install (2.4.3.1, Apple Silicon under Rosetta), with the REST API as the only interface.

## What we built

Eight sources that never meet in one database: EPA AQS validated hourly SO2 (Parquet), AirNow preliminary hourly SO2 and station wind (Parquet), the USGS fountaining-episode table joined to HVO notice publication times (Parquet), Hilo radiosonde inversion heights (Parquet), NOAA GFS point forecasts (two Parquet files), and the school's institutional tables in Postgres (stations, action-plan levels, forecasts, verifications). Zetaris reads the Parquet from a local S3 store and the tables over JDBC. On top sits one USL, `pahala_exposure`, with eight governed tables, each carrying `available_utc`: the moment a row became public. Eleven data quality rules sit on those tables. The agents never see a base table; one tool, `run_sql`, accepts a read-only statement over the USL and rewrites every table reference to `available_utc <= <issue time>`.

## Where it sped us up

- One query language over Parquet and Postgres meant the agents' evidence code is one module, not two. We kept a Python fallback for judges without Zetaris, and a test checks the two give identical answers on the same night. They do, to the row.
- The USL turned our pre-registration into something a machine enforces. The no-lookahead rule was a paragraph in a document; it is now a column on every table and a rewrite in one function, and the integration tests run against Zetaris itself, not against our own files.
- Data quality rules caught something we had not written down. `analyzer_upper_range` flagged exactly two hours, Ocean View at 01:00 and 02:00 HST on 3 September 2025 (1,206 and 1,166 ppb), the same night the Department of Health marks with an asterisk in its own list. The first version of the rule also flagged 4,468 small negative readings; that made us look, decide they are zero noise from validated analyzers (mostly at the Volcanoes NP station), split the rule, and document that the features clip them at zero. That is the kind of conversation a semantic layer is for.
- The DutyLead's re-query step is a Zetaris query with a different as-of filter or a different station, not new code. Because every table speaks the same language, "check the upwind station again" is one line.

## How we would use it at work

An air-quality or emergency-management office has exactly this shape of problem: federal validated data that arrives months late, state preliminary data that arrives hourly, agency notices with their own clock, numerical forecasts in their own files, and an institutional database with the rules people actually act on. Nobody wants to copy all of that into one warehouse before the first forecast. A semantic layer that leaves the data where it is, stamps every row with when it became knowable, and lets a data quality rule say "this analyzer saturates at 1,000 ppb" is the piece that was missing. We would use it as the contract between the forecasters and the data engineers: the USL is what the agents are allowed to read, the rules are what the engineers promise.

## What Zetaris should improve

Honest list, in the order we hit them.

1. **A service named `postgres` on the shared Docker network shadows the platform's own database.** Our compose file joined the Zetaris network with a Postgres service called `postgres`; Zetaris's internal connections were round-robined to our container and failed authentication until we renamed ours `vogpg`. The install guide should warn about the reserved service names, or the platform should pin its own hosts by container name.
2. **`REGISTER DATASOURCE TABLES` only scans the `public` schema.** Our tables live in `institutional`; registration returned success with nothing registered. `CREATE DATASOURCE VIEW` with schema-qualified SQL works and is what we use, but the registration call should either take a schema or report that it found no tables.
3. **Reserved column names fail COMPILE USL with a generic message.** `source` and `level` are rejected as "Invalid DDL statement" with no hint. A list of reserved words, or the offending token in the error, would have saved an hour of bisection.
4. **Nullability in ACTIVATE is stricter than Parquet allows.** Every Parquet column is nullable to Spark, so a `NOT NULL` column in the USL DDL cannot be activated from Parquet without `COALESCE`. The error message is excellent (it even suggests the fix), but the rule means a NOT NULL constraint has to be expressed as a data quality rule instead. Either accept `WHERE col IS NOT NULL` as evidence of non-nullability, or say so in the USL guide.
5. **Descriptions reject colons and parentheses.** Minor, but the first thing a new user types in a `DESCRIBE BY` clause is a sentence.
6. **The REST route needs `source: "SqlEditor"` and a `queryId`, and returns `{headers, data}` with every value as a string.** None of this is in the public getting-started docs; we learned it from the repository's own helper scripts. Typed values (or a `types` array next to `headers`) would remove a parsing layer from every client.
7. **Latency.** A count over the 175,000-row SO2 table takes 4 to 8 s and the full DQ run on it 27 to 35 s through the REST route on this laptop. Fine for a nightly desk, slow for an interactive agent loop; a documented way to cache or materialise a USL table to the local store would help.
8. **`SHOW LIGHTNING TABLES` and `SHOW TABLES IN <datasource>` are disabled or parse-error on this release**, so discovering what one has registered means remembering it. `SHOW DATASOURCE VIEWS` returned an empty list even with six views in use.

None of these stopped us. The semantic layer did what it promised: the same trusted answer every time, with the information cutoff written into the data rather than into our good intentions.
