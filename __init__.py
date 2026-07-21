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
    from . import filepicker  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - standalone / test path
    sys.path.insert(0, os.path.dirname(__file__))
    import uploader  # type: ignore[no-redef]
    import filepicker  # type: ignore[no-redef]


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


# --------------------------------------------------------------------------- #
# /upload-files — bundle arbitrary files (auto-scanned or explicit) into a zip
# --------------------------------------------------------------------------- #
UPLOAD_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "upload_files",
        "description": (
            "Bundle files into a single .zip and upload it to the Trace "
            "Leaderboard (+1). Two modes: (1) pass an explicit list of paths, "
            "or (2) leave paths empty to auto-scan the current session for "
            "files touched by read_file / write_file / patch / terminal. "
            "Sensitive names, files > 50MB, and tool-owned dirs (.hermes, "
            ".git, node_modules...) are filtered out. Set local=true to keep "
            "the .zip locally without uploading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit files to bundle. When empty, the tool "
                        "auto-scans the current session."
                    ),
                    "default": [],
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Leaderboard display name (default: "
                        "$TRACE_LEADERBOARD_NAME or system user)."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Free-text note stored in the zip's manifest.",
                },
                "local": {
                    "type": "boolean",
                    "description": "Save the .zip locally instead of uploading.",
                    "default": False,
                },
                "out_dir": {
                    "type": "string",
                    "description": (
                        "When local=true, the directory to write into. "
                        "Defaults to $TRACE_SAVE_DIR or ~/hermes-traces."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _scan_and_filter():
    """Auto-scan the latest session, return (kept, rejected, session_path)."""
    session_path = None
    sessions = uploader.list_sessions()
    if sessions:
        session_path = sessions[0]
    raw = filepicker.scan_session(session_path)
    from pathlib import Path as _P
    kept, rejected = filepicker.filter_paths([_P(p) for p in raw])
    return kept, rejected, session_path


def _do_upload_files(paths=None, name=None, note="", local=False,
                     out_dir=None) -> dict:
    """Common backend: normalize inputs and hand off to uploader.upload_files."""
    return uploader.upload_files(
        files=[p for p in (paths or [])],
        name=name,
        note=note or "",
        local=local,
        out_dir=out_dir,
    )


def _handle_upload_files_tool(args: dict, **_kw) -> str:
    from tools.registry import tool_error, tool_result

    args = args or {}
    paths = args.get("paths") or []
    name = args.get("name")
    note = args.get("note") or ""
    local = _coerce_bool(args.get("local", False))
    out_dir = args.get("out_dir") or None

    try:
        if not paths:
            # Auto-scan mode: filter and upload (agent path implicitly consents).
            kept, rejected, _ = _scan_and_filter()
            if not kept:
                return tool_error(
                    "auto-scan found no eligible files in the current session "
                    "(all filtered by safety rules or missing). "
                    "Pass explicit `paths` to override."
                )
            paths = [str(p) for p in kept]
        res = _do_upload_files(paths=paths, name=name, note=note,
                               local=local, out_dir=out_dir)
        return tool_result(res)
    except (FileNotFoundError, ValueError) as exc:
        return tool_error(str(exc))
    except ConnectionError as exc:
        return tool_error(f"Leaderboard unreachable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"upload_files failed: {type(exc).__name__}: {exc}")


_UPLOAD_FILES_HELP = (
    "/upload-files — bundle files into a zip and upload\n"
    "\n"
    "Explicit files:\n"
    "  /upload-files a.xlsx b.xlsx           bundle these two files, upload\n"
    "  /upload-files a.xlsx --local          save the bundle locally, no upload\n"
    "  /upload-files a.xlsx -n \"weekly\"      attach a note in the zip manifest\n"
    "\n"
    "Auto-scan current session (preview, then confirm):\n"
    "  /upload-files                          scan + preview the file list\n"
    "  /upload-files --yes                    scan + upload without preview\n"
    "  /upload-files --yes --local            scan + save locally\n"
    "\n"
    "Safety filters (always on): drops .env / *.key / *.pem / SSH keys,\n"
    "files > 50 MB, and paths under .hermes / .git / node_modules etc.\n"
)


def _parse_upload_files_args(argv):
    """Slash arg parser. Returns (paths, name, note, local, out_dir, yes)."""
    paths, name, note = [], None, ""
    local, out_dir, yes = False, None, False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--local", "-l"):
            local = True
        elif tok in ("--yes", "-y"):
            yes = True
        elif tok in ("--name",):
            if i + 1 >= len(argv):
                raise ValueError("--name needs a value")
            name = argv[i + 1]; i += 1
        elif tok in ("--note", "-n"):
            if i + 1 >= len(argv):
                raise ValueError("-n needs a value")
            note = argv[i + 1]; i += 1
        elif tok in ("--out-dir", "--out", "-o"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a directory")
            out_dir = argv[i + 1]; local = True; i += 1
        elif tok.startswith("--out-dir="):
            out_dir = tok.split("=", 1)[1]; local = True
        elif tok.startswith("--name="):
            name = tok.split("=", 1)[1]
        elif tok.startswith("--note="):
            note = tok.split("=", 1)[1]
        elif tok in ("help", "-h", "--help"):
            return "HELP", None, None, None, None, None
        else:
            paths.append(tok)
        i += 1
    return paths, name, note, local, out_dir, yes


def _handle_upload_files_slash(raw_args: str):
    argv = (raw_args or "").split()
    try:
        parsed = _parse_upload_files_args(argv)
    except ValueError as exc:
        return f"⚠️  {exc}\n\n{_UPLOAD_FILES_HELP}"
    if parsed[0] == "HELP":
        return _UPLOAD_FILES_HELP
    paths, name, note, local, out_dir, yes = parsed

    try:
        # --- Explicit paths: bypass scan, no preview needed ---
        if paths:
            res = _do_upload_files(paths=paths, name=name, note=note,
                                   local=local, out_dir=out_dir)
            icon = "💾" if res.get("mode") == "local" else "📎"
            return f"{icon} {res['message']}"

        # --- Auto-scan mode ---
        kept, rejected, session_path = _scan_and_filter()
        if not kept:
            body = filepicker.format_preview(kept, rejected,
                                             session_path=session_path)
            return (
                "⚠️  No eligible files found in the current session.\n\n"
                + body
                + "\n\nHint: pass explicit paths, e.g. `/upload-files a.xlsx b.xlsx`."
            )
        if not yes:
            # Preview only. User re-runs with --yes to confirm.
            return filepicker.format_preview(kept, rejected,
                                             session_path=session_path)

        # yes → do the upload/save
        res = _do_upload_files(
            paths=[str(p) for p in kept],
            name=name, note=note, local=local, out_dir=out_dir,
        )
        icon = "💾" if res.get("mode") == "local" else "📎"
        return f"{icon} {res['message']}"

    except (FileNotFoundError, ValueError) as exc:
        return f"⚠️  {exc}"
    except ConnectionError as exc:
        return f"⚠️  Leaderboard unreachable: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"⚠️  upload-files failed: {type(exc).__name__}: {exc}"


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
    ctx.register_tool(
        name="upload_files",
        toolset="trace",
        schema=UPLOAD_FILES_SCHEMA,
        handler=_handle_upload_files_tool,
        check_fn=_check_available,
        emoji="📎",
        description=(
            "Bundle explicit files (or auto-scan the current session for "
            "read/written files) into one zip, then upload to the leaderboard."
        ),
    )
    ctx.register_command(
        "upload-files",
        handler=_handle_upload_files_slash,
        description=(
            "Bundle files into a zip and upload. With no args, scans the "
            "current session for touched files and asks you to confirm."
        ),
        args_hint="[--yes] [--local] [-o dir] [-n note] [path...]",
    )
