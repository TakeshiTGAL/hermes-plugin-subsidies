"""Block a subsidy search whose keyword was not copied from a tool result.

Process-local. Other plugins are not imported. The hook arguments are the
public ones: tool_name, result, args, session_id.
"""

from __future__ import annotations

import json
from typing import Any

_SKIP = frozenset({"tool_search", "tool_describe", "tool_call"})
_INDUSTRY_KEYS = frozenset({
    "industry",
    "company_industry",
    "business_item",
    "business_items",
})
_NAME_KEYS = frozenset({"name", "company_name", "name_en", "kana"})
_LEGAL_FORMS = (
    "株式会社",
    "有限会社",
    "合同会社",
    "合名会社",
    "合資会社",
    "(株)",
    "（株）",
    "㈱",
    "(有)",
    "（有）",
)
_MAX = 40

_industries: dict[str, list[str]] = {}
_summaries: dict[str, list[str]] = {}
_names: dict[str, list[str]] = {}


def clear(session_id: str | None = None) -> None:
    if session_id is None:
        _industries.clear()
        _summaries.clear()
        _names.clear()
        return
    _industries.pop(session_id, None)
    _summaries.pop(session_id, None)
    _names.pop(session_id, None)


def _parse(result: Any) -> Any:
    if isinstance(result, str):
        text = result.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return result


def _take_text(item: Any, found: list[str]) -> None:
    """Keep industry strings, including a list such as jp-corporate's basic.industry."""
    if isinstance(item, str):
        text = item.strip()
        if text:
            found.append(text)
    elif isinstance(item, list):
        for part in item:
            _take_text(part, found)


def _collect(value: Any, industries: list[str], summaries: list[str], names: list[str]) -> None:
    if isinstance(value, dict):
        summary = value.get("business_summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(summary.strip())
        if any(key in value for key in ("business_summary", "industry", "corporate_number")):
            for key in _NAME_KEYS:
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    names.append(item.strip())
        for key, item in value.items():
            if key in _INDUSTRY_KEYS:
                _take_text(item, industries)
            elif key != "business_summary":
                _collect(item, industries, summaries, names)
    elif isinstance(value, list):
        for item in value:
            _collect(item, industries, summaries, names)


def _keep(bucket: list[str], texts: list[str]) -> None:
    for text in texts:
        if text not in bucket:
            bucket.append(text)
    del bucket[:-_MAX]


def remember(session_id: str, tool_name: str, result: Any) -> None:
    if tool_name in _SKIP:
        return
    industries: list[str] = []
    summaries: list[str] = []
    names: list[str] = []
    _collect(_parse(result), industries, summaries, names)
    sid = session_id or ""
    if industries:
        _keep(_industries.setdefault(sid, []), industries)
    if summaries:
        _keep(_summaries.setdefault(sid, []), summaries)
    if names:
        _keep(_names.setdefault(sid, []), names)


def _bare_name(name: str) -> str:
    text = name.strip()
    for token in _LEGAL_FORMS:
        text = text.replace(token, "")
    return "".join(text.split())


def _summary_company_names(summary: str) -> list[str]:
    """The opening span of a summary is a company name when it is not the industry word.

    ``トヨタ自動車は自動車を製造する。`` names トヨタ自動車. 自動車 still matches the summary.
    A legal form in that span is enough on its own. This does not read another plugin.
    """
    for sep in ("は", "が"):
        index = summary.find(sep)
        if index <= 0:
            continue
        head = summary[:index].strip()
        tail = summary[index + len(sep):]
        if any(form in head for form in _LEGAL_FORMS):
            return [head]
        if len(head) < 4:
            return []
        for suffix_len in range(2, len(head) - 1):
            prefix = head[:-suffix_len]
            suffix = head[-suffix_len:]
            if len(prefix) >= 2 and suffix in tail:
                return [head]
        return []
    return []


def _company_keyword(needle: str, names: list[str]) -> bool:
    """True when the keyword is a company name, not a shorter industry word inside it."""
    for name in names:
        if needle == name.strip():
            return True
        bare = _bare_name(name)
        if bare and needle == bare:
            return True
    return False


def _matches(keyword: str, industries: list[str], summaries: list[str], names: list[str]) -> bool:
    """The keyword must be copied from the industry text.

    ``item in needle`` is rejected: industry ``自動車`` would otherwise let the
    company name ``トヨタ自動車`` through. A company name that appears inside
    ``business_summary`` is not an industry keyword.
    """
    needle = keyword.strip()
    if len(needle) < 2:
        return False
    if any(needle in item for item in industries):
        return True
    summary_names: list[str] = []
    for summary in summaries:
        for name in _summary_company_names(summary):
            if name not in summary_names:
                summary_names.append(name)
    if _company_keyword(needle, list(names) + summary_names):
        return False
    return any(needle in item for item in summaries)


_BLOCK_NONE = (
    "No industry text was returned by a tool, so subsidies_search was not run. "
    "A company name is not an industry keyword. eligibility is not_claimed. "
    "No award was estimated. Sales are not in this tool. "
    "When a tool result contains industry text, call subsidies_search with that text only."
)

_BLOCK_MISMATCH = (
    "Industry text was returned by a tool, but the keyword is not that text, "
    "so subsidies_search was not run. "
    "A company name does not pass, including when the industry text is contained in the company name. "
    "eligibility is not_claimed. No award was estimated. Sales are not in this tool."
)


def block(tool_name: str, args: dict, session_id: str) -> dict | None:
    if tool_name != "subsidies_search":
        return None
    keyword = str((args or {}).get("keyword") or "")
    sid = session_id or ""
    industries = list(_industries.get(sid, []))
    summaries = list(_summaries.get(sid, []))
    names = list(_names.get(sid, []))
    if _matches(keyword, industries, summaries, names):
        return None
    message = _BLOCK_NONE if not industries and not summaries else _BLOCK_MISMATCH
    return {"action": "block", "message": message}
