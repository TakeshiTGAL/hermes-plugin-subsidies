"""jp-subsidies: jGrants subsidy search and weekly new/deadline watch for Hermes Agent."""

# Hermes imports this directory as a package. pytest's collector also imports this
# file on its own (the repo root is the package), where relative imports cannot work.
if __package__:
    from . import (  # noqa: F401
        citation,
        cli,
        client,
        cron_entry,
        enums,
        errors,
        schemas,
        tools,
        turn_scope,
        watch,
    )
else:
    import turn_scope  # noqa: F401


_SKILL_DESCRIPTION = (
    "Japan subsidies and jGrants grants. "
    "Before web_search, use tool_search to find subsidies_search and tool_call it "
    "only with industry text copied from a tool result. "
    "If none, pre_tool_call blocks the search. eligibility is not_claimed. "
    "Do not estimate an award or say the company is eligible. Sales are not in this tool."
)


def _subsidy_turn(user_message, conversation_history=None) -> bool:
    """A subsidy question, or a subsidies tool called in the previous turn."""
    return turn_scope.owns(user_message, conversation_history, "subsidy")


def _nudge_subsidies(user_message="", conversation_history=None, **_kwargs):
    if not _subsidy_turn(user_message, conversation_history):
        return None
    return {"context": (
        "Call subsidies_search only with industry text copied from a tool result. "
        "Do not call tool_describe. If no tool result contains an industry, do not call subsidies_search. "
        "pre_tool_call blocks a company name. eligibility is not_claimed. "
        "Do not estimate an award. Do not write 対象です. Sales are not in this tool. "
        "Do not write 取得しました or 確認できます unless the tool result contains that sentence."
    )}


def _remember_industry(tool_name="", result=None, session_id="", **_kwargs):
    from . import industry_gate
    industry_gate.remember(session_id or "", tool_name or "", result)
    return None


def _block_ungrounded_search(tool_name="", args=None, session_id="", **_kwargs):
    from . import industry_gate
    return industry_gate.block(
        tool_name or "",
        args if isinstance(args, dict) else {},
        session_id or "",
    )


def register(ctx):
    """Register tools, CLI, and slash command under the `subsidies` toolset."""
    from . import cli as _cli
    from . import schemas as _schemas
    from . import settings as _settings
    from . import tools as _tools

    getter = getattr(ctx, "get_config", None)
    _settings.bind_config(getter if callable(getter) else None)

    def read_settings() -> dict:
        out = {}
        for key in ("watch_deadline_days", "search_limit"):
            try:
                value = ctx.get_config(key) if callable(getter) else None
            except Exception:
                value = None
            if value not in (None, ""):
                out[key] = value
        return out

    _cli.set_settings_reader(read_settings)

    for schema in _schemas.ALL_SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="subsidies",
            schema=schema,
            handler=_tools.HANDLERS[schema["name"]],
            emoji="💴",
        )

    ctx.register_cli_command(
        name="jp-subsidies",
        help="jGrants subsidy search watch: schedule weekly new/deadline alerts",
        setup_fn=_cli.setup_parser,
        handler_fn=_cli.handle,
        description=(
            "Save a watch profile on the jGrants public API, write the weekly cron script, "
            "and print the `hermes cron create … --no-agent` line for new subsidies and "
            "upcoming deadlines."
        ),
    )
    for hook_name, callback in (
        ("pre_llm_call", _nudge_subsidies),
        ("transform_tool_result", _remember_industry),
        ("pre_tool_call", _block_ungrounded_search),
    ):
        try:
            ctx.register_hook(hook_name, callback)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "jp-subsidies %s hook unavailable", hook_name, exc_info=True,
            )
    ctx.register_command(
        "subsidies",
        _cli.slash,
        description="Read-only watch check",
        args_hint="[check] [profile]",
    )
    try:
        from pathlib import Path
        ctx.register_skill(
            "japan-subsidies",
            Path(__file__).resolve().parent / "skills" / "japan-subsidies" / "SKILL.md",
            description=_SKILL_DESCRIPTION,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning("jp-subsidies skill was not registered", exc_info=True)
