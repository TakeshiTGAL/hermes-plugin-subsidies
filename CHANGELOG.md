# Changelog

## 0.1.9

What was understood: company talk treated two katakana characters followed by kanji as a registry name, so フィボナッチ数列 and グラタン皿 received the subsidy paragraph. The README described the industry-text memory and did not name the hook `transform_tool_result`. Pytest before this change, on 0.1.8: 116 passed, 3 skipped, 2 warnings.

What changed: `pre_llm_call` adds the subsidy paragraph only on a subsidy question (補助金, 助成金, jGrants, or 募集) or when a subsidies tool was called in the previous turn. A company name does not add it. The decision is `turn_scope.domains`, copied into this plugin and not imported from another. The README names `transform_tool_result`. Pytest on this commit: 119 passed, 3 skipped.

## 0.1.8

What was understood: `pre_llm_call` added the subsidy paragraph on every turn, including a settings question. Pytest before this change, on 0.1.7: 114 passed, 3 skipped, 2 warnings.

What changed: that paragraph is added only on company talk or a subsidy question (補助金, 助成金, jGrants, 募集, or subsidies_). Other turns get no extra context. The paragraph text is unchanged. Pytest on this commit: 116 passed, 3 skipped, 2 warnings.

## 0.1.7

What was understood: a company name inside `business_summary` was blocked when a name field was also present. With no name field, the keyword トヨタ自動車 passed through the summary `トヨタ自動車は自動車を製造する。`. Pytest before this change, on 0.1.6: 113 passed, 3 skipped, 2 warnings.

What changed: the opening span before は is a company name when a shorter suffix of that span also appears after は, or when the span contains a legal form. トヨタ自動車 does not pass. 自動車 does. Pytest on this commit: 114 passed, 3 skipped, 2 warnings.

## 0.1.6

What was understood: `business_summary` was stored with the industry strings. A keyword passed when it was contained in that string, so a company name written inside the summary passed as an industry keyword. An industry list, and a keyword that merely contains the industry text, were already rejected. Pytest before this change, on 0.1.5: 112 passed, 3 skipped.

What changed: a company name, and the same name with the legal form removed, does not pass through `business_summary`. A keyword contained in an industry string still passes. A shorter industry word inside the summary still passes. Eligibility stays `not_claimed`. Pytest on this commit: 113 passed, 3 skipped.

## 0.1.5

What was understood: `industry_gate` kept `industry` only when the value was a string. jp-corporate returns `basic.industry` as a list, such as `["E 製造業"]`. The list was forgotten, so a keyword copied from that list was blocked with "No industry text was returned by a tool". A match also treated the industry string as contained in the keyword, so industry `自動車` let the company name `トヨタ自動車` through. Pytest on this commit: 112 passed, 3 skipped.

What changed: a list of industry strings is remembered. The keyword must be contained in the industry text. A company name does not pass when the industry text is contained in the company name. The block says industry text was not returned only when nothing was remembered. When industry text was remembered and the keyword is not that text, the block says the industry text was returned.

## 0.1.4

What was understood: `pre_llm_call` told the model to call `subsidies_search` only with industry text copied from a tool result. There was no `pre_tool_call`. On a small model, after no industry text was returned, `subsidies_search` ran with `keyword` set to the company name. The result was a subsidy list with count 0, and the reply said the filing-company sales could be confirmed with that tool. Pytest on this commit: 110 passed, 3 skipped.

What changed: `pre_tool_call` blocks `subsidies_search` unless `keyword` is industry text copied from a tool result in this session (`industry`, `company_industry`, `business_item`, `business_items`, or `business_summary`). A company name does not pass. `subsidies_detail` and `subsidies_watch_setup` are not blocked. The block says the search was not run, eligibility is `not_claimed`, no award was estimated, and sales are not in this tool. `transform_tool_result` remembers that text and returns no string.

## 0.1.3

What was understood: `text_match.eligibility` is already `not_claimed`, and the result has no award estimate. On a small model the skill body was not opened. `subsidies_search` and `subsidies_detail` were not called, and the reply said subsidies whose guideline text matches the industry had been fetched.

What changed: `pre_llm_call` tells the model to call `subsidies_search` only with industry text copied from a tool result. If no tool result has an industry, the model is told not to say a subsidy was fetched, not to estimate an award, and not to say the company is eligible. The tool result is unchanged.

## 0.1.2

What was understood: `subsidies_detail` returned the jGrants fields, including `subsidy_max_limit` 0, which the tool already refuses to treat as a proven ceiling. It did not compare a corporation's published industry, capital, employee count, or address with the call text, and it did not list a statute the call names.

What changed: `subsidies_detail` accepts those four optional attributes and returns `text_match`. A hit is a literal substring. `eligibility` is `not_claimed`. The result does not contain an award estimate. A cap is included only when `subsidy_max_limit` is greater than 0 (unit 円) or the detail text states 上限 with a unit as written (万円 is not converted). Named statutes are returned as `law_title` and `article` for a separate e-Gov lookup. An article number that is absent is not guessed. The skill tells the model not to call the company eligible.

## 0.1.1

Adds the `japan-subsidies` skill. It tells the model to find the jGrants tools with tool_search before web_search. It does not set requires_toolsets, so it stays in the model's skill list when Tool Search is left at its default, which hides this plugin's tools.

In `subsidies_search`, date separators, fullwidth digits, a prefecture written without 都, an uppercase sort, and limit written as 10.0 or fullwidth digits are accepted, and extra arguments, a limit outside the range, and an impossible date are refused with a fix and an example, and the caller's value is not copied into the message. A limit above the cap used to be reduced to the cap. Errors add `ok: false` and `error_type`. `subsidies_detail` and `subsidies_watch_setup` still ignore an argument they do not know.

The `subsidies_watch_setup` description says that it fetches a jGrants baseline and writes a weekly cron script under the Hermes home's `scripts/`.

The first sentence Tool Search shows now uses the words a person would ask with. A sentence that starts by describing the mechanism is not found, and the model falls through to the built-in web_search.
