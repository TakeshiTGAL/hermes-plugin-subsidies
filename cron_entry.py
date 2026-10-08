"""Entry point for the script-only (no LLM) weekly cron job.

stdout is delivered verbatim by Hermes cron. On failure the message is printed
and the exit code is non-zero.
"""

from __future__ import annotations

import sys

from . import client
from .errors import SubsidiesError
from .watch import run


def main(profile: str = "default") -> int:
    try:
        # Same 60s call budget as the tools, the CLI, and /subsidies.
        # Without it, two retries of the 30s socket timeout run past 60s.
        with client.budget():
            result = run(profile)
    except SubsidiesError as exc:
        print(
            f"jp-subsidies weekly check failed (profile={profile}).\n"
            f"{exc}\n"
            f"（jp-subsidies 週次チェックに失敗しました。）"
        )
        if exc.next_step:
            print(exc.next_step)
        return 1
    print(result["message"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "default"))
