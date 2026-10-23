-- Institutional tables for vog-desk. Zetaris reads these over JDBC.
-- Safe to run more than once. Postgres runs it only on first start of an empty volume.

CREATE SCHEMA IF NOT EXISTS institutional;

-- Monitoring stations used by the desk.
CREATE TABLE IF NOT EXISTS institutional.stations (
    site_id   text PRIMARY KEY,
    name      text,
    latitude  double precision,
    longitude double precision,
    agency    text,
    role      text
);

INSERT INTO institutional.stations (site_id, name, latitude, longitude, agency, role) VALUES
    ('150012016', 'Pahala',              19.2039, -155.4802, 'Hawaii DOH',            'target'),
    ('150010005', 'Hawaii Volcanoes NP', 19.4308, -155.2578, 'National Park Service', 'near summit'),
    ('150012020', 'Ocean View',          19.1176, -155.7781, 'Hawaii DOH',            'downwind replication'),
    ('150011006', 'Hilo',                19.7176, -155.1105, 'Hawaii DOH',            'windward reference')
ON CONFLICT DO NOTHING;

-- SO2 action levels from the 2018 Hawaii Schools SO2 Action Plan.
-- actions_summary is left NULL on purpose. The project owner pastes the official
-- action text from the plan. A tool must not paraphrase it.
CREATE TABLE IF NOT EXISTS institutional.action_plan_levels (
    level           smallint PRIMARY KEY,
    color           text NOT NULL,
    so2_ppm_min     numeric,
    so2_ppm_max     numeric,
    averaging       text NOT NULL DEFAULT '15-minute average',
    actions_summary text,
    source          text NOT NULL
);

INSERT INTO institutional.action_plan_levels (level, color, so2_ppm_min, so2_ppm_max, source) VALUES
    (0, 'green',  0.00, 0.10, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)'),
    (1, 'yellow', 0.11, 0.20, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)'),
    (2, 'orange', 0.21, 1.00, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)'),
    (3, 'red',    1.01, 3.00, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)'),
    (4, 'purple', 3.01, 5.00, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)'),
    (5, 'maroon', 5.01, NULL, 'Hawaii Interagency Vog Information Dashboard, Schools SO2 Action Plan (2018)')
ON CONFLICT DO NOTHING;

-- School calendar and bell times. No seed rows here.
-- An illustrative bell schedule for Kau High and Pahala Elementary is loaded by a
-- script later. Those rows must be marked illustrative in the note column.
CREATE TABLE IF NOT EXISTS institutional.school_calendar (
    school     text,
    day        date,
    in_session boolean NOT NULL,
    bell_start time,
    bell_end   time,
    note       text,
    PRIMARY KEY (school, day)
);

-- One row per forecast issued by the desk.
CREATE TABLE IF NOT EXISTS institutional.forecasts (
    case_id         text PRIMARY KEY,
    issue_date_hst  date NOT NULL,
    issue_utc       timestamptz NOT NULL,
    probability     double precision,
    action_level    text,
    decision_kind   text CHECK (decision_kind IN ('issue', 'issue_with_caveat', 'handover')),
    discussion      text,
    model_version   text,
    trace_path      text,
    forecast_kind   text NOT NULL DEFAULT 'full_desk',
    created_utc     timestamptz NOT NULL DEFAULT now()
);

-- Verification of a forecast against the observed outcome.
CREATE TABLE IF NOT EXISTS institutional.verifications (
    case_id           text PRIMARY KEY REFERENCES institutional.forecasts (case_id),
    observed_max_ppb  double precision,
    n_hours           smallint,
    event             boolean,
    outcome           text CHECK (outcome IN ('hit', 'miss', 'false_alarm', 'correct_negative', 'excluded', 'handover')),
    brier             double precision,
    memory_record_id  text,
    label_source      text,
    created_utc       timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW institutional.forecast_verification AS
SELECT
    f.case_id,
    f.issue_date_hst,
    f.issue_utc,
    f.probability,
    f.action_level,
    f.decision_kind,
    f.discussion,
    f.model_version,
    f.trace_path,
    f.forecast_kind,
    f.created_utc AS forecast_created_utc,
    v.observed_max_ppb,
    v.n_hours,
    v.event,
    v.outcome,
    v.brier,
    v.memory_record_id,
    v.label_source,
    v.created_utc AS verification_created_utc
FROM institutional.forecasts AS f
JOIN institutional.verifications AS v ON v.case_id = f.case_id;
