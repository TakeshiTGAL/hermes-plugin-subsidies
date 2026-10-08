"""Weekly watch: baseline on first run, then new IDs + deadlines within N days.

API failures never update the stored ID set and are reported as failures (not
as “0 new” or “no upcoming deadlines”).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import citation, client, enums, save, settings
from .errors import SubsidiesError

JST = timezone(timedelta(hours=9))
# Stored as a single file name. The pattern is the name itself: no rewriting,
# so two different labels cannot land on one file (東京 and 大阪 must not become default).
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def profile_name(name: str) -> str:
    if name is None or name == "":
        return "default"
    text = str(name)
    if not _PROFILE_RE.fullmatch(text) or ".." in text:
        raise SubsidiesError(
            f"profile must be 1–64 ASCII letters, digits, dot, underscore, or hyphen "
            f"(got {name!r}). Names are stored unchanged, so Japanese names are not "
            "folded into 'default'.",
            kind="bad_request",
            next_step="Pass an ASCII profile such as 'tokyo' or 'osaka', or omit profile to use 'default'.",
        )
    return text


def state_path(profile: str) -> Path:
    return save.plugin_root() / "watch" / f"{profile_name(profile)}.json"


def exact_named_file(path: Path) -> bool:
    """True only when the directory lists this exact name, not a case variant."""
    parent = path.parent
    if not parent.is_dir():
        return False
    return any(child.name == path.name and child.is_file() for child in parent.iterdir())


def discard_state_if_created(profile: str, existed_before: bool) -> None:
    """Remove a watch JSON this call created after a later step failed."""
    if existed_before:
        return
    path = state_path(profile)
    if exact_named_file(path):
        path.unlink()


def _refuse_script_case_collision(profile: str) -> None:
    """Refuse a case-only cron script name before any watch JSON is written."""
    from . import schedule

    script = save.scripts_dir() / schedule.script_name(profile)
    save.refuse_case_variant(script.parent, script.name, what="Cron script")


def _refuse_state_case_collision(profile: str) -> None:
    """Refuse a case-only watch JSON name before the list API is called."""
    path = state_path(profile)
    save.refuse_case_variant(path.parent, path.name, what="Watch profile file")


def load_state(profile: str) -> Optional[dict[str, Any]]:
    path = state_path(profile)
    save.refuse_case_variant(path.parent, path.name, what="Watch profile file")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SubsidiesError(
            f"Could not read watch state at {path}: {error}",
            kind="internal_error",
            next_step="Delete the broken state file and run schedule again.",
        ) from error
    if not isinstance(data, dict):
        raise SubsidiesError(
            f"Watch state at {path} is not a JSON object.",
            kind="internal_error",
            next_step="Delete the broken state file and run schedule again.",
        )
    return data


def write_state(profile: str, data: dict[str, Any]) -> Path:
    path = state_path(profile)
    save.assert_writable(path)
    save.refuse_case_variant(path.parent, path.name, what="Watch profile file")
    save.ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def normalize_criteria(args: dict[str, Any]) -> dict[str, Any]:
    keyword = str(args.get("keyword") or "").strip()
    if not (2 <= len(keyword) <= 255):
        raise SubsidiesError(
            "keyword must be 2–255 characters (jGrants list API requirement).",
            kind="bad_request",
            next_step="Pass a Japanese keyword such as '人材' or '設備'.",
        )
    raw_acceptance = args.get("acceptance", 1)
    if raw_acceptance in (None, ""):
        acceptance = 1
    elif raw_acceptance in (1, "1", True):
        acceptance = 1
    elif raw_acceptance in (0, "0", False):
        acceptance = 0
    else:
        raise SubsidiesError(
            f"acceptance must be 0 or 1 (got {raw_acceptance!r}).",
            kind="bad_request",
            next_step="Pass acceptance=1 (open calls only, default) or acceptance=0 (no filter).",
        )
    criteria: dict[str, Any] = {
        "keyword": keyword,
        "acceptance": acceptance,
        "sort": args.get("sort") or "created_date",
        "order": str(args.get("order") or "DESC").upper(),
    }
    if args.get("industry") not in (None, ""):
        criteria["industry"] = enums.require_enum(
            "industry", str(args["industry"]), enums.INDUSTRY_VALUES
        )
    if args.get("target_area_search") not in (None, ""):
        criteria["target_area_search"] = enums.require_enum(
            "target_area_search",
            str(args["target_area_search"]),
            enums.AREA_VALUES,
            allow_multi=False,
        )
    if args.get("target_number_of_employees") not in (None, ""):
        criteria["target_number_of_employees"] = enums.require_enum(
            "target_number_of_employees",
            str(args["target_number_of_employees"]),
            enums.EMPLOYEE_VALUES,
        )
    if args.get("use_purpose") not in (None, ""):
        criteria["use_purpose"] = enums.require_enum(
            "use_purpose", str(args["use_purpose"]), enums.USE_PURPOSE_VALUES
        )
    if criteria["sort"] not in {
        "created_date",
        "acceptance_start_datetime",
        "acceptance_end_datetime",
    }:
        raise SubsidiesError(
            "sort must be created_date, acceptance_start_datetime, or acceptance_end_datetime.",
            kind="bad_request",
            next_step=(
                "Pass sort=created_date, acceptance_start_datetime, or "
                "acceptance_end_datetime."
            ),
        )
    if criteria["order"] not in {"ASC", "DESC"}:
        raise SubsidiesError(
            "order must be ASC or DESC.",
            kind="bad_request",
            next_step="Pass order=ASC or order=DESC.",
        )
    return criteria


def api_params(criteria: dict[str, Any]) -> dict[str, Any]:
    return {
        "keyword": criteria["keyword"],
        "sort": criteria["sort"],
        "order": criteria["order"],
        "acceptance": str(criteria["acceptance"]),
        "industry": criteria.get("industry"),
        "target_area_search": criteria.get("target_area_search"),
        "target_number_of_employees": criteria.get("target_number_of_employees"),
        "use_purpose": criteria.get("use_purpose"),
    }


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_list(criteria: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[int]]:
    """Return (rows, metadata_count). Fail when metadata.count ≠ len(rows)."""
    body = client.search_subsidies(api_params(criteria))
    result = body.get("result")
    if result is None:
        raise SubsidiesError(
            "jGrants search response had no result field.",
            kind="bad_response",
            next_step="Retry later.",
        )
    if not isinstance(result, list):
        raise SubsidiesError(
            "jGrants search result was not a list.",
            kind="bad_response",
            next_step="Retry later.",
        )
    meta_count = None
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    resultset = metadata.get("resultset") if isinstance(metadata.get("resultset"), dict) else {}
    if "count" not in resultset:
        raise SubsidiesError(
            "jGrants search response is missing metadata.resultset.count.",
            kind="bad_response",
            next_step="Retry later; do not treat this as an empty or complete list.",
        )
    try:
        meta_count = int(resultset["count"])
    except (TypeError, ValueError) as error:
        raise SubsidiesError(
            "jGrants metadata.resultset.count was not an integer.",
            kind="bad_response",
            next_step="Retry later.",
        ) from error
    if meta_count != len(result):
        raise SubsidiesError(
            f"jGrants metadata count ({meta_count}) does not match result rows ({len(result)}).",
            kind="bad_response",
            next_step="Retry later; do not treat this as an empty or complete list.",
        )
    return result, meta_count


def upcoming(items: list[dict[str, Any]], days: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    now = now or datetime.now(tz=timezone.utc)
    end = now + timedelta(days=days)
    out = []
    for item in items:
        deadline = parse_dt(item.get("acceptance_end_datetime"))
        if deadline is None:
            continue
        if now <= deadline <= end:
            out.append(item)
    out.sort(
        key=lambda it: parse_dt(it.get("acceptance_end_datetime"))
        or datetime.max.replace(tzinfo=timezone.utc)
    )
    return out


def _format_deadline(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        return str(value) if value else "(no deadline in list API)"
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def _line(item: dict[str, Any]) -> str:
    title = item.get("title") or item.get("name") or "(no title)"
    end = _format_deadline(item.get("acceptance_end_datetime"))
    area = item.get("target_area_search") or "(no area)"
    limit = item.get("subsidy_max_limit")
    if limit in (None, ""):
        limit_s = "absent"
    elif limit == 0:
        limit_s = "0 (often unset/unknown in list API — check subsidies_detail)"
    else:
        limit_s = str(limit)
    return (
        f"- {title}\n"
        f"  id={item.get('id')} / deadline={end} / area={area} / subsidy_max_limit={limit_s}"
    )


def _criteria_equal(a: Any, b: Any) -> bool:
    """Compare watch search criteria (ignore non-dict / order of keys)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return a == b


def render_message(
    *,
    profile: str,
    criteria: dict[str, Any],
    baseline: bool,
    new_items: list[dict[str, Any]],
    due_items: list[dict[str, Any]],
    total: int,
    days: int,
    checked_at: datetime,
    re_setup: bool = False,
    criteria_changed: bool = False,
    previous_seen: int = 0,
    tracked_seen: int = 0,
    count_drop_warning: str = "",
    count_unverified: bool = False,
) -> str:
    when = checked_at.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    cond = f"keyword={criteria.get('keyword')!r}"
    if criteria.get("target_area_search"):
        cond += f", area={criteria['target_area_search']!r}"
    if criteria.get("industry"):
        cond += f", industry={criteria['industry']!r}"
    if criteria.get("target_number_of_employees"):
        cond += f", employees={criteria['target_number_of_employees']!r}"
    if criteria.get("use_purpose"):
        cond += f", purpose={criteria['use_purpose']!r}"
    lines = [
        f"jp-subsidies weekly check (profile={profile})",
        f"Checked at: {when}",
        f"Criteria: {cond}",
        f"List API count: {total}",
        citation.CREDIT,
        "",
    ]
    if count_drop_warning:
        lines += [count_drop_warning, ""]
    if baseline and criteria_changed:
        lines += [
            f"Criteria changed: took a new baseline of {total} id(s). "
            "Nothing is reported as new this time.",
            "Previous seen_ids from the old criteria were discarded so old matches are not "
            "flooded as 'new' on the next check.",
            f"（条件を変えたので基準を取り直しました: {total} 件。今回は新着として報告しません。）",
            "",
        ]
    elif baseline and not re_setup:
        lines += [
            f"First run: saved {total} id(s) as the baseline.",
            "Nothing is reported as new this time. Later successful runs will list ids that appear after this baseline.",
            f"（初回: 基準として {total} 件の ID を保存しました。今回は新着として報告しません。）",
            "",
        ]
    elif re_setup and count_unverified:
        lines += [
            f"Re-setup (not a first run): kept {previous_seen} previously seen id(s) unchanged "
            f"(did not absorb the current list into seen). Current list has {total} id(s); "
            f"tracked seen set stays at {tracked_seen}.",
            "New since last success: unverified (list count is 0 after a prior non-zero count).",
            "New ids stay unverified until a non-zero count returns.",
            f"（再設定: 既存の seen_ids {previous_seen} 件はそのまま残します。"
            "一覧が0件なので新着は unverified です。件数が戻るまで新着としては出しません。）",
            "",
        ]
    elif re_setup:
        lines += [
            f"Re-setup (not a first run): kept {previous_seen} previously seen id(s) unchanged "
            f"(did not absorb the current list into seen). Current list has {total} id(s); "
            f"tracked seen set stays at {tracked_seen}.",
            "Ids that appeared since the last successful check and are still not in seen_ids "
            "will be reported as new on the next successful check — they are not silenced by re-setup.",
            f"（再設定: 既存の seen_ids {previous_seen} 件はそのまま残し、今回の一覧を seen に吸収しません。"
            f"初回ではありません。未通知の新着は次回の check で報告されます。）",
            "",
        ]
    elif count_unverified:
        lines += [
            "New since last success: unverified (list count is 0 after a prior non-zero count).",
            "",
        ]
    else:
        lines.append(f"New since last success: {len(new_items)}")
        if new_items:
            lines.extend(_line(it) for it in new_items)
        else:
            lines.append("- (none)")
        lines.append("")
    if count_unverified:
        lines += [
            f"Deadlines within {days} day(s): unverified "
            "(list count is 0 after a prior non-zero count).",
            "This is not proof that no deadline falls in the window.",
            "",
        ]
    else:
        lines.append(f"Deadlines within {days} day(s): {len(due_items)}")
        if due_items:
            lines.extend(_line(it) for it in due_items)
        else:
            lines.append("- (none)")
        lines.append("")
    lines += [
        "",
        "subsidy_rate and detail URL are not in the list API — use subsidies_detail when needed.",
        f"Portal: {citation.PORTAL_URL}",
        citation.PROCESSED_NOTE,
    ]
    return "\n".join(lines)


def _prior_last_count(existing: Optional[dict[str, Any]]) -> Optional[int]:
    if not existing:
        return None
    raw = existing.get("last_count")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def setup(args: dict[str, Any]) -> dict[str, Any]:
    """Save criteria and baseline.

    Same criteria re-setup keeps previous ``seen_ids`` (does not absorb the current
    list). Different criteria retakes a baseline like the first run so old matches
    are not flooded as new on the next check.
    """
    profile = profile_name(str(args.get("profile") or "default"))
    _refuse_script_case_collision(profile)
    _refuse_state_case_collision(profile)
    criteria = normalize_criteria(args)
    days = settings.watch_deadline_days(args.get("deadline_days"))
    items, _meta = fetch_list(criteria)
    ids = [str(it.get("id")) for it in items if it.get("id")]
    now = datetime.now(tz=timezone.utc)
    existing = load_state(profile)
    prior_seen = [str(x) for x in (existing.get("seen_ids") or [])] if existing else []
    had_profile = bool(existing and (existing.get("baseline_at") or prior_seen))
    prior_criteria = existing.get("criteria") if existing else None
    criteria_changed = had_profile and not _criteria_equal(prior_criteria, criteria)
    # Same-criteria re-setup: keep prior seen. First run or criteria change: new baseline.
    re_setup = had_profile and not criteria_changed
    tracked = sorted(set(prior_seen)) if re_setup else sorted(set(ids))
    pending_new = sorted(set(ids) - set(prior_seen)) if re_setup else []
    prior_count = _prior_last_count(existing)
    count_drop = bool(re_setup and len(items) == 0 and prior_count is not None and prior_count > 0)
    state = {
        "profile": profile,
        "criteria": criteria,
        "deadline_days": days,
        "seen_ids": tracked,
        "baseline_at": (
            existing.get("baseline_at")
            if re_setup and existing and existing.get("baseline_at")
            else now.isoformat()
        ),
        "last_success_at": now.isoformat(),
        "last_error": None,
        "last_count": prior_count if count_drop else len(items),
        "re_setup_at": now.isoformat() if re_setup else None,
        "criteria_changed_at": now.isoformat() if criteria_changed else None,
    }
    path = write_state(profile, state)
    message = render_message(
        profile=profile,
        criteria=criteria,
        baseline=not re_setup,
        re_setup=re_setup,
        criteria_changed=criteria_changed,
        previous_seen=len(prior_seen),
        tracked_seen=len(tracked),
        new_items=[],
        due_items=upcoming(items, days, now=now),
        total=len(items),
        days=days,
        checked_at=now,
        count_unverified=count_drop,
        count_drop_warning=(
            f"Warning: list API count dropped from {prior_count} to 0. "
            "This may be a bad filter or an API glitch — not treated as 'no new subsidies'. "
            "This warning repeats until a non-zero count returns."
            if count_drop
            else ""
        ),
    )
    if len(items) == 0 and (not re_setup or not prior_seen) and not count_drop:
        message += (
            "\nWarning: list API count is 0 for these criteria. The baseline is empty; "
            "if a later check returns matches, every id will look new until you re-setup "
            "after confirming the filter."
            "（一覧が0件です。このまま基準を取ると、あとから件数が出たとき全部が新着に見えます。）"
        )
    elif len(items) == 0 and re_setup and prior_seen:
        message += (
            "\nNote: the list API returned 0 rows, but this is a same-criteria re-setup. "
            f"The previous {len(prior_seen)} seen id(s) were kept. This is not an empty baseline, "
            "and a later non-zero list will not mark every previously seen id as new."
            "（一覧は0件ですが、同じ条件の再設定なので以前の seen_ids は残しています。"
            "基準が空になったわけではありません。）"
        )
    if re_setup and pending_new:
        message += (
            f"\nNote: {len(pending_new)} id(s) in the current list are not in seen_ids yet; "
            "the next successful check will report them as new "
            f"（現在の一覧のうち未通知 {len(pending_new)} 件は seen に入れていません。次回の成功した check で新着として出ます）。"
        )
    return {
        "ok": True,
        "profile": profile,
        "state_path": str(path),
        "baseline": not re_setup,
        "re_setup": re_setup,
        "criteria_changed": criteria_changed,
        "count": len(items),
        "previous_seen": len(prior_seen),
        "tracked_seen": len(tracked),
        "pending_new_count": len(pending_new),
        "deadline_days": days,
        "message": message,
        "source": citation.source_block(),
    }


def run(
    profile: str = "default",
    *,
    now: Optional[datetime] = None,
    advance: bool = True,
) -> dict[str, Any]:
    """One watch pass. Cron advances seen_ids. A manual check passes advance=False.

    On API failure, state.seen_ids is left unchanged. A read-only check does not
    write seen_ids or last_count, so a guest's /subsidies cannot consume the weekly new list.
    """
    profile = profile_name(profile)
    state = load_state(profile)
    if state is None:
        raise SubsidiesError(
            f"No watch profile {profile!r}.",
            kind="not_found",
            next_step="Run `hermes jp-subsidies schedule` (or subsidies_watch_setup) first.",
        )
    criteria = state.get("criteria") or {}
    if not isinstance(criteria, dict) or not criteria.get("keyword"):
        raise SubsidiesError(
            "Watch state is missing criteria.keyword.",
            kind="internal_error",
            next_step="Run schedule again to recreate the profile.",
        )
    days = settings.deadline_days_from_state(state.get("deadline_days"))
    now = now or datetime.now(tz=timezone.utc)
    try:
        items, _meta = fetch_list(criteria)
    except SubsidiesError as error:
        if advance:
            state["last_error"] = {"at": now.isoformat(), "kind": error.kind, "message": str(error)}
            try:
                write_state(profile, state)
            except Exception:
                pass
        step = error.next_step or (
            "Fix the API/network issue and re-run; the baseline was not changed."
        )
        if error.kind == "timeout":
            step = (
                "The saved watch criteria were not changed. Retry this check later. "
                "Do not narrow the keyword; that would be a different search, not this watch."
            )
        raise SubsidiesError(
            f"jGrants watch failed for profile {profile!r}: {error}",
            kind=error.kind,
            next_step=step,
            status=error.status,
        ) from error

    drop_warning = ""
    prev_count = state.get("last_count")
    try:
        prev_n = int(prev_count) if prev_count is not None else None
    except (TypeError, ValueError):
        prev_n = None
    count_unverified = bool(prev_n is not None and prev_n > 0 and len(items) == 0)
    if count_unverified:
        drop_warning = (
            f"Warning: list API count dropped from {prev_n} to 0. "
            "This may be a bad filter or an API glitch — not treated as 'no new subsidies'. "
            "This warning repeats until a non-zero count returns."
        )

    seen = set(state.get("seen_ids") or [])
    current_ids = [str(it.get("id")) for it in items if it.get("id")]
    by_id = {str(it.get("id")): it for it in items if it.get("id")}
    if not state.get("baseline_at"):
        if advance:
            state.update(
                {
                    "seen_ids": current_ids,
                    "baseline_at": now.isoformat(),
                    "last_success_at": now.isoformat(),
                    "last_error": None,
                    "last_count": len(items),
                    "deadline_days": days,
                }
            )
            write_state(profile, state)
        message = render_message(
            profile=profile,
            criteria=criteria,
            baseline=True,
            new_items=[],
            due_items=upcoming(items, days, now=now),
            total=len(items),
            days=days,
            checked_at=now,
            count_drop_warning=drop_warning,
            count_unverified=count_unverified,
        )
        return {
            "ok": True,
            "baseline": True,
            "message": message,
            "source": citation.source_block(),
        }

    new_ids = [i for i in current_ids if i not in seen]
    new_items = [by_id[i] for i in new_ids]
    due_items = upcoming(items, days, now=now)
    # When count dropped to 0, do not merge/absorb and keep last_count so the warning repeats.
    if advance:
        if not count_unverified:
            state["seen_ids"] = sorted(set(seen) | set(current_ids))
            state["last_count"] = len(items)
        state["last_success_at"] = now.isoformat()
        state["last_error"] = None
        state["deadline_days"] = days
        write_state(profile, state)
    message = render_message(
        profile=profile,
        criteria=criteria,
        baseline=False,
        new_items=[] if count_unverified else new_items,
        due_items=due_items,
        total=len(items),
        days=days,
        checked_at=now,
        count_drop_warning=drop_warning,
        count_unverified=count_unverified,
    )
    return {
        "ok": True,
        "baseline": False,
        "new_count": 0 if count_unverified else len(new_items),
        "due_count": len(due_items),
        "count_unverified": count_unverified,
        "message": message,
        "source": citation.source_block(),
    }
