"""`hermes jp-subsidies …` commands."""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import client, schedule, watch
from .errors import SubsidiesError
PLUGIN_DIR = Path(__file__).resolve().parent
_settings: Callable[[], dict] = lambda: {}
# Dedicated pool so /subsidies does not occupy the default asyncio executor.
_SLASH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="jp-subsidies-slash")


def set_settings_reader(reader: Callable[[], dict]) -> None:
    global _settings
    _settings = reader


def setup_parser(sub) -> None:
    cmds = sub.add_subparsers(dest="subsidies_command")
    s = cmds.add_parser(
        "schedule",
        help="Save a watch baseline, write the weekly cron script, print hermes cron create",
    )
    s.add_argument("--keyword", required=True, help="jGrants keyword (2–255 chars)")
    s.add_argument(
        "--watch",
        default="default",
        help="Watch name stored in the current Hermes home (not a Hermes profile flag)",
    )
    s.add_argument("--area", dest="target_area_search", default=None)
    s.add_argument("--industry", default=None)
    s.add_argument("--employees", dest="target_number_of_employees", default=None)
    s.add_argument("--purpose", dest="use_purpose", default=None)
    s.add_argument("--deadline-days", type=int, default=None)
    s.add_argument(
        "--deliver",
        default="origin",
        help=(
            "Hermes cron delivery target (default: origin). "
            "origin uses the job's captured origin chat when present; a bare CLI create "
            "often has none and falls back to the home channel or skips chat. "
            "Use an explicit platform:chat_id for reliable CLI scheduling. "
            "'local' only writes under the Hermes home's cron/output/ and does not notify chat."
        ),
    )
    s.add_argument("--when", default="0 9 * * 1")
    c = cmds.add_parser(
        "check",
        help="Read the watch once. Does not advance seen_ids or last_count (cron does)",
    )
    c.add_argument(
        "--watch",
        default="default",
        help="Watch name stored in the current Hermes home (not a Hermes profile flag)",
    )
    sub.set_defaults(func=handle)

    def _exit_unknown_as_1(status=0, message=None):
        # argparse uses status 2 for an unknown subcommand. Acceptance requires 1.
        # Help and other status-0 exits stay 0.
        if message:
            sub._print_message(message, sys.stderr)
        code = 0 if int(status or 0) == 0 else 1
        raise SystemExit(code)

    sub.exit = _exit_unknown_as_1


def handle(args) -> int:
    cmd = getattr(args, "subsidies_command", None)
    try:
        with client.budget():
            if cmd == "schedule":
                return _schedule(args)
            if cmd == "check":
                result = watch.run(args.watch, advance=False)
                print(result["message"])
                return 0
    except SubsidiesError as exc:
        print(f"✗ {exc}")
        if exc.next_step:
            print(exc.next_step)
        return 1
    print("Usage: hermes jp-subsidies {schedule|check}")
    return 1


def _schedule(args) -> int:
    from . import save as save_mod

    payload = {
        "keyword": args.keyword,
        "profile": args.watch,
        "target_area_search": args.target_area_search,
        "industry": args.industry,
        "target_number_of_employees": args.target_number_of_employees,
        "use_purpose": args.use_purpose,
        "deadline_days": args.deadline_days,
        "deliver": args.deliver,
        "when": args.when,
    }
    profile = watch.profile_name(args.watch)
    deliver = schedule.require_deliver(args.deliver)
    schedule.require_reachable_bot_chat(deliver)
    when = schedule.require_when("" if args.when is None else str(args.when))
    schedule.require_profiles_api()
    args.deliver = deliver
    args.when = when
    script_path = save_mod.scripts_dir() / schedule.script_name(profile)
    save_mod.assert_writable(script_path)
    state_file = watch.state_path(profile)
    existed = watch.exact_named_file(state_file)
    try:
        result = watch.setup(payload)
        script = schedule.write_script(PLUGIN_DIR, result["profile"])
    except SubsidiesError:
        watch.discard_state_if_created(profile, existed)
        raise
    cmd = schedule.cron_command(script, result["profile"], args.deliver, args.when)
    print(result["message"])
    print()
    print(f"✓ State: {result['state_path']}")
    print(f"✓ Script: {script}")
    print("Run this line to register the weekly job:")
    print("  " + cmd)
    listed = schedule.hermes_cron_invocation("list")
    remove = schedule.hermes_cron_invocation("remove", "<old job id>")
    run = schedule.hermes_cron_invocation("run", "<job id from that line>")
    status = schedule.hermes_cron_invocation("status")
    print(
        "That create line always creates a new job. "
        "It does not replace one with the same name; running it again delivers the same weekly check twice. "
        f"If a job for this profile already exists: {listed}, then {remove}."
    )
    print(f"Test it: {run}")
    print(f"Note: the Hermes gateway runs scheduled jobs; check {status} if nothing arrives.")
    print("The when expression uses Hermes' configured timezone, which is not necessarily JST (Asia/Tokyo).")
    home_note = schedule.unnamed_home_note()
    if home_note:
        print(home_note)
    print("（状態とスクリプトを書きました。上の cron 行を実行してください。同じ行を二度実行するとジョブが二つになります。）")
    return 0


def _slash_schedule_refusal() -> str:
    return (
        "✗ /subsidies only runs a saved watch check. "
        "It does not take a schedule or a delivery target.\n"
        "Use subsidies_watch_setup or `hermes jp-subsidies schedule` to set up the weekly job. "
        "Put the period in --when and the chat in --deliver (origin, local, or platform:chat_id). "
        "Period words such as every, 5m, or '0 9 * * 1' are not a delivery target. "
        "`/subsidies check 5m` and `/subsidies check every` are refused as periods, not looked up as profiles."
    )


def _readonly_check(profile: str) -> str:
    with client.budget():
        return watch.run(profile, advance=False)["message"]


async def slash(raw: str) -> str:
    """`/subsidies check [profile]` — read-only watch check on a worker thread."""
    parts = (raw or "").split()
    profile = "default"
    if not parts:
        profile = "default"
    elif parts[0] == "check":
        if len(parts) > 2:
            return (
                "✗ /subsidies check takes at most one profile.\n"
                "Usage: /subsidies check [profile]"
            )
        if len(parts) == 2 and schedule.looks_like_deliver_target(parts[1]):
            return (
                "✗ /subsidies does not take a delivery target.\n"
                "Usage: /subsidies check [profile]\n"
                "Put origin, local, or platform:chat_id in `hermes jp-subsidies schedule --deliver`."
            )
        if len(parts) == 2 and schedule.looks_like_schedule(parts[1]):
            return _slash_schedule_refusal()
        profile = parts[1] if len(parts) > 1 else "default"
    elif (
        parts[0] in {"schedule", "setup", "search", "detail"}
        or schedule.looks_like_schedule(parts[0])
        or schedule.looks_like_schedule(" ".join(parts))
        or schedule.looks_like_deliver_target(parts[0])
        or schedule.looks_like_deliver_target(" ".join(parts))
    ):
        return _slash_schedule_refusal()
    elif len(parts) > 1:
        return (
            "✗ /subsidies accepts `check` and one profile name.\n"
            "Usage: /subsidies [check] [profile]"
        )
    else:
        profile = parts[0]
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_SLASH_EXECUTOR, _readonly_check, profile)
    except SubsidiesError as exc:
        msg = f"✗ {exc}" + (f"\n{exc.next_step}" if exc.next_step else "")
        return msg
