"""Tool handlers. Each returns a JSON string and never raises."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import citation, client, enums, save, schedule, settings, watch
from .errors import SubsidiesError

logger = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).resolve().parent

MISSING = None  # explicit null for fields the list API does not return
DEFAULT_DELIVER = "origin"


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _guard(handler: Callable[[dict], dict]):
    def wrapped(args: Optional[dict] = None, **kwargs) -> str:
        try:
            with client.budget():
                return _dump(handler(args or {}))
        except SubsidiesError as error:
            return _dump(error.to_dict())
        except Exception as error:  # never crash the agent turn
            logger.exception("jp-subsidies tool failed")
            return _dump(
                {
                    "error": f"Unexpected error in jp-subsidies ({type(error).__name__}: {error}).",
                    "kind": "internal_error",
                    "next_step": "Retry with simpler arguments; if it persists, report the tool name and args.",
                }
            )

    wrapped.__name__ = handler.__name__
    wrapped.__doc__ = handler.__doc__
    return wrapped


def _parse_bound(value: Any, *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse an ISO date/datetime. Date-only values use 00:00:00 UTC, or 23:59:59.999999
    UTC when ``end_of_day`` is True (so ``deadline_to=YYYY-MM-DD`` keeps that calendar day).
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    date_only = len(text) == 10 and text[4] == "-" and text[7] == "-"
    if date_only:
        text = text + ("T23:59:59.999999+00:00" if end_of_day else "T00:00:00+00:00")
    dt = watch.parse_dt(text)
    if dt is None:
        raise SubsidiesError(
            "Could not parse that date. Use YYYY-MM-DD, for example 2026-10-01, "
            "or a datetime such as 2026-10-01T00:00:00Z. Slashes are accepted on a date.",
            kind="bad_request",
            next_step="Pass deadline_from or deadline_to as 2026-10-01.",
        )
    return dt


def _format_list_item(raw: dict[str, Any]) -> dict[str, Any]:
    limit = raw.get("subsidy_max_limit")
    item = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "title": raw.get("title"),
        "subsidy_max_limit": limit,
        "subsidy_rate": MISSING,
        "acceptance_start_datetime": raw.get("acceptance_start_datetime"),
        "acceptance_end_datetime": raw.get("acceptance_end_datetime"),
        "target_area_search": raw.get("target_area_search"),
        "target_number_of_employees": raw.get("target_number_of_employees"),
        "institution_name": raw.get("institution_name"),
        "front_subsidy_detail_page_url": MISSING,
        "missing_from_list_api": ["subsidy_rate", "front_subsidy_detail_page_url"],
    }
    if limit == 0:
        item["subsidy_max_limit_note"] = (
            "List API returned 0. That often means unset/unknown in jGrants, not a proven "
            "¥0 ceiling. Check subsidies_detail (HTML detail may name real amounts)."
        )
    return item


_SEARCH_KEYS = {
    "keyword", "target_area_search", "industry", "target_number_of_employees",
    "use_purpose", "acceptance", "deadline_from", "deadline_to", "sort", "order", "limit",
}
_EXTRA = (
    "Only keyword, target_area_search, industry, target_number_of_employees, use_purpose, "
    "acceptance, deadline_from, deadline_to, sort, order, and limit are accepted. "
    "Remove any other argument. Example: keyword 人材 and limit 10."
)
_AREA = (
    "target_area_search must be a jGrants area, such as 東京都 or 全国. "
    "Add 都, 府, or 県 when the name needs it, for example 東京都."
)
_DATE = (
    "deadline_from and deadline_to must be a calendar date, for example 2026-10-01. "
    "Slashes are accepted. A month or day outside the calendar is refused."
)
_LIMIT = (
    "limit must be a whole number from 1 up to the configured cap, for example 1. "
    "A value outside that range is refused and is not reduced to the cap."
)
_SORTS = {"created_date", "acceptance_start_datetime", "acceptance_end_datetime"}


def _whole_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        folded = unicodedata.normalize("NFKC", value).strip()
        if re.fullmatch(r"[0-9]+", folded):
            return int(folded)
        if re.fullmatch(r"[0-9]+\.0+", folded):
            return int(folded.split(".", 1)[0])
    return None


def _area_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    if text in enums.AREA_VALUES:
        return text
    for full in enums.AREA_VALUES:
        if full.endswith(("都", "府", "県")) and text == full[:-1]:
            return full
    raise SubsidiesError(_AREA, kind="bad_request")


def _calendar_day(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = unicodedata.normalize("NFKC", str(value)).strip().replace("/", "-").replace(".", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match is None:
        return value
    year, month, day = (int(match.group(index)) for index in range(1, 4))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise SubsidiesError(_DATE, kind="bad_request")
    return f"{year:04d}-{month:02d}-{day:02d}"


def _search_limit(value: Any) -> int:
    cap = settings.search_limit(None)
    if value is None:
        return cap
    number = _whole_number(value)
    if number is None or number < 1 or number > cap:
        raise SubsidiesError(_LIMIT, kind="bad_request")
    return number


def _prepare_search(args: dict) -> dict:
    if any(key not in _SEARCH_KEYS for key in args):
        raise SubsidiesError(_EXTRA, kind="bad_request")
    clean = dict(args)
    area = clean.get("target_area_search")
    if area not in (None, ""):
        clean["target_area_search"] = _area_name(str(area))
    sort = clean.get("sort")
    if isinstance(sort, str) and sort.strip().lower() in _SORTS:
        clean["sort"] = sort.strip().lower()
    if "deadline_from" in clean:
        clean["deadline_from"] = _calendar_day(clean.get("deadline_from"))
    if "deadline_to" in clean:
        clean["deadline_to"] = _calendar_day(clean.get("deadline_to"))
    return clean


@_guard
def subsidies_search(args: dict) -> dict:
    # Validate enums and deadline bounds before any live HTTP call.
    prepared = _prepare_search(args)
    criteria = watch.normalize_criteria(prepared)
    d_from = _parse_bound(prepared.get("deadline_from"), end_of_day=False)
    d_to = _parse_bound(prepared.get("deadline_to"), end_of_day=True)
    limit = _search_limit(prepared.get("limit") if "limit" in prepared else None)

    result, meta_count = watch.fetch_list(criteria)

    filtered = []
    for item in result:
        if not isinstance(item, dict):
            continue
        end = watch.parse_dt(item.get("acceptance_end_datetime"))
        if d_from is not None and (end is None or end < d_from):
            continue
        if d_to is not None and (end is None or end > d_to):
            continue
        filtered.append(item)

    sliced = filtered[:limit]
    return {
        "count_api": meta_count,
        "count_returned": len(sliced),
        "count_after_deadline_filter": len(filtered),
        "deadline_filter": {
            "deadline_from": args.get("deadline_from"),
            "deadline_to": args.get("deadline_to"),
            "applied_client_side": bool(d_from or d_to),
            "note": (
                "jGrants list API has no deadline-range query parameter; "
                "deadline_from/to filter acceptance_end_datetime after the response. "
                "A date-only deadline_to keeps the whole UTC calendar day (23:59:59.999999Z)."
            ),
        },
        "query": {k: v for k, v in watch.api_params(criteria).items() if v not in (None, "")},
        "subsidies": [_format_list_item(it) for it in sliced],
        "source": citation.source_block(),
    }


def _attachment_names(detail: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in ("application_guidelines", "outline_of_grant", "application_form"):
        items = detail.get(key) or []
        names = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
        out[key] = names
    return out


@_guard
def subsidies_detail(args: dict) -> dict:
    subsidy_id = str(args.get("subsidy_id") or "").strip()
    sid = save.sanitize_id(subsidy_id)
    body = client.get_subsidy(sid)
    result = body.get("result")
    if not isinstance(result, list) or not result:
        raise SubsidiesError(
            f"No subsidy found for id {sid!r}.",
            kind="not_found",
            next_step=(
                "Confirm the id from subsidies_search; jGrants answers HTTP 200 with an "
                "empty result list for missing ids (this tool maps that to kind=not_found)."
            ),
            # Upstream returns HTTP 200 with empty result — do not imply the API sent 404.
            status=200,
        )
    raw = result[0]
    if not isinstance(raw, dict):
        raise SubsidiesError(
            "jGrants detail result was not an object.",
            kind="bad_response",
            next_step="Retry later.",
        )
    save_flag = bool(args.get("save_attachments"))
    saved = save.save_attachments(sid, raw) if save_flag else None
    out = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "title": raw.get("title"),
        "subsidy_catch_phrase": raw.get("subsidy_catch_phrase"),
        "detail": raw.get("detail"),
        "use_purpose": raw.get("use_purpose"),
        "industry": raw.get("industry"),
        "target_area_search": raw.get("target_area_search"),
        "target_area_detail": raw.get("target_area_detail"),
        "target_number_of_employees": raw.get("target_number_of_employees"),
        "subsidy_rate": raw.get("subsidy_rate"),
        "subsidy_max_limit": raw.get("subsidy_max_limit"),
        "acceptance_start_datetime": raw.get("acceptance_start_datetime"),
        "acceptance_end_datetime": raw.get("acceptance_end_datetime"),
        "project_end_deadline": raw.get("project_end_deadline"),
        "front_subsidy_detail_page_url": raw.get("front_subsidy_detail_page_url"),
        "institution_name": raw.get("institution_name"),
        "request_reception_presence": raw.get("request_reception_presence"),
        "is_enable_multiple_request": raw.get("is_enable_multiple_request"),
        "attachment_names": _attachment_names(raw),
        "structured_fields_note": (
            "jGrants does not provide separate fields for 対象経費 or 申請要件; "
            "those topics, when present, appear inside the HTML `detail` field."
        ),
        "attachments_saved": saved,
        "source": citation.source_block(),
    }
    if out["subsidy_max_limit"] == 0:
        out["subsidy_max_limit_note"] = (
            "API returned 0 for subsidy_max_limit. That often means unset/unknown in jGrants, "
            "not a proven ¥0 ceiling. Read the HTML detail field for named amounts when present."
        )
    return out


@_guard
def subsidies_watch_setup(args: dict) -> dict:
    profile = watch.profile_name(str(args.get("profile") or "default"))
    deliver = schedule.require_deliver(str(args.get("deliver") or DEFAULT_DELIVER))
    schedule.require_reachable_bot_chat(deliver)
    raw_when = args.get("when")
    when = schedule.require_when("0 9 * * 1" if raw_when is None else str(raw_when))
    schedule.require_profiles_api()
    script_path = save.scripts_dir() / schedule.script_name(profile)
    save.assert_writable(script_path)
    state_file = watch.state_path(profile)
    existed = watch.exact_named_file(state_file)
    try:
        result = watch.setup({**args, "profile": profile})
        script = schedule.write_script(PLUGIN_DIR, result["profile"])
    except SubsidiesError:
        watch.discard_state_if_created(profile, existed)
        raise
    cmd = schedule.cron_command(script, result["profile"], deliver, when)
    result["cron_script"] = str(script)
    result["cron_command"] = cmd
    result["deliver"] = deliver
    result["hint"] = (
        "Run the cron_command once. The --no-agent script formats the text with no model. "
        "Delivery to bot-chat is one model turn on that fetched text and costs a model call. "
        "bot-chat:<profile> is a Hermes profile on this machine, not a chat id. "
        "A missing profile is refused before the watch state and the script are written. "
        "Default deliver is 'origin': Hermes delivers to the job's captured origin chat when "
        "one exists (typical if you create the cron from a chat/agent turn). A bare CLI "
        "`hermes cron create` line often has no origin — then Hermes falls back to the "
        "configured home channel, or skips chat delivery if none is set. Prefer an explicit "
        "target (e.g. telegram:<chat_id>) when scheduling from CLI. "
        "'local' only writes under the Hermes home's cron/output/ and never notifies chat. "
        "Gateway must be running for any chat delivery. "
        f"Test with: {schedule.hermes_cron_invocation('run', '<job id>')}. "
        "The printed create line always creates a new job. It does not replace "
        "one with the same name; running it again delivers the same weekly check twice. "
        f"List jobs with {schedule.hermes_cron_invocation('list')} and remove the old id with "
        f"{schedule.hermes_cron_invocation('remove', '<job id>')} before "
        "running the line again. The when expression is interpreted in Hermes' configured "
        "timezone, which is not necessarily JST."
    )
    home_note = schedule.unnamed_home_note()
    if home_note:
        result["hint"] = result["hint"] + " " + home_note
    return result


HANDLERS = {
    "subsidies_search": subsidies_search,
    "subsidies_detail": subsidies_detail,
    "subsidies_watch_setup": subsidies_watch_setup,
}
