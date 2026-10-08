"""Writes the script-only Hermes cron job for a watch profile."""

from __future__ import annotations

import datetime as dt
import re
import shlex
from pathlib import Path

from .errors import SubsidiesError

# Hermes cron schedules: "30m", "every 2h", "every monday 9am", 5–6 field cron, ISO once.
_INTERVAL = re.compile(
    r"^\d+\s*(?:s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|wk|week|weeks)$",
    re.IGNORECASE,
)
_PERIOD_WORD = re.compile(
    r"^(?:every|hourly|daily|weekly|monthly|minutely|weekdays|weekends)$",
    re.IGNORECASE,
)
_CRON_TOKEN = re.compile(r"^[A-Za-z0-9*?,/-]+$")
_ISO_ONCE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")
# cli, cron, and api_server are gateway surfaces the scheduler does not deliver to.
# _CORE_SURFACE_NAMES is every platform either v0.21.4 or current main names, plus
# whatsapp_cloud (a home-channel env var, not a known delivery platform). A loaded
# plugin must not re-admit one of these when this process's known set omits it:
# the weekly script advances seen_ids before Hermes tries to deliver.
_NOT_DELIVERED = frozenset({"cli", "cron", "api_server"})
_CORE_SURFACE_NAMES = frozenset({
    "telegram",
    "discord",
    "slack",
    "whatsapp",
    "whatsapp_cloud",
    "signal",
    "bluebubbles",
    "email",
    "homeassistant",
    "mattermost",
    "matrix",
    "dingtalk",
    "feishu",
    "wecom",
    "wecom_callback",
    "weixin",
    "qqbot",
    "yuanbao",
    "webhook",
    "sms",
}) | _NOT_DELIVERED
# Names present on both v0.21.4 and current main. homeassistant is v0.21.4 only.
_FALLBACK_PLATFORMS = _CORE_SURFACE_NAMES - _NOT_DELIVERED - frozenset({
    "homeassistant",
    "whatsapp_cloud",
})
_DELIVER_EXACT = frozenset({"origin", "local"})
_ROUTING_TOKENS = frozenset({"all"})
_BOT_CHAT = "bot-chat"


def _core_platform_names() -> set[str]:
    """Built-in platforms this process's Hermes cron scheduler will deliver to.

    Reads ``cron.scheduler_delivery._KNOWN_DELIVERY_PLATFORMS`` when that module
    imports. Otherwise the names on both v0.21.4 and current main.
    """
    try:
        from cron.scheduler_delivery import _KNOWN_DELIVERY_PLATFORMS
    except Exception:
        return set(_FALLBACK_PLATFORMS)
    names: set[str] = set()
    for name in _KNOWN_DELIVERY_PLATFORMS:
        text = str(name).strip().lower()
        if text and text not in _NOT_DELIVERED:
            names.add(text)
    return names


def _deliverable_platform_names() -> set[str]:
    """Known delivery platforms, plus a loaded plugin that is not a core surface.

    ``chatwork:<id>`` is allowed when jp-chatwork registered that name. A core
    name this Hermes build does not list stays refused even if a plugin
    registered it, so an undeliverable target is rejected before any write.
    """
    extra = _extra_platform_names() - _CORE_SURFACE_NAMES
    return (_core_platform_names() | extra) - set(_NOT_DELIVERED)


def _extra_platform_names() -> set[str]:
    """Platforms a loaded plugin registered with Hermes. Empty when Hermes is not loaded."""
    try:
        from gateway.platform_registry import platform_registry
        names = platform_registry.registered_names()
    except Exception:
        return set()
    found: set[str] = set()
    for name in names:
        text = str(name).strip().lower()
        if text and all(ch.isalnum() or ch in "._-" for ch in text):
            found.add(text)
    return found


def _one_deliver(text: str) -> str | None:
    """One target: origin, local, all, bot-chat[:profile], or platform[:chat_id]."""
    if not text or any(ch.isspace() for ch in text) or "," in text:
        return None
    lower = text.lower()
    if lower in _DELIVER_EXACT or lower in _ROUTING_TOKENS:
        return lower
    if lower == _BOT_CHAT:
        return _BOT_CHAT
    if lower.startswith(_BOT_CHAT + ":"):
        chat_id = text.split(":", 1)[1].strip()
        if not chat_id or any(ch.isspace() for ch in chat_id):
            return None
        return f"{_BOT_CHAT}:{chat_id}"
    names = _deliverable_platform_names()
    platform, sep, chat = text.partition(":")
    key = platform.lower()
    if key in _NOT_DELIVERED or key not in names:
        return None
    if not sep:
        return key
    chat_id = chat.strip()
    if not chat_id:
        return None
    return f"{key}:{chat_id}"


def canonical_deliver(value: str) -> str | None:
    """Allowlist: origin, local, all, bot-chat[:profile], a platform, platform:chat_id.

    Comma-combines those (`origin,all`). A loaded plugin platform (`chatwork:id`)
    is allowed too. Schedule words (monday, 5, @daily, 9am, in 5m) are not targets.
    Returns the canonical string, or None when any part is not allowed.
    """
    text = (value or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(",")]
    if any(not part for part in parts):
        return None
    canon: list[str] = []
    for part in parts:
        one = _one_deliver(part)
        if one is None:
            return None
        canon.append(one)
    return ",".join(canon)


def looks_like_schedule(value: str) -> bool:
    """True when ``value`` is a period or cron expression, not a chat target."""
    text = (value or "").strip()
    if not text:
        return False
    if _PERIOD_WORD.fullmatch(text) or text.lower().startswith("every "):
        return True
    if _INTERVAL.fullmatch(text) or _ISO_ONCE.fullmatch(text):
        return True
    parts = text.split()
    return len(parts) >= 5 and all(_CRON_TOKEN.fullmatch(part) for part in parts)


def looks_like_deliver_target(value: str) -> bool:
    """True only for the deliver allowlist. Schedule words are not targets."""
    return canonical_deliver(value) is not None


def require_deliver(deliver: str) -> str:
    text = (deliver or "").strip()
    if not text:
        raise SubsidiesError(
            "deliver is empty.",
            kind="bad_request",
            next_step=(
                "Pass deliver as origin, local, all, bot-chat, bot-chat:<profile>, "
                "a platform name, platform:chat_id, or origin,all. "
                "bot-chat:<profile> is a Hermes profile on this machine, not a chat id. "
                "chatwork:<id> is accepted only when jp-chatwork is enabled."
            ),
        )
    canon = canonical_deliver(text)
    if canon is None:
        raise SubsidiesError(
            f"deliver {text!r} is not an allowed delivery target. "
            "Accepted: origin, local, all, bot-chat, bot-chat:<profile> "
            "(a Hermes profile on this machine, not a chat id), a Hermes platform, "
            "platform:chat_id, a comma combination of those (origin,all), "
            "or a platform a loaded plugin registered "
            "(chatwork:<id> when jp-chatwork is enabled).",
            kind="bad_request",
            next_step=(
                "Pass deliver as origin, local, all, bot-chat, bot-chat:<profile>, "
                "telegram:<chat_id>, or origin,all. "
                "bot-chat:<profile> is a profile name, not a chat id. "
                "chatwork:<id> is accepted only when jp-chatwork is enabled. "
                "Put periods such as monday, 5, @daily, 9am, or 'in 5m' in when."
            ),
        )
    return canon


def _local_profile_exists(name: str) -> bool | None:
    """True/False from Hermes ``profile_exists``. None when that API cannot be loaded."""
    try:
        from hermes_cli.profiles import normalize_profile_name, profile_exists
    except Exception:
        return None
    try:
        return bool(profile_exists(normalize_profile_name(name)))
    except Exception:
        return None


def require_reachable_bot_chat(deliver: str) -> None:
    """Refuse ``bot-chat:<profile>`` when that profile is not on this machine.

    The allowlist still accepts the shape (``bot-chat:desk``). Hermes treats the
    suffix as a local profile name and skips the target when it is missing.
    The weekly script advances seen ids before that delivery, so a missing
    profile is refused before state or the script is written. Bare ``bot-chat``
    is this profile and is not looked up.
    """
    missing: list[str] = []
    unknown = False
    for part in (deliver or "").split(","):
        text = part.strip()
        lower = text.lower()
        if not lower.startswith("bot-chat:"):
            continue
        name = text.split(":", 1)[1].strip()
        if not name:
            continue
        exists = _local_profile_exists(name)
        if exists is None:
            unknown = True
            missing.append(name)
        elif not exists:
            missing.append(name)
    if not missing:
        return
    shown = ", ".join(repr(name) for name in missing)
    why = (
        f"The profiles API could not be loaded, so the plugin cannot tell whether {shown} exists."
        if unknown
        else f"Hermes has no profile {shown} on this machine."
    )
    raise SubsidiesError(
        f"deliver {deliver!r} uses bot-chat:<profile>, which is a Hermes profile name, "
        f"not a chat id. {why} "
        "Hermes would skip that target after the weekly script advanced seen ids.",
        kind="bad_request",
        next_step=(
            "Use bot-chat for this profile, bot-chat:<profile> for a profile that exists "
            "on this machine, or telegram:<chat_id>. "
            "A missing profile is refused before the watch state and the script are written."
        ),
    )


def require_when(when: str) -> str:
    text = (when or "").strip()
    if not text:
        raise SubsidiesError(
            "when is empty.",
            kind="bad_request",
            next_step="Pass when as '0 9 * * 1', '30m', or 'every 2h'.",
        )
    if looks_like_deliver_target(text):
        raise SubsidiesError(
            f"when {text!r} looks like a delivery target, not a schedule.",
            kind="bad_request",
            next_step=(
                "Put origin, local, or platform:chat_id in deliver. "
                "Put the period in when (for example '0 9 * * 1')."
            ),
        )
    if not looks_like_schedule(text):
        raise SubsidiesError(
            f"when {text!r} is not a schedule.",
            kind="bad_request",
            next_step="Pass when as '0 9 * * 1', '30m', or 'every 2h'.",
        )
    return text

# Loads this plugin directory as a package (same technique Hermes uses), then
# calls cron_entry.main. Absolute `import watch` would break relative imports.
TEMPLATE = '''# Written by `hermes jp-subsidies schedule` on {date}. Weekly jGrants watch for profile {profile}.
# Safe to delete. Re-run schedule after moving the plugin.
import importlib.util
import sys
from pathlib import Path

_DIR = Path({plugin_dir!r})
_NAME = "jp_subsidies_runtime"
spec = importlib.util.spec_from_file_location(
    _NAME,
    _DIR / "__init__.py",
    submodule_search_locations=[str(_DIR)],
)
mod = importlib.util.module_from_spec(spec)
sys.modules[_NAME] = mod
spec.loader.exec_module(mod)
sys.exit(sys.modules[_NAME + ".cron_entry"].main({profile!r}))
'''


def script_name(profile: str) -> str:
    from .watch import profile_name

    return f"jp-subsidies-weekly-{profile_name(profile)}.py"


def write_script(plugin_dir: Path, profile: str) -> Path:
    """Write under Hermes ``scripts/`` derived from ``plugin_data_dir`` (same home as state)."""
    from . import save
    from .watch import profile_name

    scripts = save.scripts_dir()
    path = scripts / script_name(profile)
    body = TEMPLATE.format(
        date=dt.date.today().isoformat(),
        profile=profile_name(profile),
        plugin_dir=str(plugin_dir),
    )
    # Guard the target path before creating directories or writing.
    # The case check sits next to mkdir so a differently cased script cannot
    # appear between the two.
    save.assert_writable(path)
    save.refuse_case_variant(scripts, path.name, what="Cron script")
    scripts.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _profiles_unavailable(error: BaseException) -> SubsidiesError:
    return SubsidiesError(
        "The Hermes profiles API could not be loaded, so this plugin cannot tell "
        "which home would own the weekly job. Refusing before any watch state or "
        "script is written. A missing profiles API is not the default home.",
        kind="access_denied",
        next_step=(
            "Run schedule inside Hermes Agent, where the profiles API imports, then retry. "
            "Nothing was written."
        ),
    )


def _raw_profile_name() -> str:
    """Hermes' own label, including the ``custom`` sentinel for an unnamed home.

    Refuses when ``hermes_cli.profiles`` cannot be imported, when neither
    ``current_profile_name`` nor ``get_active_profile_name`` exists, or when
    that call raises. A loaded ``default`` or ``custom`` is returned unchanged
    so the printed line still omits ``-p``.
    """
    try:
        from hermes_cli import profiles as profiles_mod
    except Exception as error:
        raise _profiles_unavailable(error) from error
    try:
        current = getattr(profiles_mod, "current_profile_name", None)
        if current is not None:
            name = current("default")
        else:
            getter = getattr(profiles_mod, "get_active_profile_name", None)
            if getter is None:
                raise AttributeError(
                    "current_profile_name and get_active_profile_name are missing"
                )
            name = getter()
    except SubsidiesError:
        raise
    except Exception as error:
        raise _profiles_unavailable(error) from error
    return str(name or "").strip() or "default"


def require_profiles_api() -> str:
    """Fail closed before setup when this home cannot be named."""
    return _raw_profile_name()


def hermes_profile_name() -> str:
    """Name for a printed ``hermes -p``, or ``default`` when this home has none.

    Stable v0.21.4 exposes ``get_active_profile_name`` (read from ``HERMES_HOME``)
    and does not define ``current_profile_name``. ``custom`` means the home is not
    a named profile, so there is no ``-p`` value to print. If the profiles API
    cannot be loaded, this raises. It does not pretend the home is ``default``.
    """
    text = _raw_profile_name()
    if text in {"", "default", "custom"}:
        return "default"
    return text


def unnamed_home_note() -> str:
    """Warn when Hermes calls this home ``custom`` and the printed line has no ``-p``."""
    if _raw_profile_name() != "custom":
        return ""
    return (
        "This Hermes home is not a named profile. The line above has no -p. "
        "Run it in this same home, or the job is created in the default home."
    )


def hermes_cron_invocation(action: str, *tail: str) -> str:
    """``hermes [-p <profile>] cron <action> ...`` for the current Hermes profile."""
    parts = ["hermes"]
    profile = hermes_profile_name()
    if profile != "default":
        parts.extend(["-p", shlex.quote(profile)])
    parts.extend(["cron", action])
    parts.extend(shlex.quote(part) for part in tail)
    return " ".join(parts)


def cron_command(script: Path, profile: str, deliver: str, when: str) -> str:
    from .watch import profile_name

    return hermes_cron_invocation(
        "create",
        when,
        "--no-agent",
        "--script",
        script.name,
        "--deliver",
        deliver,
        "--name",
        f"jGrants weekly: {profile_name(profile)}",
    )
