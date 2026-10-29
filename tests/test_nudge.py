"""pre_llm_call adds the subsidy paragraph only on a company or subsidy turn."""


def test_nudge_is_absent_on_a_settings_question(plugin):
    assert plugin._nudge_subsidies(user_message="hermes の設定はどこで見る？") is None
    assert plugin._nudge_subsidies(user_message="コーヒーの入れ方") is None


def test_nudge_is_present_for_a_subsidy_word_only(plugin):
    assert plugin._nudge_subsidies(user_message="トヨタ自動車") is None
    grant = plugin._nudge_subsidies(user_message="今出ている補助金")
    assert isinstance(grant, dict)
    assert "subsidies_search" in grant["context"]
    assert "対象です" in grant["context"]
    assert "not_claimed" in grant["context"]
