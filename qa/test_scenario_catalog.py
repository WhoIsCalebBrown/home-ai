from scenario_catalog import build_catalog


def test_catalog_has_at_least_one_hundred_meaningful_conversations():
    scenarios = build_catalog()
    assert len(scenarios) == 100
    assert all(len(item["turns"]) >= 4 for item in scenarios)
    assert all(item["writes_allowed"] is False for item in scenarios)
    assert len({item["canonical_entity"] for item in scenarios}) >= 8

