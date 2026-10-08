"""Save jGrants attachment bytes and resolve plugin data paths via Hermes storage."""

from __future__ import annotations

import base64
import contextlib
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from .errors import SubsidiesError

PLUGIN_NAME = "jp-subsidies"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u3040-\u30ff\u4e00-\u9fff()-]+")
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _storage_unavailable(error: Exception) -> SubsidiesError:
    return SubsidiesError(
        "plugins.plugin_storage.plugin_data_dir is unavailable; refusing to read or write plugin data.",
        kind="access_denied",
        next_step=(
            "Run inside Hermes Agent so plugins.plugin_storage.plugin_data_dir works, then retry. "
            "This plugin does not write to HERMES_HOME or ~/.hermes as a fallback."
        ),
    )


def _require_storage_api():
    """Import ``plugin_data_dir`` without calling it (the call mkdir's)."""
    try:
        from plugins.plugin_storage import plugin_data_dir
    except Exception as error:
        raise _storage_unavailable(error) from error
    return plugin_data_dir


def _home_path() -> Path:
    """Home ``plugin_data_dir`` would use, via the same ``get_hermes_home()`` helper.

    Resolved here so the write guard can run before ``plugin_data_dir`` creates
    ``plugin-data/<name>``. If that helper cannot be loaded, refuse. This does
    not read ``HERMES_HOME`` or ``~/.hermes`` itself.
    """
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception as error:
        raise _storage_unavailable(error) from error


def plugin_root() -> Path:
    """``<hermes home>/plugin-data/jp-subsidies`` without creating it.

    ``plugins.plugin_storage.plugin_data_dir`` follows ``get_hermes_home()`` and
    mkdir's on every call. This returns that path only. ``ensure_data_dir``
    calls ``plugin_data_dir`` after the write guard allows the directory.
    If either API cannot be loaded, refuse. Does not fall back to ``HERMES_HOME``
    or ``~/.hermes``.
    """
    _require_storage_api()
    return _home_path() / "plugin-data" / PLUGIN_NAME


def _discard_empty_data_dir(path: Path) -> None:
    """Remove an empty data directory ``plugin_data_dir`` just created, and an empty parent."""
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
        parent = path.parent
        if parent.name == "plugin-data" and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def ensure_data_dir() -> Path:
    """Create the data directory via ``plugin_data_dir`` only after the guard allows it."""
    predicted = plugin_root()
    assert_writable(predicted)
    plugin_data_dir = _require_storage_api()
    try:
        created = Path(plugin_data_dir(PLUGIN_NAME))
    except Exception as error:
        raise _storage_unavailable(error) from error
    if created.resolve() != predicted.resolve():
        try:
            assert_writable(created)
        except SubsidiesError:
            _discard_empty_data_dir(created)
            raise
    return created


def scripts_dir() -> Path:
    """``<hermes home>/scripts`` — same Hermes home as ``plugin_root()``.

    Derived as ``plugin_root().parent.parent / "scripts"`` so cron scripts land in
    the profile/home that Hermes cron resolves via ``get_hermes_home()/scripts``.
    Does not call ``plugin_data_dir``, so a later write refusal leaves no
    ``plugin-data/jp-subsidies`` directory behind.
    """
    return plugin_root().parent.parent / "scripts"


def assert_writable(path: Path) -> None:
    """Fail closed: refuse when agent.file_safety is missing or denies the path."""
    try:
        from agent.file_safety import get_write_denied_error
    except Exception as error:
        raise SubsidiesError(
            "Hermes write guard (agent.file_safety) is unavailable; refusing to write.",
            kind="access_denied",
            next_step=(
                "Run inside Hermes Agent so agent.file_safety is importable, "
                f"then retry under a path Hermes allows (e.g. {path})."
            ),
        ) from error
    message = get_write_denied_error(str(path))
    if message:
        raise SubsidiesError(
            f"Write denied by Hermes: {message}",
            kind="access_denied",
            next_step=(
                f"Denied path was {path}. Write under plugin-data/{PLUGIN_NAME}/ "
                "(via plugin_data_dir) or the Hermes home's scripts/, or adjust HERMES_WRITE_SAFE_ROOT."
            ),
        )


def sanitize_id(subsidy_id: str) -> str:
    text = (subsidy_id or "").strip()
    if not text or len(text) > 18:
        raise SubsidiesError(
            "subsidy_id must be a non-empty string of at most 18 characters.",
            kind="bad_request",
            next_step="Pass the id from subsidies_search (e.g. a0W…).",
        )
    cleaned = _SAFE_ID.sub("", text)
    if cleaned != text or ".." in text or "/" in text or "\\" in text:
        raise SubsidiesError(
            "subsidy_id contains characters that are not safe for a file path.",
            kind="bad_request",
            next_step="Use the id string returned by the jGrants API without path segments.",
        )
    return cleaned


def sanitize_filename(name: str) -> str:
    base = Path(str(name or "file")).name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:180]


def write_bytes_atomic(path: Path, data: bytes) -> None:
    assert_writable(path)
    # Case check and directory creation stay together so a differently cased
    # subsidy-id directory cannot appear between the two.
    _refuse_subsidy_id_directory_collision(path.parent, path.parent.name)
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".att-", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def save_attachments(subsidy_id: str, detail: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Decode base64 attachment lists from a detail result and write them to disk."""
    sid = sanitize_id(subsidy_id)
    root = plugin_root() / "files" / sid
    rows: dict[str, list[dict[str, Any]]] = {}
    pending: list[tuple[str, str, bytes]] = []
    for key in ("application_guidelines", "outline_of_grant", "application_form"):
        items = detail.get(key) or []
        if not isinstance(items, list):
            continue
        out: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = sanitize_filename(str(item.get("name") or f"{key}_{index}"))
            data_b64 = item.get("data")
            if not data_b64:
                out.append({"name": name, "saved": False, "reason": "no data field"})
                continue
            try:
                raw = base64.b64decode(data_b64, validate=False)
            except Exception as error:
                raise SubsidiesError(
                    f"Attachment {name!r} was not valid base64.",
                    kind="bad_response",
                    next_step="Retry the detail call; if it persists, open the portal page instead.",
                ) from error
            if len(raw) > MAX_ATTACHMENT_BYTES:
                raise SubsidiesError(
                    f"Attachment {name!r} exceeds the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB cap.",
                    kind="bad_response",
                    next_step="Open the file from the jGrants portal instead of saving it here.",
                )
            pending.append((key, name, raw))
            out.append({"name": name, "bytes": len(raw), "saved": False, "write": True})
        if out:
            rows[key] = out
    _refuse_attachment_collisions(root, [name for _key, name, _raw in pending])
    written: dict[tuple[str, str], Path] = {}
    for key, name, raw in pending:
        path = root / name
        write_bytes_atomic(path, raw)
        written[(key, name)] = path
    saved: dict[str, list[dict[str, Any]]] = {}
    for key, out in rows.items():
        for row in out:
            if not row.pop("write", False):
                continue
            path = written[(key, row["name"])]
            row["saved"] = True
            row["path"] = str(path)
        saved[key] = out
    return saved


def _name_key(name: str) -> str:
    """Identity for one path: Unicode NFC, then case-insensitive."""
    return unicodedata.normalize("NFC", name).casefold()


def _refuse_attachment_collisions(root: Path, names: list[str]) -> None:
    """Fail before any write when sanitized names collide or a file already exists.

    Letter case is ignored, and Unicode NFC/NFD forms are the same name.
    ``File.PDF`` and ``file.pdf``, or NFC and NFD ``が.pdf``, are one path on
    macOS, so this plugin refuses those pairs on every disk.
    """
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(_name_key(name), []).append(name)
    existing: set[str] = set()
    if root.is_dir():
        existing = {_name_key(child.name) for child in root.iterdir() if child.is_file()}
    for name in names:
        key = _name_key(name)
        group = groups[key]
        if len(group) > 1 or key in existing or (root / name).exists():
            if len(group) > 1:
                count_sentence = (
                    f"{len(group)} attachment names collide. "
                    "None of the attachments in this call were saved."
                )
            else:
                count_sentence = "None of the attachments in this call were saved."
            raise SubsidiesError(
                f"Attachment file name {name!r} collides with another file. "
                "Names that match after ignoring letter case, or after Unicode "
                "NFC and NFD normalization, count as the same file. "
                "Refusing to overwrite.",
                kind="bad_response",
                next_step=(
                    "Use a distinct attachment name, or delete the existing file "
                    f"under {root}, then retry. {count_sentence}"
                ),
            )


def refuse_case_variant(directory: Path, name: str, *, what: str) -> None:
    """Refuse before write when ``directory/name`` differs only by letter case.

    ``Tokyo.json`` and ``tokyo.json`` are one file on a case-insensitive disk,
    so this plugin refuses that pair on every disk.
    """
    if not directory.is_dir():
        return
    key = name.casefold()
    for child in directory.iterdir():
        if child.name.casefold() == key and child.name != name:
            raise SubsidiesError(
                f"{what} {name!r} collides with existing {child.name!r}. "
                "Names that match after ignoring letter case count as the same file. "
                "Refusing to overwrite.",
                kind="bad_request",
                next_step=(
                    "Use the existing name exactly, or delete the existing file "
                    f"under {directory}, then retry. Nothing new was written for {name!r}."
                ),
            )


def _refuse_subsidy_id_directory_collision(root: Path, sid: str) -> None:
    """Fail before any write when another directory differs only by id case."""
    parent = root.parent
    if not parent.is_dir():
        return
    key = sid.casefold()
    for child in parent.iterdir():
        if child.is_dir() and child.name.casefold() == key and child.name != sid:
            raise SubsidiesError(
                f"Subsidy id {sid!r} collides with existing directory {child.name!r}. "
                "Ids that match after ignoring letter case count as the same directory. "
                "Refusing to write.",
                kind="bad_response",
                next_step=(
                    "Use the id string exactly as the jGrants API returned it. "
                    f"If {child.name!r} was created for a different case of this id, "
                    f"delete that directory under {parent}, then retry. "
                    "None of the attachments in this call were saved."
                ),
            )
