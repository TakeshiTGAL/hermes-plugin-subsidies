"""Literal overlap between published company attributes and a jGrants call.

A string that appears in both places is not an eligibility decision and not an award estimate.
Plugins do not call each other. The caller passes attribute strings it already has.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SPLIT = re.compile(r"[、,，/・\s]+")
_LAW = re.compile(
    r"(?P<title>[一-龥々〆ヵヶ]{2,40}法)"
    r"(?:第(?P<article>[0-9０-９]+)条(?:の(?P<branch>[0-9０-９]+))?)?"
)
_CAP = re.compile(
    r"上限(?:額)?[^0-9０-９]{0,12}(?P<amount>[0-9０-９,，]+)\s*(?P<unit>兆円|億円|万円|円)"
)
_SKIP_TITLES = frozenset({"法律", "本法", "同法", "当該法"})

_NOTE = (
    "A literal overlap is not a statement that this corporation is eligible. "
    "This tool does not estimate an award."
)
_ABSENT = "要項の文言には無い。"


def _plain(value: Any) -> str:
    text = _TAG.sub(" ", str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    return _WS.sub(" ", text).strip()


def _needles(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            text = str(value)
        else:
            text = str(int(value))
        return [text] if len(text) >= 2 else []
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            for needle in _needles(item):
                if needle not in found:
                    found.append(needle)
        return found
    text = _plain(value)
    if len(text) < 2:
        return []
    found = [text]
    for part in _SPLIT.split(text):
        part = part.strip()
        if len(part) >= 2 and part not in found:
            found.append(part)
    return found


def _overlap(attribute: str, needles: list[str], haystack: str) -> dict[str, Any]:
    hits = [needle for needle in needles if needle and needle in haystack]
    return {
        "attribute": attribute,
        "matched": bool(hits),
        "needles": needles,
        "hits": hits,
        "note": "The call text contains these characters." if hits else _ABSENT,
    }


def cited_statutes(plain: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for match in _LAW.finditer(plain or ""):
        title = match.group("title")
        if title in _SKIP_TITLES:
            continue
        article = match.group("article")
        branch = match.group("branch")
        article_text = None
        if article:
            article_text = article + (f"の{branch}" if branch else "")
        key = (title, article_text)
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"law_title": title, "raw": match.group(0)}
        if article_text:
            item["article"] = article_text
        else:
            item["article"] = None
            item["note"] = "The call text names this statute and no article number. Do not guess one."
        found.append(item)
    return found


def subsidy_cap(raw: dict[str, Any], plain: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    limit = raw.get("subsidy_max_limit")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0:
        entries.append({"source": "subsidy_max_limit", "amount": int(limit), "unit": "円"})
    for match in _CAP.finditer(plain or ""):
        entries.append({
            "source": "detail_text",
            "amount_text": match.group("amount"),
            "unit": match.group("unit"),
            "text": match.group(0).strip(),
        })
    return {"stated": bool(entries), "entries": entries}


def text_match(raw: dict[str, Any], company: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    company = company or {}
    parts = [
        raw.get("title"), raw.get("detail"), raw.get("industry"),
        raw.get("target_area_search"), raw.get("target_area_detail"),
        raw.get("target_number_of_employees"), raw.get("use_purpose"),
    ]
    plain = _plain(" ".join(str(part) for part in parts if part))
    overlaps = []
    if company.get("industry") not in (None, "", []):
        overlaps.append(_overlap("industry", _needles(company.get("industry")), plain))
    if company.get("capital_jpy") is not None:
        overlaps.append(_overlap("capital_jpy", _needles(company.get("capital_jpy")), plain))
    if company.get("employees") is not None:
        overlaps.append(_overlap("employees", _needles(company.get("employees")), plain))
    if company.get("location") not in (None, ""):
        overlaps.append(_overlap("location", _needles(company.get("location")), plain))
    return {
        "eligibility": "not_claimed",
        "note": _NOTE,
        "overlaps": overlaps,
        "subsidy_cap": subsidy_cap(raw, plain),
        "cited_statutes": cited_statutes(plain),
    }
