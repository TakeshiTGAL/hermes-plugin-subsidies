---
name: japan-subsidies
description: "Japan subsidies and jGrants grants. Before web_search, use tool_search to find subsidies_search and tool_call it only with industry text copied from a tool result. If none, pre_tool_call blocks the search. eligibility is not_claimed. Do not estimate an award or say the company is eligible. Sales are not in this tool."
---

For this kind of request, use tool_search to find this plugin's tools before web_search, then call them with tool_call. Write tool_search queries in English (for example: "Japan subsidies").

Tools: `subsidies_search`, `subsidies_detail`, `subsidies_watch_setup`. Show the jGrants attribution the tool returns.

When the user asks which subsidies are open for one corporation, take `industry`, `capital_stock_jpy`, `employees`, and `location` from `jp_corp_profile` section `basic`. Do not invent them. If that profile call says the token is unset, say the company attributes are empty until the free gBizINFO token is set, and do not attach open calls to the company.

Call `subsidies_search` with `acceptance` 1 only when `keyword` is contained in industry text copied from a tool result, including a list of industry strings. A company name is not that text, including when the industry text is contained in the company name. `pre_tool_call` blocks the search, and the block says eligibility is `not_claimed`, no award was estimated, and sales are not in this tool. Then call `subsidies_detail` for each id that the search returned. Pass `company_industry`, `company_capital_jpy`, `company_employees`, and `company_location`.

Report `text_match.overlaps`. Where `matched` is false, say the call text does not contain that attribute (the tool's note is 要項の文言には無い). Where `matched` is true, quote the hit and the call's URL. Do not write that the company is eligible. Do not estimate an award. A cap is only `text_match.subsidy_cap` when `stated` is true, copied with its unit. `subsidy_max_limit` 0 is not a cap.

When `cited_statutes` has a `law_title` and an `article`, call `egov_law_article` and copy `source.url`. When `article` is null, say the call did not name an article and do not guess one.
