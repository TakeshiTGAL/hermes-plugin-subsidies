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
        watch,
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
    ctx.register_command(
        "subsidies",
        _cli.slash,
        description="Read-only watch check",
        args_hint="[check] [profile]",
    )
