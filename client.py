"""HTTPS client for the jGrants public API (no key).

Host is fixed to api.jgrants-portal.go.jp. Official overview PDF states a call
limit of 10 requests / 1 second; this client enforces a process-wide minimum
interval so bursts stay under that ceiling.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .errors import SubsidiesError
from .version import __version__

API_HOST = "api.jgrants-portal.go.jp"
API_BASE = f"https://{API_HOST}/exp/v1/public"
USER_AGENT = f"hermes-plugin-subsidies/{__version__}"
TIMEOUT_SECONDS = 30
CALL_BUDGET_SECONDS = 60
RETRY_DELAY_SECONDS = 1.0
MAX_RETRIES = 2
MAX_RESPONSE_BYTES = 40 * 1024 * 1024  # detail payloads include base64 files
# Conservative vs the published 10/s ceiling (API利用概要.pdf).
MIN_REQUEST_INTERVAL_SECONDS = 0.15

_request_lock = threading.Lock()
_last_request_mono = 0.0
_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "subsidies_deadline", default=None
)


@contextlib.contextmanager
def budget(seconds: float = CALL_BUDGET_SECONDS):
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _remaining() -> Optional[float]:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def _out_of_time() -> SubsidiesError:
    return SubsidiesError(
        f"jGrants did not answer within {CALL_BUDGET_SECONDS} seconds.",
        kind="timeout",
        next_step=(
            "Retry later. Do not narrow the keyword; that would be a different search."
        ),
    )


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname != API_HOST:
            raise SubsidiesError(
                "jGrants redirected the request to another host or to plain HTTP; "
                "the plugin refused to follow it.",
                kind="http_error",
                next_step="Retry later. If it keeps happening, check the jGrants status.",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameHostRedirects)


def _throttle() -> None:
    global _last_request_mono
    with _request_lock:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_mono)
        if wait > 0:
            remaining = _remaining()
            if remaining is not None and remaining < wait:
                raise _out_of_time()
            time.sleep(wait)
        _last_request_mono = time.monotonic()


def _read_limited(response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise SubsidiesError(
                f"jGrants response exceeded the {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB size cap.",
                kind="bad_response",
                next_step="Retry without saving attachments, or ask for one subsidy at a time.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _http_get(path: str, params: Optional[dict[str, Any]] = None) -> tuple[int, dict]:
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v not in (None, "")},
        doseq=True,
    )
    url = API_BASE + path + (("?" + query) if query else "")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    timeout = TIMEOUT_SECONDS
    remaining = _remaining()
    if remaining is not None:
        if remaining < 1:
            raise _out_of_time()
        timeout = min(timeout, remaining)
    _throttle()
    try:
        with _opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            raw = _read_limited(response)
    except SubsidiesError:
        raise
    except urllib.error.HTTPError as error:
        body = b""
        with contextlib.suppress(Exception):
            body = error.read(MAX_RESPONSE_BYTES + 1)
        parsed = None
        if body:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # HTML or other non-JSON error pages must keep the HTTP status
                # and stay retryable. A parse failure is not a new error kind.
                parsed = None
        if isinstance(parsed, dict) and parsed.get("message"):
            raise SubsidiesError(
                f"jGrants rejected the request (HTTP {error.code}): {parsed.get('message')}",
                kind="bad_request" if error.code == 400 else "http_error",
                status=error.code,
                next_step=(
                    "Check keyword (2–255 characters), acceptance (0 or 1), sort/order, "
                    "and enum values for industry / employees / area."
                    if error.code == 400
                    else "Retry in a minute; if it keeps failing, the service may be busy."
                ),
            ) from None
        raise SubsidiesError(
            f"jGrants answered HTTP {error.code}.",
            kind="http_error",
            status=error.code,
            next_step="Retry in a minute; if it keeps failing, check the jGrants portal.",
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if _remaining() is not None and (_remaining() or 0) < 1:
            raise _out_of_time() from None
        raise SubsidiesError(
            f"Could not reach {API_HOST} ({error}).",
            kind="network_error",
            next_step="Check the network connection and try again.",
        ) from None
    data = _parse_json_body(raw)
    if not isinstance(data, dict):
        raise SubsidiesError(
            "jGrants returned a response that is not a JSON object.",
            kind="bad_response",
            next_step="Retry later.",
        )
    # HTTP 200 with {"message": "Bad request"} has been observed for some APIs;
    # jGrants uses HTTP 400 for that shape, but treat message-only bodies as errors.
    if "message" in data and "result" not in data and "metadata" not in data:
        raise SubsidiesError(
            f"jGrants returned an error body: {data.get('message')}",
            kind="bad_request",
            status=status if isinstance(status, int) else 200,
            next_step="Check the search parameters and retry.",
        )
    return int(status), data


def _parse_json_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SubsidiesError(
            "jGrants returned a response that is not JSON.",
            kind="bad_response",
            next_step="Retry later.",
        ) from error


def call(path: str, params: Optional[dict[str, Any]] = None) -> dict:
    """GET one public endpoint. Retries transient network/5xx failures."""
    last: Optional[SubsidiesError] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            _status, body = _http_get(path, params)
            return body
        except SubsidiesError as error:
            last = error
            retryable = error.kind in ("network_error", "http_error") and (
                error.status is None or error.status >= 500 or error.status == 429
            )
            if attempt < MAX_RETRIES and retryable:
                remaining = _remaining()
                if remaining is not None and remaining < RETRY_DELAY_SECONDS + 1:
                    raise
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise
    assert last is not None
    raise last


def search_subsidies(params: dict[str, Any]) -> dict:
    return call("/subsidies", params)


def get_subsidy(subsidy_id: str) -> dict:
    safe = urllib.parse.quote(subsidy_id, safe="")
    return call(f"/subsidies/id/{safe}")
