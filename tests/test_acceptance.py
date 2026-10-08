"""Offline checks named in the frozen acceptance table.

Harness probes (install, slash_loop, cron_manual, redirect_key, argv_flags)
are not started here. These tests cover the same behavior without a socket.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import io
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

PLUGIN_DIR = Path(__file__).resolve().parent.parent
SHIP = Path.home() / "takeshitgal" / "ship" / "jp-subsidies"
FIXTURES = PLUGIN_DIR / "tests" / "fixtures"


def _load(handler, args):
    return json.loads(handler(args))


def _sched(**kwargs):
    base = dict(
        subsidies_command="schedule",
        keyword="設備",
        watch="osaka",
        target_area_search=None,
        industry=None,
        target_number_of_employees=None,
        use_purpose=None,
        deadline_days=None,
        deliver="origin",
        when="0 9 * * 1",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


@pytest.fixture(scope="module")
def public_text():
    catalog = yaml.safe_load((SHIP / "jp-subsidies.yaml").read_text(encoding="utf-8"))
    return {
        "readme": (PLUGIN_DIR / "README.md").read_text(encoding="utf-8"),
        "manifest": (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"),
        "description": str(catalog["description"]),
        "catalog_sha": str(catalog["sha"]),
        "pr": (SHIP / "pr_body.md").read_text(encoding="utf-8"),
        "notice": (PLUGIN_DIR / "NOTICE").read_text(encoding="utf-8"),
    }


def test_keyword_strips_ends_and_keeps_internal_spaces(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": " 人材 育成 "})
    assert "error" not in out
    assert fake_api.calls[0][1]["keyword"] == "人材 育成"
    fake_api.calls.clear()
    for bad in ("人", "a" * 256, "  a  "):
        refused = _load(plugin.tools.subsidies_search, {"keyword": bad})
        assert refused["kind"] == "bad_request", bad
    assert fake_api.calls == []


def test_official_enum_tuples_match_fixture(plugin):
    official = json.loads((FIXTURES / "official_enums.json").read_text(encoding="utf-8"))
    assert list(plugin.enums.EMPLOYEE_VALUES) == official["EMPLOYEE_VALUES"]
    assert list(plugin.enums.INDUSTRY_VALUES) == official["INDUSTRY_VALUES"]
    assert list(plugin.enums.USE_PURPOSE_VALUES) == official["USE_PURPOSE_VALUES"]
    assert list(plugin.enums.AREA_VALUES) == official["AREA_VALUES"]
    assert len(plugin.enums.EMPLOYEE_VALUES) == 8
    assert "海外" in plugin.enums.AREA_VALUES


def test_area_multi_value_rejected_before_http(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "target_area_search": "東京都 / 全国"},
    )
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []
    ok = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "industry": "農業、林業 / 漁業"},
    )
    assert "error" not in ok
    assert fake_api.calls[-1][1]["industry"] == "農業、林業 / 漁業"


def test_acceptance_yes_is_not_coerced(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材", "acceptance": "yes"})
    assert out["kind"] == "bad_request"
    assert fake_api.calls == []


def test_list_nulls_and_zero_limit_note(plugin, fake_api):
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    row = out["subsidies"][0]
    assert row["subsidy_rate"] is None
    assert row["front_subsidy_detail_page_url"] is None
    assert "jgrants-portal" not in json.dumps(row["front_subsidy_detail_page_url"])
    assert row["subsidy_max_limit"] == 0
    assert "unset/unknown" in row["subsidy_max_limit_note"]
    assert "¥0" not in row["subsidy_max_limit_note"] or "not a proven" in row["subsidy_max_limit_note"]
    detail = _load(plugin.tools.subsidies_detail, {"subsidy_id": "a0WfQ000003MWE2UAO"})
    assert detail["subsidy_rate"]
    assert detail["front_subsidy_detail_page_url"].startswith("https://")
    assert "対象経費" not in detail
    assert "申請要件" not in detail
    assert "対象経費" in detail["structured_fields_note"]


def test_date_only_deadline_to_includes_utc_end_and_weekly_shows_jst(plugin, fake_api):
    body = copy.deepcopy(fake_api.search)
    body["result"] = [dict(body["result"][0])]
    body["result"][0]["id"] = "a0WT150000000001"
    body["result"][0]["acceptance_end_datetime"] = "2026-10-07T15:00:00.000Z"
    body["metadata"]["resultset"]["count"] = 1
    fake_api.search = body
    kept = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "deadline_to": "2026-10-07"},
    )
    assert kept["count_api"] == 1
    assert kept["count_returned"] == 1
    assert "deadline" not in kept["query"]
    body["result"][0]["acceptance_end_datetime"] = "2026-10-08T00:00:00.000Z"
    dropped = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "deadline_to": "2026-10-07"},
    )
    assert dropped["count_api"] == 1
    assert dropped["count_returned"] == 0
    assert plugin.watch._format_deadline("2026-10-07T15:00:00.000Z") == "2026-10-08 00:00 JST"


def test_future_deadline_from_keeps_api_count(plugin, fake_api):
    rows = []
    for index in range(32):
        rows.append(
            {
                "id": f"a0W{index:014d}"[:18],
                "title": "old",
                "subsidy_max_limit": 1,
                "acceptance_end_datetime": "2020-01-01T00:00:00.000Z",
            }
        )
    fake_api.search = {"metadata": {"resultset": {"count": 32}}, "result": rows}
    out = _load(
        plugin.tools.subsidies_search,
        {"keyword": "人材", "deadline_from": "2099-01-01"},
    )
    assert out["count_api"] == 32
    assert out["count_returned"] == 0
    assert "deadline" not in out["query"]


def test_explicit_deadline_days_cannot_raise_the_cap(plugin, fake_api):
    plugin.settings.bind_config(None)
    try:
        assert plugin.settings.watch_deadline_days(None) == 14
        assert plugin.settings.watch_deadline_days(99) == 30
        saved = _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "cap30", "deadline_days": 99},
        )
        assert saved["deadline_days"] == 30
        assert plugin.watch.load_state("cap30")["deadline_days"] == 30
        plugin.settings.bind_config(
            lambda key: 10 if key == "watch_deadline_days" else None
        )
        assert plugin.settings.watch_deadline_days(99) == 10
        assert plugin.settings.search_limit(10000) == 30
        plugin.settings.bind_config(lambda key: 10 if key == "search_limit" else None)
        assert plugin.settings.search_limit(10000) == 10
        clamped = _load(plugin.tools.subsidies_search, {"keyword": "人材", "limit": 10000})
        assert clamped["count_returned"] <= 10
    finally:
        plugin.settings.bind_config(None)


def test_cron_entry_uses_saved_days_not_config(plugin, fake_api, capsys):
    plugin.settings.bind_config(lambda key: 30 if key == "watch_deadline_days" else None)
    try:
        _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "cron7", "deadline_days": 7},
        )
        assert plugin.watch.load_state("cron7")["deadline_days"] == 7
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = plugin.cron_entry.main("cron7")
    finally:
        plugin.settings.bind_config(None)
    assert code == 0
    assert "7 day(s)" in buf.getvalue()
    assert "30 day" not in buf.getvalue()
    captured = capsys.readouterr().out
    assert "7 day(s)" in captured or "7 day(s)" in buf.getvalue()


def test_html_503_stays_http_error_and_retries_twice(plugin, monkeypatch):
    attempts = {"n": 0}

    class HTML503(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(
                "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies",
                503,
                "unavailable",
                hdrs=None,
                fp=None,
            )

        def read(self, n=-1):
            return b"<html><body>busy</body></html>"

    def fake_open(request, timeout=None):
        attempts["n"] += 1
        raise HTML503()

    monkeypatch.setattr(plugin.client._opener, "open", fake_open)
    monkeypatch.setattr(plugin.client.time, "sleep", lambda _seconds: None)
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.client.call("/subsidies", {"keyword": "人材"})
    assert attempts["n"] == 3
    assert caught.value.kind == "http_error"
    assert caught.value.status == 503
    assert caught.value.kind != "bad_response"


def test_redirects_stay_on_same_https_host_and_send_no_key(plugin, monkeypatch):
    handler = plugin.client._SameHostRedirects()
    req = urllib.request.Request("https://api.jgrants-portal.go.jp/exp/v1/public/subsidies")
    followed = handler.redirect_request(
        req,
        None,
        302,
        "Found",
        {},
        "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies/",
    )
    assert followed.get_full_url().startswith("https://api.jgrants-portal.go.jp/")
    for bad in (
        "https://evil.example/steal",
        "http://api.jgrants-portal.go.jp/exp/v1/public/subsidies",
    ):
        with pytest.raises(plugin.errors.SubsidiesError):
            handler.redirect_request(req, None, 302, "Found", {}, bad)

    captured = {}

    def fake_open(request, timeout=None):
        captured["request"] = request
        raise urllib.error.URLError("blocked")

    monkeypatch.setattr(plugin.client._opener, "open", fake_open)
    monkeypatch.setattr(plugin.client.time, "sleep", lambda _seconds: None)
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.client._http_get("/subsidies", {"keyword": "人材"})
    assert caught.value.kind == "network_error"
    headers = {key.lower(): value for key, value in captured["request"].header_items()}
    assert "authorization" not in headers
    assert captured["request"].get_full_url().startswith("https://api.jgrants-portal.go.jp/")


def test_in_process_get_gap_is_at_least_150ms(plugin, monkeypatch):
    slept = []
    monkeypatch.setattr(plugin.client.time, "sleep", lambda seconds: slept.append(seconds))
    plugin.client._last_request_mono = time.monotonic()
    plugin.client._throttle()
    assert slept
    assert slept[-1] >= plugin.client.MIN_REQUEST_INTERVAL_SECONDS - 0.01
    assert plugin.client.MIN_REQUEST_INTERVAL_SECONDS >= 0.15


def test_oversized_response_is_bad_response(plugin, monkeypatch):
    class Huge:
        def getcode(self):
            return 200

        def read(self, n=-1):
            if getattr(self, "_done", False):
                return b""
            self._done = True
            return b"x" * (plugin.client.MAX_RESPONSE_BYTES + 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(plugin.client._opener, "open", lambda request, timeout=None: Huge())
    monkeypatch.setattr(plugin.client.time, "sleep", lambda _seconds: None)
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.client._http_get("/subsidies", {"keyword": "人材"})
    assert caught.value.kind == "bad_response"
    assert plugin.client.MAX_RESPONSE_BYTES == 40 * 1024 * 1024


def test_timeout_next_step_does_not_ask_to_narrow(plugin):
    step = plugin.client._out_of_time().next_step
    assert "narrower keyword" not in step
    assert "Do not narrow the keyword" in step


def test_attachment_over_20mib_is_refused_and_cap_has_no_argument(plugin):
    import inspect

    assert plugin.save.MAX_ATTACHMENT_BYTES == 20 * 1024 * 1024
    assert "max_bytes" not in inspect.signature(plugin.save.save_attachments).parameters
    plugin.save.MAX_ATTACHMENT_BYTES = 4
    try:
        with pytest.raises(plugin.errors.SubsidiesError) as caught:
            plugin.save.save_attachments(
                "a0W0000000000009",
                {"outline_of_grant": [{"name": "a.pdf", "data": "eHh4eHg="}]},
            )
    finally:
        plugin.save.MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
    assert caught.value.kind == "bad_response"
    assert not (plugin.save.plugin_root() / "files" / "a0W0000000000009").exists()


def test_failed_search_leaves_no_cached_body(plugin, fake_api, _isolate_hermes_home):
    before = {path for path in _isolate_hermes_home.rglob("*")}
    fake_api.raise_on_search = plugin.errors.SubsidiesError(
        "Could not reach api.jgrants-portal.go.jp.",
        kind="network_error",
    )
    out = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    assert out["kind"] == "network_error"
    after = {path for path in _isolate_hermes_home.rglob("*")}
    assert after == before


def test_missing_write_guard_creates_no_watch_or_script(plugin, fake_api, _isolate_hermes_home):
    sys.modules.pop("agent.file_safety", None)
    before = {path for path in _isolate_hermes_home.rglob("*")}
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "noguard"})
    assert out["kind"] == "access_denied"
    assert "file_safety" in out["error"]
    assert not (_isolate_hermes_home / "scripts").exists()
    assert not (_isolate_hermes_home / "plugin-data" / "jp-subsidies").exists()
    assert {path for path in _isolate_hermes_home.rglob("*")} == before
    assert plugin.watch.load_state("noguard") is None
    assert fake_api.calls == []


def test_denied_script_guard_does_not_create_plugin_data(plugin, _isolate_hermes_home):
    """Same measurement as acceptance row 38: guard refuses, directories do not grow."""
    data = _isolate_hermes_home / "plugin-data" / "jp-subsidies"
    scripts = _isolate_hermes_home / "scripts"
    assert not data.exists()
    sys.modules["agent.file_safety"].get_write_denied_error = lambda path, **kwargs: (
        "outside HERMES_WRITE_SAFE_ROOT"
    )
    before = {path for path in _isolate_hermes_home.rglob("*")}
    script = plugin.save.scripts_dir() / "jp-subsidies-weekly-default.py"
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.assert_writable(script)
    assert caught.value.kind == "access_denied"
    assert {path for path in _isolate_hermes_home.rglob("*")} == before
    assert not data.exists()
    assert not scripts.exists()


def test_missing_plugin_data_dir_does_not_fall_back(plugin, fake_api, _isolate_hermes_home):
    sys.modules.pop("plugins.plugin_storage", None)
    plugins_mod = sys.modules.get("plugins")
    if plugins_mod is not None and hasattr(plugins_mod, "plugin_storage"):
        delattr(plugins_mod, "plugin_storage")
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "nodir"})
    assert out["kind"] == "access_denied"
    assert "HERMES_HOME" in out["next_step"]
    assert not (_isolate_hermes_home / "plugin-data" / "jp-subsidies" / "watch").exists()
    assert fake_api.calls == []


def test_cli_cron_and_api_server_are_not_deliver_targets(plugin, fake_api, monkeypatch):
    monkeypatch.setattr(
        plugin.schedule,
        "_extra_platform_names",
        lambda: {"cli", "cron", "api_server", "chatwork"},
    )
    for deliver in ("cli", "cron", "api_server"):
        out = _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "nodeliver", "deliver": deliver},
        )
        assert out["kind"] == "bad_request", deliver
        assert plugin.watch.load_state("nodeliver") is None
    assert fake_api.calls == []
    assert plugin.schedule.require_deliver("chatwork:1") == "chatwork:1"


def test_monday_refusal_names_accepted_targets_and_unloaded_chatwork(plugin, fake_api):
    out = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "monday", "deliver": "monday"},
    )
    text = out["error"] + out.get("next_step", "")
    assert out["kind"] == "bad_request"
    assert "all" in text
    assert "bot-chat" in text
    assert "origin,all" in text
    assert "when jp-chatwork is enabled" in text
    chat = _load(
        plugin.tools.subsidies_watch_setup,
        {"keyword": "人材", "profile": "monday", "deliver": "chatwork:1"},
    )
    assert chat["kind"] == "bad_request"
    assert "when jp-chatwork is enabled" in chat["error"] + chat.get("next_step", "")
    assert plugin.watch.load_state("monday") is None
    assert fake_api.calls == []


def test_bad_when_writes_nothing(plugin, fake_api, _isolate_hermes_home):
    for when in ("zzz", "", "origin"):
        out = _load(
            plugin.tools.subsidies_watch_setup,
            {"keyword": "人材", "profile": "badwhen", "when": when},
        )
        assert out["kind"] == "bad_request", repr(when)
        assert plugin.watch.load_state("badwhen") is None
    assert not (_isolate_hermes_home / "scripts").exists()
    assert fake_api.calls == []
    code = plugin.cli.handle(_sched(watch="cliempty", when=""))
    assert code == 1
    assert plugin.watch.load_state("cliempty") is None
    assert fake_api.calls == []


def test_printed_cron_create_is_a_new_job_and_uses_profile_flag(plugin, fake_api, monkeypatch, capsys, _isolate_hermes_home):
    monkeypatch.setattr(plugin.schedule, "hermes_profile_name", lambda: "studio")
    assert plugin.cli.handle(_sched()) == 0
    printed = capsys.readouterr().out
    assert "hermes -p studio cron create" in printed
    assert "--no-agent" in printed
    assert "-p osaka" not in printed
    assert "--profile" not in printed
    assert "running it again delivers the same weekly check twice" in printed
    assert "hermes cron create line" not in printed
    for action in ("list", "remove", "run", "status"):
        assert f"hermes -p studio cron {action}" in printed
    assert "hermes cron remove" not in printed
    state_path = plugin.watch.state_path("osaka")
    assert str(_isolate_hermes_home) in str(state_path)
    assert state_path.is_file()
    monkeypatch.setattr(plugin.schedule, "hermes_profile_name", lambda: "default")
    again = plugin.schedule.cron_command(
        state_path, "osaka", "origin", "0 9 * * 1"
    )
    assert again.startswith("hermes cron create ")
    assert " -p " not in again
    assert plugin.schedule.hermes_cron_invocation("list") == "hermes cron list"
    assert plugin.schedule.hermes_cron_invocation("status") == "hermes cron status"


def test_profile_name_on_v0214_reads_the_home(plugin, monkeypatch):
    """v0.21.4 has no current_profile_name. The home path still names the profile."""
    import types

    fake = types.ModuleType("hermes_cli.profiles")
    fake.get_active_profile_name = lambda: "harnessprof"
    pkg = types.ModuleType("hermes_cli")
    pkg.profiles = fake
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)
    assert plugin.schedule.hermes_profile_name() == "harnessprof"
    fake.get_active_profile_name = lambda: "custom"
    assert plugin.schedule.hermes_profile_name() == "default"
    note = plugin.schedule.unnamed_home_note()
    assert "no -p" in note
    assert "hermes cron" not in note
    fake.current_profile_name = lambda default="default": "from-current"
    assert plugin.schedule.hermes_profile_name() == "from-current"
    fake.current_profile_name = lambda default="default": "custom"
    assert plugin.schedule.hermes_profile_name() == "default"


def test_profiles_api_failure_refuses_before_write(plugin, fake_api, monkeypatch, _isolate_hermes_home, capsys):
    """Ledger: a missing, raising, or renamed profiles API must not print a default cron line."""
    import types

    home = _isolate_hermes_home

    def refused(label: str) -> None:
        before = {path.relative_to(home) for path in home.rglob("*")}
        out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "noprof"})
        assert out["kind"] == "access_denied", label
        assert "cron_command" not in out
        assert "not the default home" in out["error"]
        assert "hermes cron" not in out["error"] + out.get("next_step", "")
        code = plugin.cli.handle(_sched(watch="noprof"))
        printed = capsys.readouterr().out
        assert code == 1, label
        assert "hermes cron" not in printed
        assert "not the default home" in printed
        after = {path.relative_to(home) for path in home.rglob("*")}
        assert after == before, label
        assert fake_api.calls == [], label

    monkeypatch.setitem(sys.modules, "hermes_cli", None)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)
    refused("import")

    fake = types.ModuleType("hermes_cli.profiles")
    pkg = types.ModuleType("hermes_cli")
    pkg.profiles = fake
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake)
    refused("renamed")

    def boom():
        raise RuntimeError("profiles down")

    fake.get_active_profile_name = boom
    refused("raises")

    fake.current_profile_name = lambda default="default": (_ for _ in ()).throw(RuntimeError("renamed-call"))
    refused("current-raises")


def test_missing_home_helper_does_not_create_plugin_data(plugin, monkeypatch, _isolate_hermes_home):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    before = {path for path in _isolate_hermes_home.rglob("*")}
    with pytest.raises(plugin.errors.SubsidiesError) as caught:
        plugin.save.plugin_root()
    assert caught.value.kind == "access_denied"
    assert "HERMES_HOME" in caught.value.next_step
    assert {path for path in _isolate_hermes_home.rglob("*")} == before
    assert not (_isolate_hermes_home / "plugin-data").exists()


def test_argparse_watch_is_not_a_hermes_profile_flag(plugin):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    command = sub.add_parser("jp-subsidies")
    plugin.cli.setup_parser(command)
    with pytest.raises(SystemExit):
        parser.parse_args(["jp-subsidies", "schedule", "--keyword", "設備", "--profile", "osaka"])
    args = parser.parse_args(["jp-subsidies", "schedule", "--keyword", "設備", "--watch", "osaka"])
    assert args.watch == "osaka"
    assert getattr(args, "profile", None) in (None, argparse.SUPPRESS)
    with pytest.raises(SystemExit) as unknown:
        parser.parse_args(["jp-subsidies", "nosuch"])
    assert unknown.value.code == 1
    with pytest.raises(SystemExit) as helped:
        parser.parse_args(["jp-subsidies", "--help"])
    assert helped.value.code in (0, None)


def test_slash_worker_does_not_stall_the_loop_or_the_default_pool(plugin, monkeypatch):
    def slow(_profile):
        time.sleep(0.4)
        return "ok"

    monkeypatch.setattr(plugin.cli, "_readonly_check", slow)

    async def stall():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(plugin.cli.slash("check stall"))
        gaps = []
        last = loop.time()
        while not task.done():
            await asyncio.sleep(0.02)
            now = loop.time()
            gaps.append(now - last)
            last = now
        assert await task == "ok"
        return max(gaps)

    # A full on-loop sleep is about 0.4s. One late tick under a busy
    # preship run can exceed 0.2s, so keep the quietest of three runs.
    quietest = min(asyncio.run(stall()) for _ in range(3))
    assert quietest < 0.2

    async def eight():
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        try:
            tasks = [asyncio.create_task(plugin.cli.slash("check busy")) for _ in range(8)]
            await asyncio.sleep(0.05)
            started = loop.time()
            await asyncio.to_thread(time.sleep, 0.05)
            waited = loop.time() - started
            await asyncio.gather(*tasks)
            return waited
        finally:
            pool.shutdown(wait=True)

    assert asyncio.run(eight()) < 2.0
    assert asyncio.iscoroutinefunction(plugin.cli.slash)


def test_weekly_english_leads_and_carries_citation(plugin, fake_api):
    out = _load(plugin.tools.subsidies_watch_setup, {"keyword": "人材", "profile": "cite"})
    message = out["message"]
    assert message.startswith("jp-subsidies weekly check")
    assert "出典：J グランツ" in message
    assert message.index("jp-subsidies weekly check") < message.index("出典：J グランツ")
    assert message.index("出典：J グランツ") < message.index(plugin.citation.PROCESSED_NOTE)
    search = _load(plugin.tools.subsidies_search, {"keyword": "人材"})
    detail = _load(plugin.tools.subsidies_detail, {"subsidy_id": "a0WfQ000003MWE2UAO"})
    for body in (search, detail, out):
        assert body["source"]["citation"] == "出典：J グランツ"
        assert body["source"]["processed_note"] == plugin.citation.PROCESSED_NOTE


def test_catalog_and_public_text_match_the_table(public_text):
    desc = public_text["description"]
    readme = public_text["readme"]
    pr = public_text["pr"]
    blob = "\n".join((desc, readme, pr, public_text["manifest"]))
    assert desc.startswith(
        "Japanese subsidy search and weekly watch, built on the jGrants public API."
    )
    assert "Disclosure —" in desc
    assert len(desc) <= 1500
    assert "Count mismatch is not complete." not in blob
    # 10/8: カタログ文は Teknium の形（完全な文）。語句の一致ではなく、開示すべき事実が入っているかを見る
    for phrase in (
        "no daily request cap",
        "60 seconds",
        "retries at most twice",
        "not proof that every subsidy came back",
        "scripts/jp-subsidies-weekly-*.py",
        "hermes cron remove",
        "plugin-data/jp-subsidies/",
        "hermes tools",
        "CHATWORK_ALLOWED_USERS",
        "CHATWORK_ROOMS",
        "/subsidies command is outside hermes tools",
        "allow_admin_from",
        "user_allowed_commands",
        "disable the plugin",
        "anyone you allow on your Hermes gateway",
        "set up a watch by itself",
        "not a sandbox",
        "one model turn",
        "cron/output/",
    ):
        assert phrase in desc, phrase
    for phrase in (
        "gateway.platform_registry.registered_names()",
        "A manual check does not advance seen.",
        "one model turn",
        "costs a model call",
        "--no-agent",
        "plugins.isolation",
        "tests/",
        "register()",
        "speaker",
        "agent.file_safety",
        "not necessarily",
        "cron/output/",
        "home channel",
        "running it twice delivers",
        "hermes cron remove",
        "jp-estat",
        "built on the jGrants public API",
    ):
        assert phrase in blob, phrase
    assert "jGrants for Hermes" not in blob
    assert "Unofficial" not in blob
    assert "非公式" not in blob
    assert "not affiliated" not in blob.lower()
    assert "jp-edinet" not in pr
    for peer in ("jp-estat", "jp-corporate", "jp-egov-law", "jp-charts"):
        assert peer in pr
    assert "not that MCP server" in pr
    watch = pr.split("- Watch:", 1)[1]
    assert 0 <= watch.find("get_hermes_home()") < watch.find("If that API cannot be loaded")
    assert watch.find("If that API cannot be loaded") < watch.find("Tokyo")
    assert "detail v2 path was not called" in readme
    assert "0.15 second gap was not measured between separate processes" in readme
    assert "実ゲートウェイ" in readme
    shas = set(__import__("re").findall(r"\b[0-9a-f]{40}\b", pr))
    assert shas == {public_text["catalog_sha"]}
    notice_line = "Copyright (c) 2025 Digital Agency, Government of Japan"
    license_text = (
        SHIP / "audit_log" / "src" / "upstream_LICENSE.txt"
    ).read_text(encoding="utf-8")
    assert notice_line in license_text
    assert notice_line in public_text["notice"]
    register_src = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "tests" not in register_src
    assert "cron remove" not in register_src
