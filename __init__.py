"""trace-saver plugin — one-click save of Hermes session traces.

Registers:

* ``save_trace`` tool (toolset ``trace``) — the agent can call it to package
  a Hermes session trace and either upload it to the leaderboard or keep it
  as a local .zip.
* ``/save-trace`` slash command — the user can trigger the same from a session.

Two modes:

* **upload** (default) — zip → POST to ``$TRACE_LEADERBOARD_URL``
  (default ``http://10.9.66.12:8848``). Scores +1 on the board.
* **local**  — zip → write to ``$TRACE_SAVE_DIR`` (default ``~/hermes-traces``)
  or a caller-supplied directory. Nothing uploaded, nothing scored.

Config (env):
  TRACE_LEADERBOARD_URL   base URL of the leaderboard
  TRACE_LEADERBOARD_NAME  board display name (default: system user)
  TRACE_SAVE_DIR          local-mode output dir (default: ~/hermes-traces)
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
            "Package a Hermes session trace into a .zip. By default uploads "
            "it to the Trace Leaderboard (each upload = +1). Set local=true "
            "to keep the .zip on disk without uploading."
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
                        "Leaderboard / archive display name. Defaults to "
                        "$TRACE_LEADERBOARD_NAME or the system user."
                    ),
                },
                "local": {
                    "type": "boolean",
                    "description": (
                        "If true, only save the .zip locally (do not upload). "
                        "Default false."
                    ),
                    "default": False,
                },
                "out_dir": {
                    "type": "string",
                    "description": (
                        "When local=true, directory to write the .zip into. "
                        "Defaults to $TRACE_SAVE_DIR or ~/hermes-traces."
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


def _coerce_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _do_save(session: str = "latest", name=None, local: bool = False,
             out_dir=None) -> dict:
    return uploader.save_trace(
        session=session or "latest",
        name=name,
        local=local,
        out_dir=out_dir,
    )


def _handle_tool(args: dict, **_kw) -> str:
    from tools.registry import tool_error, tool_result

    args = args or {}
    session = str(args.get("session") or "latest").strip()
    name = args.get("name")
    local = _coerce_bool(args.get("local", False))
    out_dir = args.get("out_dir") or None
    try:
        return tool_result(_do_save(session=session, name=name,
                                    local=local, out_dir=out_dir))
    except (FileNotFoundError, ValueError) as exc:
        return tool_error(str(exc))
    except ConnectionError as exc:
        return tool_error(f"Leaderboard unreachable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"save_trace failed: {type(exc).__name__}: {exc}")


_HELP = (
    "/save-trace — package a Hermes session trace\n"
    "\n"
    "Upload to leaderboard (default, +1 point each):\n"
    "  /save-trace                       upload latest session\n"
    "  /save-trace all                   upload every session in one zip\n"
    "  /save-trace <session-id>          upload a specific session\n"
    "  /save-trace latest <name>         override the leaderboard name\n"
    "\n"
    "Save locally, do NOT upload:\n"
    "  /save-trace --local               save latest to $TRACE_SAVE_DIR (~/hermes-traces)\n"
    "  /save-trace --local all           save all sessions locally\n"
    "  /save-trace --local <session-id>  save one session locally\n"
    "  /save-trace --local -o <dir>      write to a specific directory\n"
    "  /save-trace --local <sess> <name> -o <dir>  full form\n"
    "\n"
    f"  board:    {uploader.leaderboard_url()}\n"
    f"  name:     {uploader.default_name()}\n"
    f"  save-dir: {uploader.default_save_dir()}"
)


def _parse_slash_args(argv):
    """Parse the slash-command tail into (session, name, local, out_dir).

    Positional order: [session] [name].  Flags may appear anywhere:
        --local / -l        set local=True
        --out-dir / -o DIR  set out_dir (implies --local when given)
    """
    local = False
    out_dir = None
    positionals = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--local", "-l"):
            local = True
        elif tok in ("--out-dir", "--out", "-o"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a directory argument")
            out_dir = argv[i + 1]
            local = True  # -o implies local mode
            i += 1
        elif tok.startswith("--out-dir="):
            out_dir = tok.split("=", 1)[1]
            local = True
        else:
            positionals.append(tok)
        i += 1

    session = positionals[0] if len(positionals) >= 1 else "latest"
    name = positionals[1] if len(positionals) >= 2 else None
    return session, name, local, out_dir


def _handle_slash(raw_args: str):
    argv = (raw_args or "").split()
    if argv and argv[0] in ("help", "-h", "--help"):
        return _HELP

    try:
        session, name, local, out_dir = _parse_slash_args(argv)
    except ValueError as exc:
        return f"⚠️  {exc}\n\n{_HELP}"

    try:
        res = _do_save(session=session, name=name, local=local, out_dir=out_dir)
        icon = "💾" if res.get("mode") == "local" else "📤"
        return f"{icon} {res['message']}"
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
        description=(
            "Zip a Hermes session trace. Uploads to the Trace Leaderboard "
            "by default; set local=true to keep the .zip locally instead."
        ),
    )
    ctx.register_command(
        "save-trace",
        handler=_handle_slash,
        description=(
            "Zip a Hermes session trace. Uploads to the leaderboard (+1) "
            "by default; --local keeps it on disk without uploading."
        ),
        args_hint="[--local] [latest|all|<id>] [name] [-o dir]",
    )
