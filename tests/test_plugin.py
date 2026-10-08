"""Offline tests for jp-subsidies (no live jGrants calls)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

PLUGIN_DIR = Path(__file__).resolve().parent.parent
TOOL_NAMES = ["subsidies_search", "subsidies_detail", "subsidies_watch_setup"]


def _load(handler, args):
    return json.loads(handler(args))


def test_register_wires_tools_and_cli(plugin):
    registered = {}
    cli = {}

    class Ctx:
        def register_tool(self, name, toolset, schema, handler, **kwargs):
            registered[name] = (toolset, schema, handler)

        def register_cli_command(self, **kwargs):
            cli["cli"] = kwargs

        def register_command(self, *args, **kwargs):
            cli["slash"] = (args, kwargs)

        def get_config(self, key):
            return None

    plugin.register(Ctx())
    assert list(registered) == TOOL_NAMES
    for name, (toolset, schema, handler) in registered.items():
        assert toolset == "subsidies"
        assert schema["name"] == name
        assert callable(handler)
    assert cli["cli"]["name"] == "jp-subsidies"


def test_manifest_matches_registration(plugin):
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    registered = []

    class Ctx:
        def register_tool(self, name, toolset, schema, handler, **kwargs):
            registered.append(name)

        def register_cli_command(self, **kwargs):
            pass

        def register_command(self, *a, **k):
            pass

        def get_config(self, key):
            return None

    plugin.register(Ctx())
    assert manifest["name"] == "jp-subsidies"
    assert manifest["manifest_version"] == 2
    assert manifest["requires_hermes"] == ">=0.21.4"
    assert manifest["provides_tools"] == registered
    assert "requires_env" not in manifest


def test_search_maps_list_fields_and_nulls_missing(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["count_returned"] == 3
    assert out["source"]["citation"] == "出典：J グランツ"
    assert "processed_note" in out["source"]
    assert "保証されたものではありません" in out["source"]["processed_note"]
    row = out["subsidies"][0]
    assert row["title"]
    assert row["subsidy_rate"] is None
    assert row["front_subsidy_detail_page_url"] is None
    assert "subsidy_rate" in row["missing_from_list_api"]
    if row["subsidy_max_limit"] == 0:
        assert "unset" in row["subsidy_max_limit_note"].lower() or "unknown" in row["subsidy_max_limit_note"].lower()
    assert fake_api.calls[0][0] == "/subsidies"
    assert fake_api.calls[0][1]["keyword"] == "人材"
    assert fake_api.calls[0][1]["acceptance"] == "1"


def test_search_rejects_short_keyword(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人"})
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_search_rejects_numeric_employees(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "target_number_of_employees": "20"},
    )
    assert out["kind"] == "bad_request"
    assert "20名以下" in out["next_step"]
    assert fake_api.calls == []


def test_search_deadline_filter_client_side(plugin, fake_api):
    # Fixture has 2027-05-31 and two 2026-10-30 ends; keep only October 2026.
    out = _load(
        plugin.tools.subsidies_search,
        {
            "keyword": "人材",
            "deadline_from": "2026-10-01",
            "deadline_to": "2026-10-30",  # date-only must keep that UTC day
        },
    )
    assert out["deadline_filter"]["applied_client_side"] is True
    assert out["count_after_deadline_filter"] == 2
    for row in out["subsidies"]:
        assert row["acceptance_end_datetime"].startswith("2026-10-30")


def test_search_empty(plugin, fake_api):
    fake_api.search = json.loads(
        (PLUGIN_DIR / "tests/fixtures/search_empty.json").read_text(encoding="utf-8")
    )
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["count_returned"] == 0
    assert out["subsidies"] == []


def test_search_bad_request_body(plugin, fake_api):
    fake_api.raise_on_search = plugin.errors.SubsidiesError(
        "jGrants returned an error body: Bad request",
        kind="bad_request",
        status=400,
        next_step="Check the search parameters and retry.",
    )
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["kind"] == "bad_request"


def test_detail_strips_base64_and_lists_names(plugin, fake_api):
    out = _load(plugin.tools.subsidies_detail, {"subsidy_id": "a0WfQ000003MWE2UAO"})
    assert out["subsidy_rate"]
    assert out["front_subsidy_detail_page_url"].startswith("https://www.jgrants-portal.go.jp/")
    assert "outline_of_grant" in out["attachment_names"]
    assert out["attachment_names"]["outline_of_grant"]
    assert out.get("attachments_saved") is None
    if out["subsidy_max_limit"] == 0:
        assert "unset" in out["subsidy_max_limit_note"].lower() or "unknown" in out[
            "subsidy_max_limit_note"
        ].lower()
    blob = json.dumps(out)
    assert "JVBERi" not in blob
    assert "data" not in json.dumps(out.get("attachment_names"))


def test_detail_missing_id(plugin, fake_api):
    fake_api.fail_detail_with = json.loads(
        (PLUGIN_DIR / "tests/fixtures/detail_empty.json").read_text(encoding="utf-8")
    )
    out = _load(plugin.tools.subsidies_detail, {"subsidy_id": "DOES_NOT_EXIST"})
    # sanitize allows this id shape; empty result → not_found
    assert out["kind"] == "not_found"


def test_detail_rejects_unsafe_id(plugin, fake_api):
    out = _load(plugin.tools.subsidies_detail, {"subsidy_id": "../etc/passwd"})
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_detail_saves_attachments(plugin, fake_api, _isolate_hermes_home):
    out = _load(
        plugin.tools.subsidies_detail,
        {"subsidy_id": "a0WfQ000003MWE2UAO", "save_attachments": True},
    )
    saved = out["attachments_saved"]
    assert saved
    paths = [item["path"] for group in saved.values() for item in group if item.get("saved")]
    assert paths
    for path in paths:
        assert Path(path).is_file()
        assert "plugin-data/jp-subsidies" in path.replace("\\", "/")
        assert str(_isolate_hermes_home) in path


def test_write_guard_denies(plugin, fake_api, monkeypatch, _isolate_hermes_home):
    import sys

    fake_fs = sys.modules["agent.file_safety"]
    fake_fs.get_write_denied_error = lambda path, **kwargs: "denied for test"
    before = {path for path in _isolate_hermes_home.rglob("*")}
    out = _load(
        plugin.tools.subsidies_detail,
        {"subsidy_id": "a0WfQ000003MWE2UAO", "save_attachments": True},
    )
    assert out["kind"] == "access_denied"
    assert {path for path in _isolate_hermes_home.rglob("*")} == before
    assert not (_isolate_hermes_home / "plugin-data" / "jp-subsidies").exists()


def test_watch_baseline_then_new(plugin, fake_api, _isolate_hermes_home):
    setup = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "demo", "deliver": "local"},
    )
    assert setup["baseline"] is True
    assert "First run: saved" in setup["message"]
    assert "基準として" in setup["message"]
    assert "cron_command" in setup
    assert "--no-agent" in setup["cron_command"]
    assert "plugin-data/jp-subsidies" in setup["state_path"].replace("\\", "/")
    assert Path(setup["state_path"]).is_file()
    # processed_note at end of weekly text (after English body)
    assert setup["message"].index("jp-subsidies weekly check") < setup["message"].index(
        "保証されたものではありません"
    )

    # Second run with an extra id → new
    extra = {
        "acceptance_end_datetime": "2026-10-20T14:59:00.000Z",
        "acceptance_start_datetime": "2026-09-01T00:00:00.000Z",
        "id": "a0WNEW000000000001",
        "institution_name": None,
        "name": "S-NEW",
        "subsidy_max_limit": 1,
        "target_area_search": "東京都",
        "target_number_of_employees": "20名以下",
        "title": "新規テスト補助金",
    }
    body = json.loads(json.dumps(fake_api.search))
    body["result"] = [extra] + list(body["result"])
    body["metadata"]["resultset"]["count"] = len(body["result"])
    fake_api.search = body

    result = plugin.watch.run("demo", now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert result["baseline"] is False
    assert result["new_count"] == 1
    assert "新規テスト補助金" in result["message"]
    assert "出典：J グランツ" in result["message"]
    assert result["message"].startswith("jp-subsidies weekly check")


def test_watch_failure_does_not_clear_baseline(plugin, fake_api):
    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "keep"})
    state_before = plugin.watch.load_state("keep")
    seen_before = list(state_before["seen_ids"])
    fake_api.raise_on_search = plugin.errors.SubsidiesError(
        "network down", kind="network_error"
    )
    with pytest.raises(plugin.errors.SubsidiesError) as exc:
        plugin.watch.run("keep")
    assert exc.value.kind == "network_error"
    state_after = plugin.watch.load_state("keep")
    assert state_after["seen_ids"] == seen_before
    assert state_after["last_error"]["kind"] == "network_error"


def test_search_limit_clamped_by_config(plugin, fake_api, monkeypatch):
    plugin.settings.bind_config(lambda key: 2 if key == "search_limit" else None)
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材", "limit": 99})
    assert out["count_returned"] == 2
    plugin.settings.bind_config(None)


def test_no_banned_words_in_tracked_texts():
    banned = ["Unofficial", "unofficial", "非公式", "not affiliated", "Not affiliated"]
    texts = []
    for path in PLUGIN_DIR.rglob("*"):
        if path.is_file() and "tests/" not in str(path) and path.suffix in {
            ".py",
            ".md",
            ".yaml",
            ".yml",
            ".txt",
            "",
        }:
            if path.name.startswith("."):
                continue
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    blob = "\n".join(texts)
    for word in banned:
        assert word not in blob, word


def test_credit_constant(plugin):
    assert plugin.citation.CREDIT == "出典：J グランツ"


def test_cron_entry_uses_sixty_second_budget(plugin, monkeypatch):
    seen = {}

    def fake_run(profile, **kwargs):
        seen["deadline"] = plugin.client._deadline.get()
        return {"message": "ok"}

    monkeypatch.setattr(plugin.cron_entry, "run", fake_run)
    assert plugin.cron_entry.main("default") == 0
    assert seen["deadline"] is not None


def test_cron_entry_success_and_failure(plugin, fake_api, capsys):
    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "cron"})
    assert plugin.cron_entry.main("cron") == 0
    captured = capsys.readouterr().out
    assert "出典：J グランツ" in captured

    fake_api.raise_on_search = plugin.errors.SubsidiesError("boom", kind="network_error")
    assert plugin.cron_entry.main("cron") == 1
    err = capsys.readouterr().out
    assert err.startswith("jp-subsidies weekly check failed")


def test_schedule_uses_write_guard(plugin, fake_api, monkeypatch, _isolate_hermes_home):
    import sys

    sys.modules["agent.file_safety"].get_write_denied_error = lambda path, **kwargs: "denied"
    before = {path for path in _isolate_hermes_home.rglob("*")}
    out = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "deny"},
    )
    assert out["kind"] == "access_denied"
    # Script path is checked before baseline setup, so no orphan state or data dir.
    assert plugin.watch.load_state("deny") is None
    assert {path for path in _isolate_hermes_home.rglob("*")} == before
    assert not (_isolate_hermes_home / "plugin-data").exists()
    assert not (_isolate_hermes_home / "scripts").exists()
    assert fake_api.calls == []


def test_watch_line_marks_zero_limit_and_jst(plugin):
    line = plugin.watch._line(
        {
            "title": "t",
            "id": "x",
            "acceptance_end_datetime": "2026-10-16T03:00:00.000Z",
            "target_area_search": "東京都",
            "subsidy_max_limit": 0,
        }
    )
    assert "unset/unknown" in line
    assert "JST" in line
    assert "2026-10-16 12:00 JST" in line  # 03:00Z → 12:00 JST


def test_schedule_script_loads_package(plugin, fake_api, _isolate_hermes_home):
    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "pkg"})
    script = plugin.schedule.write_script(PLUGIN_DIR, "pkg")
    text = script.read_text(encoding="utf-8")
    assert "importlib.util" in text
    assert "jp_subsidies_runtime" in text
    assert "cron_entry" in text
    assert "pkg" in text


def test_rejects_wrong_industry_before_http(plugin, fake_api):
    # Wrong 「医療・福祉」 is refused before HTTP, so it is not shown as no matches.
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "industry": "医療・福祉"},
    )
    assert out["kind"] == "bad_request"
    assert "医療、福祉" in out["next_step"]
    assert fake_api.calls == []


def test_rejects_unknown_use_purpose_before_http(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "use_purpose": "宇宙旅行をしたい"},
    )
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_setup_rerun_keeps_seen_without_absorbing_current(plugin, fake_api):
    first = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "merge"})
    assert first["baseline"] is True
    # Prior seen includes an old id; current list gains a new unnotified id.
    state = plugin.watch.load_state("merge")
    prior = list(state["seen_ids"]) + ["a0WOLD000000000001"]
    state["seen_ids"] = prior
    plugin.watch.write_state("merge", state)

    body = json.loads(json.dumps(fake_api.search))
    body["result"].append(
        {
            "id": "a0WNEW000000000001",
            "title": "未通知の新着",
            "name": "未通知の新着",
            "subsidy_max_limit": 0,
            "acceptance_end_datetime": "2026-12-31T14:59:59.000Z",
        }
    )
    body["metadata"]["resultset"]["count"] = len(body["result"])
    fake_api.search = body

    second = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "merge"})
    assert second["re_setup"] is True
    assert second["baseline"] is False
    assert second["pending_new_count"] == 1
    assert "Re-setup" in second["message"]
    assert "did not absorb" in second["message"]
    assert "First run" not in second["message"]
    tracked = plugin.watch.load_state("merge")["seen_ids"]
    assert "a0WOLD000000000001" in tracked
    assert "a0WNEW000000000001" not in tracked  # not silenced by re-setup
    assert any(i.startswith("a0Wf") for i in tracked)

    check = plugin.watch.run("merge", now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert check["new_count"] == 1
    assert "a0WNEW000000000001" in check["message"]


def test_metadata_count_mismatch_fails(plugin, fake_api):
    body = json.loads(json.dumps(fake_api.search))
    body["metadata"]["resultset"]["count"] = 99  # rows are 3
    fake_api.search = body
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["kind"] == "bad_response"
    assert "does not match" in out["error"]


def test_metadata_count_missing_fails(plugin, fake_api):
    body = json.loads(json.dumps(fake_api.search))
    del body["metadata"]["resultset"]["count"]
    fake_api.search = body
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["kind"] == "bad_response"
    assert "missing metadata.resultset.count" in out["error"]


def test_plugin_root_refuses_without_plugin_data_dir(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setitem(sys.modules, "plugins", None)
    monkeypatch.setitem(sys.modules, "plugins.plugin_storage", None)
    with pytest.raises(plugin.errors.SubsidiesError) as exc:
        plugin.save.plugin_root()
    assert exc.value.kind == "access_denied"
    assert not (_isolate_hermes_home / "plugin-data").exists()


def test_count_drop_warning_in_watch(plugin, fake_api):
    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "drop"})
    prior_count = plugin.watch.load_state("drop")["last_count"]
    assert prior_count > 0
    fake_api.search = json.loads(
        (PLUGIN_DIR / "tests/fixtures/search_empty.json").read_text(encoding="utf-8")
    )
    result = plugin.watch.run("drop", now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert "dropped from" in result["message"]
    assert "to 0" in result["message"]
    assert "unverified" in result["message"]
    assert "New since last success: 0" not in result["message"]
    assert "Deadlines within 14 day(s): 0" not in result["message"]
    assert "not proof that no deadline" in result["message"]
    assert result["count_unverified"] is True
    # last_count kept so the warning repeats next week
    assert plugin.watch.load_state("drop")["last_count"] == prior_count
    again = plugin.watch.run("drop", now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert "dropped from" in again["message"]
    assert "unverified" in again["message"]


def test_setup_criteria_change_retakes_baseline(plugin, fake_api):
    first = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "target_area_search": "東京都", "profile": "chg"},
    )
    assert first["baseline"] is True
    old_seen = set(plugin.watch.load_state("chg")["seen_ids"])
    assert old_seen

    # Different keyword → new baseline; must not leave old seen and flood later.
    second = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "設備", "target_area_search": "全国", "profile": "chg"},
    )
    assert second["criteria_changed"] is True
    assert second["baseline"] is True
    assert second["re_setup"] is False
    assert "Criteria changed" in second["message"]
    assert "First run" not in second["message"]
    new_seen = set(plugin.watch.load_state("chg")["seen_ids"])
    assert new_seen == {str(it["id"]) for it in fake_api.search["result"] if it.get("id")}
    check = plugin.watch.run("chg", now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert check["new_count"] == 0
    assert "New since last success: 0" in check["message"] or "New since last success: 0\n" in check["message"]


def test_area_overseas_accepted(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "target_area_search": "海外"},
    )
    assert "error" not in out
    assert any(c[1].get("target_area_search") == "海外" for c in fake_api.calls)


def test_scripts_dir_matches_plugin_home(plugin, _isolate_hermes_home):
    root = plugin.save.plugin_root()
    scripts = plugin.save.scripts_dir()
    assert scripts == root.parent.parent / "scripts"
    assert scripts == _isolate_hermes_home / "scripts"


def test_bad_deadline_rejects_before_http(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "deadline_to": "not-a-date"},
    )
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_default_deliver_is_origin(plugin, fake_api):
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "del"})
    assert out["deliver"] == "origin"
    assert "--deliver origin" in out["cron_command"] or " origin" in out["cron_command"]


def test_detail_always_has_processed_note(plugin, fake_api):
    out = _load(plugin.tools.subsidies_detail, {"subsidy_id": "a0WfQ000003MWE2UAO"})
    assert "processed_note" in out["source"]


def test_register_slash_command(plugin):
    slash = {}

    class Ctx:
        def register_tool(self, *a, **k):
            pass

        def register_cli_command(self, **k):
            pass

        def register_command(self, name, handler, **kwargs):
            slash["name"] = name
            slash["handler"] = handler
            slash["description"] = kwargs.get("description", "")

        def get_config(self, key):
            return None

    plugin.register(Ctx())
    assert slash["name"] == "subsidies"
    assert slash["description"] == "Read-only watch check"
    assert "update the watch state" not in slash["description"]
    assert asyncio.iscoroutinefunction(slash["handler"])


def test_notice_has_digital_agency_2025_copyright():
    notice = (PLUGIN_DIR / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright (c) 2025 Digital Agency, Government of Japan" in notice


def test_readme_does_not_claim_missing_catalog_peers():
    # Catalog peers are documented in ship/pr_body (outside this repo). README must
    # not invent catalog facts; keep a light check that we do not say "jp-edinet".
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    assert "jp-edinet" not in readme


def test_sort_error_has_next_step(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材", "sort": "nope"})
    assert out["kind"] == "bad_request"
    assert out.get("next_step")
    assert fake_api.calls == []


def test_bad_acceptance_rejected(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材", "acceptance": 2})
    assert out["kind"] == "bad_request"
    assert "acceptance" in out["error"]
    assert out.get("next_step")
    assert fake_api.calls == []


def test_japanese_profiles_do_not_collapse(plugin, fake_api):
    tokyo = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "東京"})
    osaka = _load(plugin.tools.subsidies_watch_setup, {"keyword": "設備", "profile": "大阪"})
    assert tokyo["kind"] == "bad_request"
    assert osaka["kind"] == "bad_request"
    assert plugin.watch.load_state("default") is None
    assert fake_api.calls == []


def test_same_criteria_empty_list_keeps_seen_and_does_not_say_baseline_empty(plugin, fake_api):
    first = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "emptyre"})
    assert first["baseline"] is True
    prior = list(plugin.watch.load_state("emptyre")["seen_ids"])
    first_count = plugin.watch.load_state("emptyre")["last_count"]
    assert first_count > 0
    assert prior
    fake_api.search = json.loads(
        (PLUGIN_DIR / "tests/fixtures/search_empty.json").read_text(encoding="utf-8")
    )
    second = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "emptyre"})
    assert second["re_setup"] is True
    assert "baseline is empty" not in second["message"]
    assert "every id will look new" not in second["message"]
    assert "were kept" in second["message"]
    assert "not an empty baseline" in second["message"]
    assert "dropped from" in second["message"]
    assert "New since last success: unverified" in second["message"]
    assert "New ids stay unverified" in second["message"]
    assert "will be reported as new" not in second["message"]
    assert "次回の check で報告" not in second["message"]
    assert plugin.watch.load_state("emptyre")["seen_ids"] == prior
    assert plugin.watch.load_state("emptyre")["last_count"] == first_count
    weekly = plugin.watch.run("emptyre", now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert "Deadlines within 14 day(s): 0" not in weekly["message"]
    assert "unverified" in weekly["message"]


def test_empty_seen_re_setup_does_not_say_baseline_is_not_empty(plugin, fake_api):
    fake_api.search = json.loads(
        (PLUGIN_DIR / "tests/fixtures/search_empty.json").read_text(encoding="utf-8")
    )
    first = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "emptyseen"})
    assert first["baseline"] is True
    second = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "emptyseen"})
    assert second["re_setup"] is True
    assert "not an empty baseline" not in second["message"]
    assert "基準が空になったわけではありません" not in second["message"]
    assert "The baseline is empty" in second["message"]


def test_cron_uses_deadline_days_saved_at_setup(plugin, fake_api):
    plugin.settings.bind_config(lambda key: 30 if key == "watch_deadline_days" else None)
    try:
        _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "days21", "deadline_days": 21},
        )
        assert plugin.watch.load_state("days21")["deadline_days"] == 21
        plugin.settings.bind_config(None)
        result = plugin.watch.run("days21", now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    finally:
        plugin.settings.bind_config(None)
    assert "21 day" in result["message"]
    assert plugin.watch.load_state("days21")["deadline_days"] == 21


def test_deliver_rejects_schedule_words(plugin, fake_api):
    for deliver in ("monday", "in 5m", "at 9am", "@daily", "5", "*", "9am"):
        out = _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "dallow", "deliver": deliver},
        )
        assert out["kind"] == "bad_request", deliver
        assert fake_api.calls == []
    ok = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "dallow2", "deliver": "telegram:42"},
    )
    assert ok.get("kind") != "bad_request"
    assert "telegram:42" in ok["cron_command"]


def test_first_empty_list_warns_baseline_empty(plugin, fake_api):
    fake_api.search = json.loads(
        (PLUGIN_DIR / "tests/fixtures/search_empty.json").read_text(encoding="utf-8")
    )
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "emptyfirst"})
    assert out["baseline"] is True
    assert "baseline is empty" in out["message"]


def test_deadline_days_zero_is_rejected(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "days0", "deadline_days": 0},
    )
    assert out["kind"] == "bad_request"
    assert "not changed to 1" in out["error"]
    assert plugin.watch.load_state("days0") is None
    assert fake_api.calls == []


def test_limit_zero_is_rejected(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材", "limit": 0})
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_keyword_spaces_are_sent(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材 育成"})
    assert "error" not in out
    assert fake_api.calls[0][1]["keyword"] == "人材 育成"


def test_schedule_period_is_not_a_deliver_target(plugin, fake_api):
    for deliver in ("every", "5m", "every 2h", "0 9 * * 1"):
        out = _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "period", "deliver": deliver},
        )
        assert out["kind"] == "bad_request", deliver
        assert fake_api.calls == []
    swapped = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "period", "when": "origin"},
    )
    assert swapped["kind"] == "bad_request"
    assert plugin.watch.load_state("period") is None


def test_slash_refuses_schedule_words(plugin):
    text = asyncio.run(plugin.cli.slash("every 5m"))
    assert text.startswith("✗")
    assert "delivery target" in text
    for raw in ("origin", "local", "check origin", "telegram:1"):
        refused = asyncio.run(plugin.cli.slash(raw))
        assert refused.startswith("✗"), raw
        assert "delivery target" in refused or "does not take a delivery target" in refused
    for raw in ("check 5m", "check every"):
        refused = asyncio.run(plugin.cli.slash(raw))
        assert refused.startswith("✗"), raw
        assert "No watch profile" not in refused
        assert "refused as periods" in refused
    check = asyncio.run(plugin.cli.slash("check demo"))
    assert "No watch profile" in check or check.startswith("✗")


def test_manual_check_does_not_consume_weekly_new_items(plugin, fake_api):
    import copy

    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "guest"})
    before = plugin.watch.load_state("guest")
    seen = list(before["seen_ids"])
    count = before["last_count"]
    body = copy.deepcopy(fake_api.search)
    extra = dict(body["result"][0])
    extra["id"] = "a0WNEW000000000001"
    extra["title"] = "guest-visible new row"
    body["result"].append(extra)
    body["metadata"]["resultset"]["count"] = len(body["result"])
    fake_api.search = body
    text = asyncio.run(plugin.cli.slash("check guest"))
    assert "a0WNEW000000000001" in text
    assert plugin.watch.load_state("guest")["seen_ids"] == seen
    assert plugin.watch.load_state("guest")["last_count"] == count

    class Args:
        subsidies_command = "check"
        watch = "guest"

    assert plugin.cli.handle(Args()) == 0
    assert plugin.watch.load_state("guest")["seen_ids"] == seen
    weekly = plugin.watch.run("guest", now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert "a0WNEW000000000001" in weekly["message"]
    assert "a0WNEW000000000001" in plugin.watch.load_state("guest")["seen_ids"]


def test_undeliverable_platform_is_refused_before_write(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin.schedule,
        "_core_platform_names",
        lambda: set(plugin.schedule._FALLBACK_PLATFORMS),
    )
    monkeypatch.setattr(
        plugin.schedule,
        "_extra_platform_names",
        lambda: {"homeassistant", "whatsapp_cloud", "chatwork"},
    )
    for name in ("homeassistant", "whatsapp_cloud", "cli", "cron", "api_server"):
        with pytest.raises(plugin.errors.SubsidiesError):
            plugin.schedule.require_deliver(name)
    assert plugin.schedule.require_deliver("chatwork:1") == "chatwork:1"
    assert plugin.schedule.require_deliver("telegram") == "telegram"
    monkeypatch.setattr(
        plugin.schedule,
        "_core_platform_names",
        lambda: {"homeassistant", "telegram"},
    )
    assert plugin.schedule.require_deliver("homeassistant") == "homeassistant"
    with pytest.raises(plugin.errors.SubsidiesError):
        plugin.schedule.require_deliver("whatsapp_cloud")


def test_deliver_allows_composite_and_loaded_plugin(plugin, monkeypatch):
    assert plugin.schedule.require_deliver("origin,all") == "origin,all"
    assert plugin.schedule.require_deliver("bot-chat:desk") == "bot-chat:desk"
    monkeypatch.setattr(plugin.schedule, "_local_profile_exists", lambda name: False)
    with pytest.raises(plugin.errors.SubsidiesError) as missing:
        plugin.schedule.require_reachable_bot_chat("bot-chat:desk")
    assert "not a chat id" in str(missing.value)
    assert not plugin.watch.state_path("default").exists()
    refused = json.loads(
        plugin.tools.subsidies_watch_setup({"keyword": "人材", "deliver": "bot-chat:desk"})
    )
    assert refused["kind"] == "bad_request"
    assert "not a chat id" in refused["error"]
    assert not plugin.watch.state_path("default").exists()
    plugin.schedule.require_reachable_bot_chat("bot-chat")
    plugin.schedule.require_reachable_bot_chat("origin,local")
    monkeypatch.setattr(plugin.schedule, "_local_profile_exists", lambda name: True)
    plugin.schedule.require_reachable_bot_chat("bot-chat:desk")
    monkeypatch.setattr(plugin.schedule, "_extra_platform_names", lambda: {"chatwork"})
    assert plugin.schedule.require_deliver("chatwork:42") == "chatwork:42"
    monkeypatch.setattr(plugin.schedule, "_extra_platform_names", lambda: set())
    for deliver in ("monday", "5", "*", "origin,monday", "chatwork:42"):
        with pytest.raises(plugin.errors.SubsidiesError) as caught:
            plugin.schedule.require_deliver(deliver)
        assert "bot-chat" in str(caught.value)
        assert "all" in str(caught.value)


def test_unknown_cli_command_exits_1(plugin):
    class Args:
        subsidies_command = "nope"

    assert plugin.cli.handle(Args()) == 1


def test_attachment_name_collision_writes_nothing(plugin):
    import base64

    detail = {
        "application_guidelines": [
            {"name": "a b.pdf", "data": base64.b64encode(b"one").decode()},
            {"name": "a_b.pdf", "data": base64.b64encode(b"two").decode()},
        ]
    }
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments("a0W0000000000001", detail)
    assert "collides" in str(caught.value)
    assert "Both files" not in (caught.value.next_step or "")
    root = plugin.save.plugin_root() / "files" / "a0W0000000000001"
    assert not root.exists()


def test_attachment_case_collision_writes_nothing(plugin):
    import base64

    detail = {
        "application_guidelines": [
            {"name": "File.PDF", "data": base64.b64encode(b"AAA").decode()},
            {"name": "file.pdf", "data": base64.b64encode(b"BBB").decode()},
        ]
    }
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments("a0W0000000000002", detail)
    assert "collides" in str(caught.value)
    assert "letter case" in str(caught.value)
    assert "2 attachment names collide" in (caught.value.next_step or "")
    assert "Both files" not in (caught.value.next_step or "")
    root = plugin.save.plugin_root() / "files" / "a0W0000000000002"
    assert not root.exists()


def test_attachment_case_does_not_overwrite_existing(plugin):
    import base64

    plugin.save.save_attachments(
        "a0W0000000000004",
        {
            "application_guidelines": [
                {"name": "file.pdf", "data": base64.b64encode(b"AAA").decode()},
            ]
        },
    )
    path = plugin.save.plugin_root() / "files" / "a0W0000000000004" / "file.pdf"
    assert path.read_bytes() == b"AAA"
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments(
            "a0W0000000000004",
            {
                "application_guidelines": [
                    {"name": "File.PDF", "data": base64.b64encode(b"BBB").decode()},
                ]
            },
        )
    assert "collides" in str(caught.value)
    assert "Both files" not in (caught.value.next_step or "")
    assert path.read_bytes() == b"AAA"
    assert [item.name for item in path.parent.iterdir()] == ["file.pdf"]


def test_attachment_nfc_nfd_collision_writes_nothing(plugin):
    import base64
    import unicodedata

    nfc = unicodedata.normalize("NFC", "が.pdf")
    nfd = unicodedata.normalize("NFD", "が.pdf")
    assert nfc != nfd
    detail = {
        "application_guidelines": [
            {"name": nfc, "data": base64.b64encode(b"AAA").decode()},
            {"name": nfd, "data": base64.b64encode(b"BBB").decode()},
        ]
    }
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments("a0W0000000000005", detail)
    assert "NFC" in str(caught.value)
    assert "NFD" in str(caught.value)
    assert "2 attachment names collide" in (caught.value.next_step or "")
    assert "Both files" not in (caught.value.next_step or "")
    root = plugin.save.plugin_root() / "files" / "a0W0000000000005"
    assert not root.exists()


def test_attachment_nfd_does_not_overwrite_existing(plugin):
    import base64
    import unicodedata

    nfc = unicodedata.normalize("NFC", "が.pdf")
    nfd = unicodedata.normalize("NFD", "が.pdf")
    plugin.save.save_attachments(
        "a0W0000000000006",
        {
            "application_guidelines": [
                {"name": nfd, "data": base64.b64encode(b"AAA").decode()},
            ]
        },
    )
    root = plugin.save.plugin_root() / "files" / "a0W0000000000006"
    files = [item for item in root.iterdir() if item.is_file()]
    assert len(files) == 1
    assert files[0].read_bytes() == b"AAA"
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments(
            "a0W0000000000006",
            {
                "application_guidelines": [
                    {"name": nfc, "data": base64.b64encode(b"BBB").decode()},
                ]
            },
        )
    assert "NFC" in str(caught.value)
    assert "letter case" in str(caught.value)
    assert files[0].read_bytes() == b"AAA"
    assert len([item for item in root.iterdir() if item.is_file()]) == 1


def test_profile_case_does_not_overwrite_state_or_script(plugin):
    plugin.watch.write_state("Tokyo", {"keyword": "AAA"})
    plugin.watch.write_state("Tokyo", {"keyword": "CCC"})
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.watch.write_state("tokyo", {"keyword": "BBB"})
    assert "letter case" in str(caught.value)
    assert "Nothing new was written" in (caught.value.next_step or "")
    with pytest.raises(plugin.errors.SubsidiesError):
        plugin.watch.load_state("tokyo")
    assert plugin.watch.load_state("Tokyo")["keyword"] == "CCC"
    watch_dir = plugin.save.plugin_root() / "watch"
    json_files = [item for item in watch_dir.iterdir() if item.suffix == ".json"]
    assert [item.name for item in json_files] == ["Tokyo.json"]
    assert "BBB" not in json_files[0].read_text(encoding="utf-8")

    script = plugin.schedule.write_script(PLUGIN_DIR, "Tokyo")
    with pytest.raises(plugin.errors.SubsidiesError) as script_caught:
        plugin.schedule.write_script(PLUGIN_DIR, "tokyo")
    assert "letter case" in str(script_caught.value)
    scripts = [
        item
        for item in script.parent.iterdir()
        if item.name.startswith("jp-subsidies-weekly-")
    ]
    assert [item.name for item in scripts] == ["jp-subsidies-weekly-Tokyo.py"]
    assert "Tokyo" in script.read_text(encoding="utf-8")


def test_script_only_case_does_not_write_state_or_block_original(plugin):
    script = plugin.schedule.write_script(PLUGIN_DIR, "Tokyo")
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.watch.setup({"keyword": "人材", "profile": "tokyo"})
    assert "Cron script" in str(caught.value)
    assert "letter case" in str(caught.value)
    watch_dir = plugin.save.plugin_root() / "watch"
    if watch_dir.exists():
        assert [item.name for item in watch_dir.iterdir() if item.suffix == ".json"] == []
    assert plugin.watch.load_state("Tokyo") is None
    assert script.is_file()
    assert "Tokyo" in script.read_text(encoding="utf-8")


def test_failed_script_write_removes_state_created_this_call(plugin, fake_api, monkeypatch):
    def boom(*_args, **_kwargs):
        raise plugin.errors.SubsidiesError(
            "script refused",
            kind="bad_request",
            next_step="Retry the schedule.",
        )

    monkeypatch.setattr(plugin.schedule, "write_script", boom)
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "freshcase"})
    assert out["kind"] == "bad_request"
    assert plugin.watch.load_state("freshcase") is None


def test_subsidy_id_case_does_not_share_directory(plugin):
    import base64

    plugin.save.save_attachments(
        "abcDEF",
        {
            "application_guidelines": [
                {"name": "a.pdf", "data": base64.b64encode(b"AAA").decode()},
            ]
        },
    )
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments(
            "ABCdef",
            {
                "application_guidelines": [
                    {"name": "b.pdf", "data": base64.b64encode(b"BBB").decode()},
                ]
            },
        )
    assert "directory" in str(caught.value)
    assert "letter case" in str(caught.value)
    parent = plugin.save.plugin_root() / "files"
    dirs = [item for item in parent.iterdir() if item.is_dir()]
    assert [item.name for item in dirs] == ["abcDEF"]
    files = [item for item in dirs[0].iterdir() if item.is_file()]
    assert [item.name for item in files] == ["a.pdf"]
    assert files[0].read_bytes() == b"AAA"


def test_attachment_three_name_collision_counts_three(plugin):
    import base64

    detail = {
        "application_guidelines": [
            {"name": "File.PDF", "data": base64.b64encode(b"AAA").decode()},
            {"name": "file.pdf", "data": base64.b64encode(b"BBB").decode()},
            {"name": "FILE.pdf", "data": base64.b64encode(b"CCC").decode()},
        ]
    }
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.save_attachments("a0W0000000000003", detail)
    assert "3 attachment names collide" in (caught.value.next_step or "")
    assert "Both files" not in (caught.value.next_step or "")
    root = plugin.save.plugin_root() / "files" / "a0W0000000000003"
    assert not root.exists()


def test_watch_timeout_next_step_keeps_saved_criteria(plugin, fake_api, monkeypatch):
    _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "slow"})

    def boom(*_args, **_kwargs):
        raise plugin.errors.SubsidiesError(
            "jGrants did not answer within 60 seconds.",
            kind="timeout",
            next_step="Retry with a narrower keyword or fewer optional filters.",
        )

    monkeypatch.setattr(plugin.watch, "fetch_list", boom)
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.watch.run("slow")
    assert caught.value.kind == "timeout"
    assert "narrower keyword" not in caught.value.next_step
    assert "saved watch criteria were not changed" in caught.value.next_step


def test_missing_detail_status_is_200(plugin, fake_api):
    fake_api.detail = json.loads(
        (PLUGIN_DIR / "tests/fixtures/detail_empty.json").read_text(encoding="utf-8")
    )
    out = _load(plugin.tools.subsidies_detail, {"subsidy_id": "a0WMISSING0000001"})
    assert out["kind"] == "not_found"
    assert out.get("status") == 200
