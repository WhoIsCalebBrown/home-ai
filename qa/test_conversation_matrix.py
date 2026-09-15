from conversation_matrix import run_matrix


def test_generated_media_and_confirmation_matrix_is_clean():
    result = run_matrix()
    assert result["scenario_count"] >= 30
    assert result["confirmation_count"] >= 12
    assert result["ok"], result["failures"]
