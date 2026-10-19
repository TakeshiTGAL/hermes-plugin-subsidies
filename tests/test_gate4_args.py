"""Five argument shapes. No recorded session had an argument mistake."""
import json


def _search(plugin, args):
    return json.loads(plugin.tools.subsidies_search(args))


def test_slash_dates_short_prefecture_case_and_integral_limit(plugin, fake_api):
    out = _search(plugin, {
        "keyword": "人材",
        "target_area_search": "東京",
        "sort": "Created_Date",
        "order": "desc",
        "limit": 10.0,
        "deadline_from": "2026/10/01",
        "deadline_to": "２０２７/０５/３１",
    })
    assert "error" not in out
    assert out["count_returned"] == 3
    sent = fake_api.calls[0][1]
    assert sent["target_area_search"] == "東京都"
    assert sent["sort"] == "created_date"
    assert sent["order"] == "DESC"

    fake_api.calls.clear()
    wide = _search(plugin, {"keyword": "人材", "limit": "１０"})
    assert "error" not in wide
    assert wide["count_returned"] == 3


def test_extra_argument_hides_the_key(plugin, fake_api):
    out = _search(plugin, {"keyword": "設備", "page": 2})
    text = json.dumps(out, ensure_ascii=False)
    assert out["ok"] is False
    assert out["error_type"] == "bad_request"
    assert "人材" in out["error"]
    assert "設備" not in text
    assert "page" not in text
    assert fake_api.calls == []


def test_limit_outside_the_cap_is_not_reduced(plugin, fake_api):
    for bad in (0, 7.5, 250):
        out = _search(plugin, {"keyword": "人材", "limit": bad})
        text = json.dumps(out, ensure_ascii=False)
        assert out["ok"] is False
        assert out["error_type"] == "bad_request"
        assert out["error"] == plugin.tools._LIMIT
        assert "7.5" not in text
        assert "250" not in text
        assert fake_api.calls == []


def test_impossible_day_hides_the_input(plugin, fake_api):
    out = _search(plugin, {"keyword": "人材", "deadline_from": "2026/13/40"})
    text = json.dumps(out, ensure_ascii=False)
    assert out["ok"] is False
    assert out["error_type"] == "bad_request"
    assert "2026-10-01" in out["error"]
    assert "13" not in text
    assert "40" not in text
    assert fake_api.calls == []


def test_unknown_area_hides_the_input(plugin, fake_api):
    out = _search(plugin, {"keyword": "人材", "target_area_search": "Eurasia"})
    text = json.dumps(out, ensure_ascii=False)
    assert out["ok"] is False
    assert out["error_type"] == "bad_request"
    assert "東京都" in out["error"]
    assert "Eurasia" not in text
    assert fake_api.calls == []
