# Changelog

## 0.1.1

Adds the `japan-subsidies` skill. It tells the model to find the jGrants tools with tool_search before web_search. It does not set requires_toolsets, so it stays in the model's skill list when Tool Search is left at its default, which hides this plugin's tools.

In `subsidies_search`, date separators, fullwidth digits, a prefecture written without 都, an uppercase sort, and limit written as 10.0 or fullwidth digits are accepted, and extra arguments, a limit outside the range, and an impossible date are refused with a fix and an example, and the caller's value is not copied into the message. A limit above the cap used to be reduced to the cap. Errors add `ok: false` and `error_type`. `subsidies_detail` and `subsidies_watch_setup` still ignore an argument they do not know.

The `subsidies_watch_setup` description says that it fetches a jGrants baseline and writes a weekly cron script under the Hermes home's `scripts/`.

The first sentence Tool Search shows now uses the words a person would ask with. A sentence that starts by describing the mechanism is not found, and the model falls through to the built-in web_search.
