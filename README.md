# jp-subsidies: subsidy search and weekly watch, built on the jGrants public API

Find Japanese subsidies that match a small business, sole proprietor, or professional practice, and get a **weekly** alert for **new matches** and **deadlines within N days** — built on the **jGrants public API** (no API key). The product name is jp-subsidies. `--no-agent` formatting uses no model. Delivery to `bot-chat` is one model turn: the agent follows the fetched text, and that turn costs a model call.

```
You:    東京で人材系の補助金を募集中だけ見たい
        (Tokyo — open calls about “人材” / human resources)
Hermes: subsidies_search(keyword="人材", target_area_search="東京都", acceptance=1)
        → titles, subsidy_max_limit (0 often means unset — see detail), acceptance windows…
          (rate and detail URL are absent from the list → null)
        subsidies_detail(subsidy_id="…") → rate, portal URL, attachment names
```

[日本語の説明は下にあります](#日本語)

## Install

```bash
hermes plugins install TakeshiTGAL/hermes-plugin-subsidies --enable
```

Once listed in the Hermes catalog, `hermes plugins install jp-subsidies --enable` works too. For a local clone: `hermes plugins install "file://$PWD" --enable`.

No API key. Requires Hermes Agent **0.21.4** or later (`manifest_version: 2`).

## Tools, CLI, and slash command

| Surface | What it does |
|---|---|
| `subsidies_search` | Keyword search plus optional area / industry / employee-size / purpose enums / acceptance. Optional `deadline_from` / `deadline_to` filter **client-side**. |
| `subsidies_detail` | One id → rate, portal URL, HTML `detail`, attachment **names**. Optional `save_attachments` writes under `<HERMES_HOME>/plugin-data/jp-subsidies/files/<id>/` for this profile (via `plugin_data_dir`) through Hermes' write guard. |
| `subsidies_watch_setup` | Saves criteria + baseline IDs under `<HERMES_HOME>/plugin-data/jp-subsidies/` for this profile, writes a weekly cron script, prints `hermes cron create … --no-agent`. |
| CLI `hermes jp-subsidies schedule\|check` | Same watch setup / one-shot check without a model turn. Not registered when Hermes `plugins.isolation` is `host` (tools and `/subsidies` still are). |
| Slash `/subsidies [check] [profile]` | Reads the saved watch (calls the jGrants list API) and does **not** advance `seen_ids` or `last_count`. A schedule or delivery target is refused, including `/subsidies check 5m` and `/subsidies check every`. |

### Enum filters (do not invent spellings)

`industry`, `target_area_search`, `target_number_of_employees`, and `use_purpose` must match jGrants enum strings exactly. Wrong values (e.g. industry `医療・福祉` instead of `医療、福祉`) are **rejected before the HTTP call**, so a bad value is not shown as no matches.

### What the list API does **not** return

The list endpoint does not include `subsidy_rate` or `front_subsidy_detail_page_url`. The plugin returns those fields as `null` in search results and does **not** invent portal URLs. Call `subsidies_detail`.

### Weekly watch (Hermes-specific)

```bash
hermes jp-subsidies schedule --keyword 設備 --watch osaka
# --watch is the watch name. It is not -p and not --profile.
# A Hermes profile named osaka is not required. The watch is written in the current home.
# If the current Hermes profile is not default, the printed cron line includes -p <name>.
# If the profiles API cannot be loaded, raises, or no longer has the active-profile function, schedule refuses before writing state or the script. It does not print a line for the default home.
# default --deliver origin  (use telegram/… if you prefer; 'local' does not notify chat)
# then run the printed: hermes cron create '0 9 * * 1' --no-agent --script …
```

- **First successful setup** stores every matching id as a baseline and says so; it does **not** dump them all as “new”.
- **Re-running setup with the same criteria** keeps previous `seen_ids` unchanged (does **not** absorb the current list into seen) and says it is a re-setup (not a first run). Ids that appeared since the last successful check remain reportable on the next check.
- **Re-running setup with different criteria** retakes a new baseline (discards old `seen_ids`) so prior matches are not flooded as “new” next week. If the **first** setup, or a **criteria change**, sees **0** list rows, it warns that a later non-zero list would all look new. A **same-criteria re-setup** that sees 0 rows keeps the previous `seen_ids`. When the previous `last_count` was greater than 0, that count is kept and the drop warning is shown (the next check still treats new items and deadlines as unverified). A kept non-empty seen set is not called an empty baseline. If the saved seen set is already empty, the message gives the same empty-baseline warning as the first run.
- Profile names are stored unchanged (`1–64` ASCII letters, digits, `.`, `_`, `-`). A second name that differs only by letter case (`Tokyo` and `tokyo`) is refused before either the watch JSON or the cron script is written, including when only the cron script is left. `東京` and `大阪` are rejected; they are not both saved as `default`. If Hermes' profiles API cannot be loaded, raises, or no longer exposes the active-profile function, schedule refuses before it writes the watch state or the cron script. That failure is not treated as the default home, and no cron line is printed.
- Keywords may contain spaces (`人材 育成` is sent as typed). Spaces inside the keyword are kept and not rejected. Leading and trailing spaces are stripped.
- A date-only `deadline_to` (`YYYY-MM-DD`) keeps that **UTC** calendar day through `23:59:59.999999Z`. Weekly lines show the same instant in **JST**. An API end of `T15:00:00.000Z` is `00:00` JST on the next calendar day, and it still matches `deadline_to` of the UTC date.
- **Later cron runs** list ids that were not in the seen set, and items whose `acceptance_end_datetime` falls within `watch_deadline_days` (default 14, config max 30). Only the cron script advances `seen_ids` and `last_count`. `hermes jp-subsidies check` and `/subsidies` show the same text and leave that state unchanged, so a manual check does not consume the weekly new list. A manual check does not advance seen. Integers below 1 are rejected (`0` is not changed to `1`). Values above the config cap are reduced to the cap and saved on the watch state. Cron uses that saved `deadline_days`; it does not re-read config (the cron process has no plugin ctx). Deadlines in the weekly text are shown in **JST**.
- The suggested cron expression `0 9 * * 1` is interpreted in **Hermes' configured timezone**, not necessarily Monday 09:00 JST.
- If `metadata.resultset.count` is missing or ≠ the number of `result` rows, the call **fails** (not treated as empty). If the count drops from N>0 to 0 after a prior success, the message includes a **warning**, shows new items and deadlines as **unverified** (not “0 new” and not “0 deadlines”), and **repeats the warning** until a non-zero count returns.
- **API/network failure** is reported as a failure. Seen ids are **not** replaced with an empty set.
- Formatting with `--no-agent` uses **no model**. Delivery to `bot-chat` is one model turn on the fetched text and costs a model call. If chat delivery fails after the weekly script succeeds, seen ids have already advanced and the weekly text remains under the Hermes home's `cron/output/`. Default deliver is **`origin`**: Hermes sends to the job’s captured origin chat when one exists (typical if you create the cron from a chat/agent turn). A bare CLI `hermes cron create` line often has **no** origin — then Hermes falls back to the configured **home** channel, or **skips chat delivery** if none is set. `origin` does not promise a chat message. Prefer an explicit target (e.g. `telegram:<chat_id>`) when scheduling from CLI. `--deliver` accepts `origin`, `local`, `all`, `bot-chat` (this profile's Bot Chat) or `bot-chat:<profile>` (a Hermes profile on this machine, not a chat id), a platform name this Hermes build's cron scheduler can deliver to (`telegram`), `platform:chat_id`, a comma combination of those (`origin,all`), and, when jp-chatwork is enabled, a platform that plugin registered (`chatwork:<id>`). A name this build cannot deliver, and a `bot-chat:<profile>` whose profile is not on this machine, are refused before the watch state and the script are written. `cli`, `cron`, and `api_server` are rejected. Words such as `monday`, `5`, `@daily`, `9am`, `in 5m`, `at 9am`, and `*` are rejected. The allowlist reads Hermes' non-public `gateway.platform_registry.registered_names()`. `--deliver local` only writes under the Hermes home’s `cron/output/` and does **not** notify chat. Any chat delivery needs the Hermes gateway (`hermes cron status`) and reaches the messaging platform through Hermes (not this plugin). `--when zzz` and an empty `when` are refused before state or the script is written. A `when` that looks like a delivery target is refused and belongs in `--deliver`.
- **Removing the plugin does not remove** the cron job, scripts under the Hermes home’s `scripts/`, or watch/attachment data under `<HERMES_HOME>/plugin-data/jp-subsidies/` (per profile). Stop with `hermes cron remove <job id>` (`hermes cron list`), then delete those files.
- There is **no per-day request quota** in this plugin (only the published 10/s ceiling and the in-process ≥0.15s gap). Unattended cron can call jGrants every week without a human. Cron scripts are ordinary Python on your machine — not an adversarial sandbox.
- Agent-started path: `subsidies_watch_setup` / `hermes jp-subsidies schedule` can be started from chat; they write state + script and print a `hermes cron create` line for you (or the agent) to run. That line **always creates a new job**. It does not look up or replace an existing one; running it twice delivers the same weekly check twice. Remove the old id with `hermes cron list` then `hermes cron remove <job id>` before running the line again. Slash `/subsidies` and `hermes jp-subsidies check` call the list API and do not advance `seen_ids` or `last_count`. They do not accept a schedule or a delivery target, including `/subsidies check 5m` and `/subsidies check every` (those are periods, not profile names).
- `metadata.resultset.count` matching the number of rows means this response was not truncated in transit. It does **not** prove the server returned every subsidy that matches the keyword (a server-side cap would not be visible here).
- Who can call the tools: the subsidies toolset is enabled on every platform by default, including gateways. Anyone a gateway admits can call these tools. There is no API key; those calls use the public jGrants API and can write this profile's watch state and saved files. For example, while `CHATWORK_ALLOWED_USERS` is unset, everyone in a room listed in `CHATWORK_ROOMS` can talk to the bot and therefore call these tools. Turn the toolset off per platform with `hermes tools`. `/subsidies` is outside that switch. If `allow_admin_from` is unset, anyone a gateway admits can run it. Limit it with `allow_admin_from`, `user_allowed_commands`, or `group_user_allowed_commands` (an empty DM list can fall through to the room list), or disable the plugin. A manual check is read-only and does not change the weekly seen set. CLI use is the person running Hermes. With `plugins.isolation: host`, `hermes jp-subsidies` is not registered; the three tools and `/subsidies` still are.
- Disk records are watch criteria, subsidy ids, timestamps, cron scripts, and attachment bytes you asked to save. The plugin does **not** record the speaker's name or user id. The Hermes session transcript still contains the tool text.
- `tests/` is shipped with the repo and is **not** loaded by `register()`.

## Credit and terms

Per [jGrants Web-API 利用規約](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf) 第5条1項一, every reply includes:

**出典：J グランツ**

Search, detail, and weekly text are curated/structured outputs, so every tool reply also includes the 第5条1項二 **processed_note**. Do not present edited text as if the government authored it.

Published call limit ([API利用概要](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E6%A6%82%E8%A6%81.pdf)): **10 requests / 1 second**. This plugin spaces live GETs by ≥0.15s in-process. Attachment PDFs/forms may carry third-party rights (terms 第5条2項); the plugin saves bytes you asked for but does not republish their text.

Open API landing page: <https://www.jgrants-portal.go.jp/open-api>. Spec: <https://developers.digital.go.jp/documents/jgrants/api/>.

## Security and privacy

- HTTPS only to `api.jgrants-portal.go.jp` (same-host HTTPS redirects only).
- No API key and no telemetry.
- One call waits at most 60 seconds, including the weekly cron script, retries a transient failure at most twice, and refuses a response over 40 MiB.
- `search_limit` and `watch_deadline_days` caps cannot be raised by tool arguments above config. Integers below 1 are rejected (`0` is not changed to `1`).
- Disk writes go under `<HERMES_HOME>/plugin-data/jp-subsidies/` for this profile via `plugins.plugin_storage.plugin_data_dir` (follows Hermes `get_hermes_home()`, including profile / `HERMES_DATA_DIR_SUFFIX` / Windows). Cron scripts go under the same home’s `scripts/` (`plugin_data_dir(…).parent.parent / "scripts"`). If `plugin_data_dir` cannot be loaded, the plugin refuses. It does not fall back to `HERMES_HOME` or `~/.hermes`. Hermes `agent.file_safety` write guard is required before mkdir/write.
- Subsidy ids and file names are sanitized before building paths. File names that match after ignoring letter case (`File.PDF` and `file.pdf`) or after Unicode NFC/NFD normalization (`が.pdf`) are one file. Subsidy ids that differ only by letter case are one directory. Either collision refuses the save before any write.

## What was not checked

- A live gateway with `allow_admin_from` set was not used to send `/subsidies`. Who can run the command is taken from Hermes slash rules.
- With `plugins.isolation: host`, a tool was not called through to the end inside the child process. Validate shows that the CLI is skipped and that the three tools and `/subsidies` remain.
- A configured `search_limit` of 100 was not confirmed against a live API body of exactly 100 rows.
- With jp-chatwork actually loaded, delivery of `chatwork:<id>` into a chat was not confirmed. Tests only swap in a registered name and check the allowlist.
- A weekly message was not sent to a real chat, and a real cron job was not watched until it fired and was received.
- Subsidy-rate text was not compared with a grant-guidelines PDF. The portal page is empty HTML, and that page's DOM is not used as a source.
- The detail v2 path was not called.
- Whether the server applies a hidden cap once a keyword has several hundred rows was not checked. A count that matches the rows does not prove there is no such cap.
- The 0.15 second gap was not measured between separate processes. The gap applies inside one process only.

## Reference

API parameter shapes and credit wording were cross-checked against the MIT-licensed [digital-go-jp/jgrants-mcp-server](https://github.com/digital-go-jp/jgrants-mcp-server) (`Copyright (c) 2025 Digital Agency, Government of Japan`). See `NOTICE`. This plugin is not that MCP server; it is a Hermes Agent plugin with cron watch on top of the same public API.

## License

MIT. See `LICENSE` and `NOTICE`.

---

## 日本語

中小企業・個人事業主・士業向けに、**jGrants の公開 API** で補助金を探し、**週1回**で「新着」と「締切が近いもの」を届ける Hermes プラグインです。製品名は jp-subsidies で、名乗りは built on the jGrants public API です。`--no-agent` の整形はモデルを使いません。`bot-chat` への配信は、外から取った文に従うモデルの1ターンで、費用がかかります。

- 検索: キーワード（必須・2文字以上。途中のスペースは拒否しない。前後の空白だけ削る）、地域・業種・従業員数・利用目的は **所定の文言のみ**（誤ると HTTP の前に拒否する。無該当とは見せない）。日付だけの `deadline_to` は UTC のその日の終わり。週次の表示は JST
- 詳細: 補助率・ポータル URL・HTML 詳細・添付ファイル名。保存はプロファイルごとの `<HERMES_HOME>/plugin-data/jp-subsidies/`。添付名は大文字小文字と Unicode の NFC/NFD を同一視し、衝突したら書く前に全部拒否する。補助金 ID の大文字小文字だけが違う場合も、別フォルダには書かず拒否する
- 週次: 初回は基準保存のみ。同じ条件の再設定は seen_ids を残す。一覧が0件で前の件数が1以上ならその件数を残して警告する。保存済みの seen が0件の再設定は、初回と同じ「基準は空」の注意を出す。条件を変えた再設定は基準を取り直す。件数が前回より 0 に落ちたら、新着も締切も unverified（0件とは書かない）。プロファイル名は英数字と . _ -（`東京` は `default` にしない）。`Tokyo` と `tokyo` は、監視 JSON と cron スクリプトのどちらも書く前に拒否する（スクリプトだけ残っているときも含む）。`deadline_days` の 0 は拒否。cron は setup で保存した日数を使い、設定は読み直さない。配信先は `origin`・`local`・`all`・`bot-chat`・`bot-chat:<profile>`（チャット id ではなく、この機械の Hermes プロファイル名）・この Hermes が cron で届けられるプラットフォーム名・`platform:chat_id`・それらのカンマ結合（`origin,all`）と、jp-chatwork を有効にしたときの面（`chatwork:<id>`）です。届けられない名前と、この機械に無いプロファイルは、状態とスクリプトを書く前に拒否します。照合は `gateway.platform_registry.registered_names()` を読みます。`cli`・`cron`・`api_server` と、`monday` や `5` や `@daily` は拒否します。監視の名前は `--watch` です（`-p` と `--profile` は使いません）。Hermes のプロファイルが default でなければ、印刷する cron 行に `-p` が付きます。プロファイル API が読めないとき、例外のとき、関数名が変わったときは、default として行を出さず、状態とスクリプトを書く前に拒否します。`--when zzz` と空の when は、状態とスクリプトを書く前に拒否します。`/subsidies` と `hermes jp-subsidies check` は読むだけで、`seen_ids` と `last_count` は進めません（進めるのは cron だけ）。`origin` や `local` は配信先なので拒否します。`check 5m` と `check every` は周期として拒否する（プロファイル名としては探さない）
- 配信の既定は `origin`（チャットから cron を作ると origin に届くことが多い。CLI だけの create では origin が無く home に落ちるか、未設定なら通知しないことがある。確実なら `telegram:…` など明示）。`local` は Hermes home の `cron/output/` のみで通知しません。cron の配信が失敗しても、週次スクリプトが成功したあとなら seen は既に進んでおり、本文は Hermes home の `cron/output/` に残ります。印刷した `hermes cron create` は毎回新しいジョブです（同じ行を二度実行すると二通届く）。`0 9 * * 1` は Hermes のタイムゾーンです（JST とは限りません）
- subsidies の道具は既定でゲートウェイを含む全部の面で有効です。`CHATWORK_ALLOWED_USERS` が無いとき、`CHATWORK_ROOMS` に書いた部屋の全員がこの道具を呼べます（API キーは無く、公開 API と、このプロファイルの監視状態を書けます）。面ごとに止めるには `hermes tools`。`/subsidies` はその切り替えの外です。`allow_admin_from` が未設定なら、ゲートウェイに入れる人は誰でも使えます。絞るには `allow_admin_from`・`user_allowed_commands`・`group_user_allowed_commands`（DM の一覧が空だと部屋の一覧に落ちることがある）、またはプラグインを無効にします。手動の check は読むだけで、週次の seen は変えません。`plugins.isolation: host` では `hermes jp-subsidies` は登録されません。ツールと `/subsidies` は残ります。話した人の名前と ID は記録しません

表示するときは必ず **出典：J グランツ** と加工注記を付けてください（API利用規約 第5条）。

確かめていないこと: 実ゲートウェイに `allow_admin_from` を付けて `/subsidies` を送ったこと、`plugins.isolation: host` の子プロセスで道具を最後まで呼んだこと、`search_limit` が100の実応答、jp-chatwork を読み込んだ状態の `chatwork:<id>` の到着、実在のチャットへの週次と本物の cron の受信、補助率と交付要綱 PDF の照合、詳細の v2 パス、キーワードが数百件のときのサーバ側の見えない上限、別プロセスのあいだの 0.15 秒です。件数の一致はその上限が無い証明ではありません。0.15 秒はプロセスの中だけです。ポータル画面の DOM は一次資料にしていません。
