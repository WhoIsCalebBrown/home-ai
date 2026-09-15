from conversation_matrix import run_matrix
from scenario_catalog import build_catalog


def test_generated_media_and_confirmation_matrix_is_clean():
    result = run_matrix()
    assert result["scenario_count"] >= 30
    assert result["confirmation_count"] >= 12
    assert result["ok"], result["failures"]


def test_multi_turn_catalog_has_at_least_one_hundred_distinct_base_scenarios():
    catalog = build_catalog()
    ids = {item["scenario_id"] for item in catalog}
    assert len(catalog) >= 100
    assert len(ids) == len(catalog)
    assert all(len(item["turns"]) >= 4 for item in catalog)
    assert all(item["writes_allowed"] is False for item in catalog)
