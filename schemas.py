"""Tool schemas: what the model reads to decide when and how to call each tool."""

SEARCH = {
    "name": "subsidies_search",
    "description": (
        "Search Japanese subsidies on jGrants (Jグランツ) via the public list API. "
        "Filter by keyword (required, 2–255 chars), optional region (target_area_search), "
        "industry, employee-size enum, use_purpose, and whether acceptance is open. "
        "industry / target_area_search / target_number_of_employees / use_purpose must be "
        "exact jGrants enum strings (e.g. industry '医療、福祉' with a Japanese comma — "
        "'医療・福祉' is rejected). A value that is not an enum is rejected before HTTP, "
        "so it is not shown as no matches. "
        "Optional deadline_from / deadline_to (ISO date or datetime) filter "
        "acceptance_end_datetime on the client — the list API has no deadline-range "
        "query parameter. A date-only deadline_to keeps the whole UTC calendar day "
        "(end 23:59:59.999999Z). Returns name/title, subsidy_max_limit, acceptance window, "
        "target area/employees, and institution_name when present. "
        "When subsidy_max_limit is 0, treat it as unset/unknown from the list API (not a proven "
        "¥0 ceiling) and open subsidies_detail. "
        "subsidy_rate and front_subsidy_detail_page_url are NOT in the list API; those "
        "fields are returned as null (not guessed). Use subsidies_detail for rate, URL, "
        "HTML detail, and attachment names. Always show 出典：J グランツ and the "
        "processed_note (jGrants Web-API terms 第5条1項二) from source."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": (
                    "Search keyword, 2–255 characters (required by jGrants). "
                    "Leading and trailing spaces are removed. Spaces inside the keyword are kept."
                ),
            },
            "target_area_search": {
                "type": "string",
                "description": (
                    "Optional area enum exactly as jGrants defines it, e.g. '東京都', '全国', "
                    "'近畿地方'. Wrong spellings are rejected before the HTTP call."
                ),
            },
            "industry": {
                "type": "string",
                "description": (
                    "Optional industry enum exactly as jGrants defines it, e.g. '製造業', "
                    "'医療、福祉' (ideographic comma 、). '医療・福祉' is rejected."
                ),
            },
            "target_number_of_employees": {
                "type": "string",
                "description": (
                    "Optional employee-size enum string exactly as jGrants defines it: "
                    "'従業員数の制約なし', '5名以下', '20名以下', '50名以下', '100名以下', "
                    "'300名以下', '900名以下', '901名以上'. Do not pass a bare number."
                ),
            },
            "use_purpose": {
                "type": "string",
                "description": (
                    "Optional purpose enum from jGrants (e.g. '人材育成を行いたい'). "
                    "Unknown values are rejected before the HTTP call."
                ),
            },
            "acceptance": {
                "type": "integer",
                "description": (
                    "1 = accepting applications only (default), 0 = no acceptance filter. "
                    "Other values are rejected (not coerced)."
                ),
            },
            "deadline_from": {
                "type": "string",
                "description": "Optional client-side filter: keep items with acceptance_end_datetime >= this.",
            },
            "deadline_to": {
                "type": "string",
                "description": (
                    "Optional client-side filter: keep items with acceptance_end_datetime <= this. "
                    "A date-only value (YYYY-MM-DD) means end of that UTC calendar day "
                    "(23:59:59.999999Z). Watch lines show the same instant in JST, so "
                    "T15:00:00Z is 00:00 JST on the next calendar day and still matches "
                    "deadline_to of the UTC date."
                ),
            },
            "sort": {
                "type": "string",
                "enum": ["created_date", "acceptance_start_datetime", "acceptance_end_datetime"],
                "description": "Sort field (default created_date).",
            },
            "order": {
                "type": "string",
                "enum": ["ASC", "DESC"],
                "description": "Sort order (default DESC).",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max rows after filters. Integers below 1 are rejected (0 is not changed to 1). "
                    "Values above the config cap are reduced to the cap and cannot raise it "
                    "(default 30, hard max 100)."
                ),
            },
        },
        "required": ["keyword"],
    },
}

DETAIL = {
    "name": "subsidies_detail",
    "description": (
        "Fetch one subsidy by id from the jGrants public detail API: purpose (use_purpose), "
        "industry, subsidy_rate, max limit, acceptance window, HTML detail text (may contain "
        "eligibility and expense notes — jGrants does not expose those as separate fields), "
        "front_subsidy_detail_page_url, and attachment file names "
        "(application_guidelines / outline_of_grant / application_form). "
        "Base64 file bodies are never returned in the tool JSON. "
        "When save_attachments=true, files are written under "
        "<HERMES_HOME>/plugin-data/jp-subsidies/files/<id>/ "
        "for this profile (via plugin_data_dir) only after Hermes' write guard allows it, "
        "and paths are returned. "
        "Missing API fields are null — never invented. Cite 出典：J グランツ and include "
        "source.processed_note (terms 第5条1項二)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subsidy_id": {
                "type": "string",
                "description": "Subsidy id from subsidies_search (≤18 characters).",
            },
            "save_attachments": {
                "type": "boolean",
                "description": (
                    "If true, decode and save attachment bytes via the Hermes write guard. "
                    "Names that match after ignoring letter case, or after Unicode NFC and NFD normalization, are one file; a collision saves nothing."
                ),
            },
        },
        "required": ["subsidy_id"],
    },
}

WATCH_SETUP = {
    "name": "subsidies_watch_setup",
    "description": (
        "Save a watch profile (keyword + optional area/industry/employees) under "
        "<HERMES_HOME>/plugin-data/jp-subsidies/ for this profile (via plugin_data_dir), take a baseline snapshot of matching subsidy IDs, "
        "write a weekly cron script under the same Hermes home's scripts/ "
        "(plugin_data_dir → parent.parent/scripts), and return the "
        "`hermes cron create … --no-agent` line. "
        "The first run reports only that the baseline was saved (it does not flood new "
        "items). Re-running setup with the same criteria keeps previous seen_ids unchanged "
        "(does not absorb the current list into seen, and does not claim first run); "
        "unnotified new ids stay reportable on the next successful check. "
        "Re-running with different criteria retakes a new baseline (discards old seen_ids) "
        "so prior matches are not flooded as new. "
        "Weekly cron scripts are written under the same Hermes home as plugin-data "
        "(via plugin_data_dir → parent.parent/scripts). "
        "Later cron runs list IDs that appeared since the last success and items "
        "whose acceptance_end_datetime is within watch_deadline_days (default 14). "
        "API failures are reported as failures and do not reset the baseline to empty. "
        "Default deliver is 'origin' (captured origin chat when present; bare CLI create "
        "may fall back to home or skip chat — prefer explicit platform:chat_id). "
        "'local' only writes under the Hermes home's cron/output/. "
        "The returned hermes cron create line always creates a new job; running it again "
        "does not replace an existing one. Profile names are stored unchanged (Japanese names rejected). "
        "A second name that differs only by letter case, such as Tokyo and tokyo, is refused before either the watch JSON or the cron script is written."
    ),

    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "Keyword for the weekly search (2–255 chars)."},
            "profile": {
                "type": "string",
                "description": (
                    "ASCII profile name, 1–64 letters, digits, dot, underscore, or hyphen "
                    "(default 'default'). Stored unchanged: Japanese names such as 東京 are "
                    "rejected and are not folded into default. Tokyo and tokyo are refused "
                    "as a second name before write. One cron script per profile."
                ),
            },
            "target_area_search": {"type": "string"},
            "industry": {"type": "string"},
            "target_number_of_employees": {"type": "string"},
            "use_purpose": {"type": "string"},
            "deadline_days": {
                "type": "integer",
                "description": (
                    "Days ahead for deadline alerts. Integers below 1 are rejected "
                    "(0 is not changed to 1). Values above the config cap are reduced to the cap "
                    "(default 14, hard max 30) and cannot raise it."
                ),
            },
            "deliver": {
                "type": "string",
                "description": (
                    "Hermes cron delivery target for the suggested command "
                    "(default 'origin'). origin delivers to the job's captured origin chat "
                    "when Hermes captured one; a bare CLI `hermes cron create` often has no "
                    "origin and then falls back to the home channel or skips chat — prefer "
                    "an explicit platform:chat_id from CLI. 'local' only saves under the "
                    "Hermes home's cron/output/ and does not send a chat notification. "
                    "Chat delivery goes through the Hermes gateway to the messaging platform. "
                    "bot-chat is this profile's Bot Chat and costs one model turn. "
                    "bot-chat:<profile> is a Hermes profile on this machine, not a chat id. "
                    "A missing profile is refused before the watch state and the script are written. "
                    "Schedule words (every, 5m, '0 9 * * 1') are rejected here; put them in when."
                ),
            },
            "when": {
                "type": "string",
                "description": (
                    "Schedule for the suggested command (default '0 9 * * 1', or '30m' / 'every 2h'). "
                    "Interpreted in Hermes' configured timezone, not necessarily JST. "
                    "A delivery target such as origin is rejected in this field."
                ),
            },
        },
        "required": ["keyword"],
    },
}

ALL_SCHEMAS = [SEARCH, DETAIL, WATCH_SETUP]
BY_NAME = {s["name"]: s for s in ALL_SCHEMAS}
