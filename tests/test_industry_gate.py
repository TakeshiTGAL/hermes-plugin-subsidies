"""A company name is not an industry keyword."""
import json


def _gate(plugin):
    return __import__(plugin.__name__ + ".industry_gate", fromlist=["block"])


def test_company_name_is_blocked_when_no_industry_was_returned(plugin):
    gate = _gate(plugin)
    gate.clear()
    blocked = gate.block(
        "subsidies_search", {"keyword": "トヨタ自動車"}, "s1",
    )
    assert blocked["action"] == "block"
    assert "not an industry keyword" in blocked["message"]
    assert "not_claimed" in blocked["message"]
    assert "No award" in blocked["message"]
    assert "Sales are not" in blocked["message"]


def test_detail_and_article_tools_are_not_blocked(plugin):
    gate = _gate(plugin)
    gate.clear()
    assert gate.block("subsidies_detail", {"id": "1"}, "s1") is None
    assert gate.block("subsidies_watch_setup", {}, "s1") is None


def test_keyword_copied_from_industry_text_is_allowed(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s2",
        "jp_corp_profile",
        json.dumps({"basic": {"industry": "自動車製造業"}}),
    )
    assert gate.block(
        "subsidies_search", {"keyword": "自動車"}, "s2",
    ) is None
    blocked = gate.block(
        "subsidies_search", {"keyword": "トヨタ自動車"}, "s2",
    )
    assert blocked["action"] == "block"


def test_tool_search_does_not_supply_an_industry(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s3",
        "tool_search",
        json.dumps({"industry": "製造業"}),
    )
    blocked = gate.block(
        "subsidies_search", {"keyword": "製造"}, "s3",
    )
    assert blocked["action"] == "block"


def test_industry_list_is_remembered(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s-list",
        "jp_corp_profile",
        json.dumps({"basic": {"industry": ["E 製造業"]}}),
    )
    assert gate.block("subsidies_search", {"keyword": "E 製造業"}, "s-list") is None
    assert gate.block("subsidies_search", {"keyword": "製造業"}, "s-list") is None
    blocked = gate.block("subsidies_search", {"keyword": "トヨタ自動車"}, "s-list")
    assert blocked["action"] == "block"
    assert "was not returned" not in blocked["message"]
    assert "Industry text was returned" in blocked["message"]
    assert "not_claimed" in blocked["message"]


def test_company_name_does_not_pass_when_industry_is_inside_it(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s-auto",
        "jp_corp_profile",
        json.dumps({"industry": "自動車"}),
    )
    blocked = gate.block("subsidies_search", {"keyword": "トヨタ自動車"}, "s-auto")
    assert blocked["action"] == "block"
    assert "was not returned" not in blocked["message"]
    assert "contained in the company name" in blocked["message"]


def test_company_name_inside_business_summary_does_not_pass(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s-sum",
        "jp_corp_profile",
        json.dumps({
            "basic": {
                "name": "トヨタ自動車株式会社",
                "business_summary": "トヨタ自動車は自動車を製造する。",
                "industry": ["E 製造業"],
            },
        }),
    )
    assert gate.block("subsidies_search", {"keyword": "製造業"}, "s-sum") is None
    blocked = gate.block("subsidies_search", {"keyword": "トヨタ自動車"}, "s-sum")
    assert blocked["action"] == "block"
    assert "was not returned" not in blocked["message"]
    assert "not_claimed" in blocked["message"]
    gate.clear()
    gate.remember(
        "s-sum2",
        "jp_corp_profile",
        json.dumps({
            "name": "トヨタ自動車株式会社",
            "business_summary": "トヨタ自動車は自動車を製造する。",
        }),
    )
    blocked = gate.block("subsidies_search", {"keyword": "トヨタ自動車"}, "s-sum2")
    assert blocked["action"] == "block"
    assert gate.block("subsidies_search", {"keyword": "自動車"}, "s-sum2") is None


def test_company_name_in_summary_without_a_name_field_does_not_pass(plugin):
    gate = _gate(plugin)
    gate.clear()
    gate.remember(
        "s-noname",
        "jp_corp_profile",
        json.dumps({
            "business_summary": "トヨタ自動車は自動車を製造する。",
        }),
    )
    blocked = gate.block("subsidies_search", {"keyword": "トヨタ自動車"}, "s-noname")
    assert blocked["action"] == "block"
    assert "was not returned" not in blocked["message"]
    assert "not_claimed" in blocked["message"]
    assert gate.block("subsidies_search", {"keyword": "自動車"}, "s-noname") is None


def test_remember_hook_returns_none(plugin):
    assert plugin._remember_industry(
        tool_name="jp_corp_profile",
        result=json.dumps({"industry": "製造業"}),
        session_id="s4",
    ) is None
    assert _gate(plugin).block(
        "subsidies_search", {"keyword": "製造"}, "s4",
    ) is None
