"""run_sql is the only way an agent reads data: read-only, semantic-layer views only, cut at as_of."""
import pandas as pd
import pytest

from vogdesk.zetaris import SqlRefused, parse_result, rewrite_asof

T = pd.Timestamp("2025-09-03 04:00", tz="UTC")


def test_asof_view_is_wrapped_with_the_cutoff():
    out = rewrite_asof("SELECT max(so2_ppb) FROM pahala_exposure.so2_hourly WHERE site_id = '150012016'", T)
    assert "FROM (SELECT * FROM pahala_exposure.so2_hourly WHERE available_utc <= TIMESTAMP '2025-09-03 04:00:00') so2_hourly" in out
    assert out.endswith("WHERE site_id = '150012016'")


def test_join_and_column_qualifiers_survive():
    sql = ("SELECT s.site_id, so2_hourly.so2_ppb FROM pahala_exposure.so2_hourly "
           "JOIN pahala_exposure.stations s ON s.site_id = so2_hourly.site_id")
    out = rewrite_asof(sql, T)
    assert "JOIN pahala_exposure.stations stations" in out  # plain view, no cutoff column
    assert "so2_hourly.so2_ppb" in out and out.count("available_utc <=") == 1


def test_hst_as_of_is_converted_to_utc():
    out = rewrite_asof("SELECT 1 FROM pahala_exposure.gfs_points", pd.Timestamp("2025-09-02 18:00", tz="Etc/GMT+10"))
    assert "TIMESTAMP '2025-09-03 04:00:00'" in out


@pytest.mark.parametrize("sql", [
    "DELETE FROM pahala_exposure.so2_hourly",
    "SELECT 1 FROM pahala_exposure.so2_hourly; DROP TABLE x",
    "SELECT * FROM vog_s3.aqs_hawaii_so2",
    "SELECT * FROM pahala_exposure.secret_view",
    "SELECT * FROM pahala_exposure.so2_hourly JOIN vog_pg.institutional.forecasts f ON 1=1",
    "SHOW LIGHTNING DATABASES",
    "SELECT 1",
])
def test_refuses_anything_but_reads_on_the_semantic_layer(sql):
    with pytest.raises(SqlRefused):
        rewrite_asof(sql, T)


def test_naive_as_of_is_refused():
    with pytest.raises(SqlRefused):
        rewrite_asof("SELECT 1 FROM pahala_exposure.so2_hourly", pd.Timestamp("2025-09-03 04:00"))


def test_parse_result_handles_the_rest_shape():
    rows, cols = parse_result({"headers": ["name", "id"], "data": [["NY_TAXI", "6"], ["TPCDS_DB", "307"]], "total": 2, "timeUsed": 113})
    assert cols == ["name", "id"] and rows[1] == {"name": "TPCDS_DB", "id": "307"}
    assert parse_result({"headers": ["one"], "data": []}) == ([], ["one"])
