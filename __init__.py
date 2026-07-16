"""trace-saver plugin — one-click save of Hermes session traces to the leaderboard.

Registers:

* ``save_trace`` tool (toolset ``trace``) — the agent can call it to upload
  the current/any session trace to the Trace Leaderboard.
* ``/save-trace`` slash command — the user can trigger the same from a session.

Both route through :mod:`uploader`, which zips the session file(s) and POSTs
the zip to ``$TRACE_LEADERBOARD_URL`` (default ``http://10.9.66.12:8848``).
Each upload = +1 on the board.

Config (env):
  TRACE_LEADERBOARD_URL   base URL of the leaderboard
  TRACE_LEADERBOARD_NAME  board display name (default: system user)
"""

from __future__ import annotations

import os
import sys

# The plugin loader imports this as ``hermes_plugins.trace_saver`` with the
# plugin dir on ``__path__``, so a relative import works. When run standalone
# (tests), fall back to putting our own dir on sys.path.
try:
    from . import uploader  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - standalone / test path
    sys.path.insert(0, os.path.dirname(__file__))
    import uploader  # type: ignore[no-redef]


SAVE_TRACE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_trace",
        "description": (
            "Package a Hermes session trace into a .zip and upload it to the "
            "Trace Leaderboard (each upload scores +1). Use this to archive/share "
            "the current session's trajectory. Defaults to the most recent session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": (
                        "Which trace to save: 'latest' (default, most recent "
                        "session), 'all' (every session in one zip), or a "
                        "session id / filename substring."
                    ),
                    "default": "latest",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Leaderboard display name. Defaults to $TRACE_LEADERBOARD_NAME "
                        "or the system user."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _check_available() -> bool:
    """Tool stays visible but only dispatches when a sessions dir exists."""
    try:
        return uploader.sessions_dir().is_dir()
    except Exception:
        return False


def _do_save(session: str = "latest", name=None) -> dict:
    return uploader.save_trace(session=session or "latest", name=name)


def _handle_tool(args: dict, **_kw) -> str:
    from tools.registry import tool_error, tool_result

    session = str((args or {}).get("session") or "latest").strip()
    name = (args or {}).get("name")
    try:
        return tool_result(_do_save(session=session, name=name))
    except (FileNotFoundError, ValueError) as exc:
        return tool_error(str(exc))
    except ConnectionError as exc:
        return tool_error(f"Leaderboard unreachable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"save_trace failed: {type(exc).__name__}: {exc}")


_HELP = (
    "/save-trace — upload a Hermes trace to the leaderboard\n"
    "  /save-trace                 save the latest session\n"
    "  /save-trace all             save every session in one zip\n"
    "  /save-trace <session-id>    save a specific session\n"
    "  /save-trace latest <name>   override the leaderboard name\n"
    f"  board: {uploader.leaderboard_url()}   name: {uploader.default_name()}"
)


def _handle_slash(raw_args: str):
    argv = (raw_args or "").split()
    if argv and argv[0] in ("help", "-h", "--help"):
        return _HELP

    session = argv[0] if len(argv) >= 1 else "latest"
    name = argv[1] if len(argv) >= 2 else None
    try:
        res = _do_save(session=session, name=name)
        return f"📤 {res['message']}"
    except (FileNotFoundError, ValueError) as exc:
        return f"⚠️  {exc}"
    except ConnectionError as exc:
        return f"⚠️  Leaderboard unreachable: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"⚠️  save-trace failed: {type(exc).__name__}: {exc}"


def register(ctx) -> None:
    """Called once by the Hermes plugin loader."""
    ctx.register_tool(
        name="save_trace",
        toolset="trace",
        schema=SAVE_TRACE_SCHEMA,
        handler=_handle_tool,
        check_fn=_check_available,
        emoji="📤",
        description="Save/upload a Hermes session trace to the Trace Leaderboard.",
    )
    ctx.register_command(
        "save-trace",
        handler=_handle_slash,
        description="Zip a Hermes session trace and upload it to the leaderboard (+1).",
        args_hint="[latest|all|<session-id>] [name]",
    )
