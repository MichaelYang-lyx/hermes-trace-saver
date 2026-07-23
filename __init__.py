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
            "Package a Hermes session trace PLUS the work files touched this "
            "session (read/written/terminal) into one .zip and upload it to "
            "the Trace Leaderboard (+1). Set with_files=false for trace only, "
            "or local=true to keep the .zip on disk without uploading."
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
                "with_files": {
                    "type": "boolean",
                    "description": (
                        "Attach the work files this session read/wrote "
                        "(auto-scanned, safety-filtered). Default true."
                    ),
                    "default": True,
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
    with_files = _coerce_bool(args.get("with_files", True))
    try:
        extra = []
        if with_files:
            try:
                scanned, _rej, _sp = _scan_and_filter()
                extra = [str(p) for p in scanned]
            except Exception:
                extra = []  # scanning is best-effort; never blocks the trace
        res = uploader.save_trace_bundle(
            session=session, extra_files=extra, name=name,
            local=local, out_dir=out_dir,
        )
        return tool_result(res)
    except (FileNotFoundError, ValueError) as exc:
        return tool_error(str(exc))
    except ConnectionError as exc:
        return tool_error(f"Leaderboard unreachable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"save_trace failed: {type(exc).__name__}: {exc}")


_HELP = (
    "/save-trace — package the session trace + files touched this session\n"
    "\n"
    "By default bundles the Hermes session trace AND the work files the\n"
    "session read/wrote (input/output/etc.) into ONE zip.\n"
    "\n"
    "Preview vs upload:\n"
    "  /save-trace                       scan + PREVIEW (trace + which files)\n"
    "  /save-trace --yes                 upload it all to the leaderboard (+1)\n"
    "  /save-trace --yes --local         save the zip locally, don't upload\n"
    "\n"
    "Pick session / name:\n"
    "  /save-trace --yes all             every session in the zip\n"
    "  /save-trace --yes <session-id>    a specific session\n"
    "  /save-trace --yes --name <name>   override the leaderboard name\n"
    "\n"
    "Trace only, no work files:\n"
    "  /save-trace --yes --no-files\n"
    "\n"
    "Tweak the attached file list (one-shot, repeatable):\n"
    "  --exclude PAT / -x PAT            drop matching files (basename or glob)\n"
    "  --add PATH   / -a PATH            add an extra file (runs safety filters)\n"
    "  --only PAT                        keep only matching files\n"
    "  /save-trace --yes -x *.log -a report.pdf\n"
    "\n"
    "Local output dir:\n"
    "  /save-trace --yes --local -o <dir>\n"
    "\n"
    "Safety filters on attached files: drops .env / *.key / *.pem / SSH keys,\n"
    "files > 50 MB, and paths under .hermes / .git / node_modules etc.\n"
    "\n"
    f"  board:    {uploader.leaderboard_url()}\n"
    f"  name:     {uploader.default_name()}\n"
    f"  save-dir: {uploader.default_save_dir()}"
)


def _parse_slash_args(argv):
    """Parse the /save-trace tail into an options dict.

    Positional order: [session] [name]. Flags may appear anywhere:
      --yes / -y             confirm & upload (no preview)
      --local / -l           save locally instead of uploading
      -o / --out-dir DIR     local dir (implies --local)
      --name NAME            leaderboard name (also accepted positionally)
      --note / -n NOTE       note stored in the zip manifest
      --no-files             trace only; skip the work-file scan
      --exclude / -x PAT     drop scanned files (repeatable)
      --add / -a PATH        add an extra file (repeatable)
      --only PAT             whitelist scanned files (repeatable)
    """
    opts = {
        "session": None, "name": None, "note": "", "yes": False,
        "local": False, "out_dir": None, "no_files": False,
        "exclude": [], "add": [], "only": [], "help": False,
    }
    positionals = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("help", "-h", "--help"):
            opts["help"] = True
            return opts
        elif tok in ("--yes", "-y"):
            opts["yes"] = True
        elif tok in ("--local", "-l"):
            opts["local"] = True
        elif tok in ("--no-files", "--trace-only"):
            opts["no_files"] = True
        elif tok in ("--out-dir", "--out", "-o"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a directory")
            opts["out_dir"] = argv[i + 1]; opts["local"] = True; i += 1
        elif tok.startswith("--out-dir="):
            opts["out_dir"] = tok.split("=", 1)[1]; opts["local"] = True
        elif tok == "--name":
            if i + 1 >= len(argv):
                raise ValueError("--name needs a value")
            opts["name"] = argv[i + 1]; i += 1
        elif tok.startswith("--name="):
            opts["name"] = tok.split("=", 1)[1]
        elif tok in ("--note", "-n"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a value")
            opts["note"] = argv[i + 1]; i += 1
        elif tok.startswith("--note="):
            opts["note"] = tok.split("=", 1)[1]
        elif tok in ("--exclude", "--drop", "-x"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a pattern")
            opts["exclude"].append(argv[i + 1]); i += 1
        elif tok.startswith("--exclude="):
            opts["exclude"].append(tok.split("=", 1)[1])
        elif tok in ("--add", "-a"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a path")
            opts["add"].append(argv[i + 1]); i += 1
        elif tok.startswith("--add="):
            opts["add"].append(tok.split("=", 1)[1])
        elif tok == "--only":
            if i + 1 >= len(argv):
                raise ValueError("--only needs a pattern")
            opts["only"].append(argv[i + 1]); i += 1
        elif tok.startswith("--only="):
            opts["only"].append(tok.split("=", 1)[1])
        else:
            positionals.append(tok)
        i += 1

    opts["session"] = positionals[0] if len(positionals) >= 1 else "latest"
    if opts["name"] is None and len(positionals) >= 2:
        opts["name"] = positionals[1]
    return opts


def _format_bundle_preview(session_label, session_path, kept, rejected, changes):
    """Preview text for the merged /save-trace: shows the trace + attached files."""
    lines = []
    if session_path is not None:
        # Classify how we picked this session so the user knows if it's reliable.
        sp_str = str(session_path)
        env_sid = os.environ.get("HERMES_SESSION_ID", "").strip()
        env_file = os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()
        if env_file and str(Path(env_file).expanduser()) == sp_str:
            tag = "当前会话 (来自 HERMES_TUI_ACTIVE_SESSION_FILE)"
        elif env_sid and env_sid in session_path.name:
            tag = "当前会话 (来自 HERMES_SESSION_ID)"
        elif "/trace-saver-dbexport-" in sp_str:
            tag = "当前会话 (从 state.db 导出)"
        elif uploader.state_db_available():
            tag = "按目录 mtime 挑选 (可能是旧会话)"
        else:
            tag = "按目录 mtime 挑选"
        lines.append(f"session trace: {session_path.name}  [{tag}]")
        if "可能是旧会话" in tag:
            lines.append(
                "  ⚠️  当前会话可能没写到 sessions/ 目录里；"
                "用 /save-trace list 查看真实活跃会话，或 --yes <session-id> 指定。"
            )
    else:
        lines.append(f"session trace: {session_label}")
    total = sum(_safe_stat(p) for p in kept)
    lines.append(f"attach files : {len(kept)} file(s), ~{total/1024:.1f} KB")
    for p in kept:
        lines.append(f"  ✓ {p}  ({_safe_stat(p)} B)")
    if rejected:
        lines.append(f"skipped      : {len(rejected)} file(s)")
        for p, why in rejected[:20]:
            lines.append(f"  ✗ {p}  — {why}")
        if len(rejected) > 20:
            lines.append(f"  ... and {len(rejected)-20} more")
    if changes:
        lines.append("tweaks:")
        for marker, p, reason in changes:
            lines.append(f"  {marker} {p}  — {reason}")
    lines.append("")
    lines.append("Add --yes to upload  (or --yes --local to save the zip locally).")
    lines.append("Tweak: -x <pat> drop / -a <path> add / --only <pat> / --no-files.")
    return "\n".join(lines)


def _safe_stat(p):
    try:
        from pathlib import Path as _P
        return _P(p).stat().st_size
    except OSError:
        return 0


def _format_session_list(rows) -> str:
    """Render `list_db_sessions()` output for the /save-trace list subcommand."""
    if not rows:
        return "state.db 不可用或没有 session。"
    from datetime import datetime as _dt
    lines = ["最近活跃会话 (state.db, 最新在上):",
             "",
             f"  {'session id':<28}  {'model':<20}  {'msgs':>4}  {'last active':<19}",
             f"  {'-'*28}  {'-'*20}  {'-'*4}  {'-'*19}"]
    for r in rows:
        last = r["last_msg"]
        try:
            last_str = _dt.fromtimestamp(float(last)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_str = str(last)
        lines.append(
            f"  {r['id']:<28}  {(r['model'] or '?'):<20}  {r['nmsg']:>4}  {last_str}"
        )
    lines.append("")
    lines.append("上传其中一个:  /save-trace --yes <session-id>")
    lines.append("(session-id 支持子串匹配,比如时间戳前缀就行)")
    return "\n".join(lines)


def _handle_slash(raw_args: str):
    argv = (raw_args or "").split()

    # `/save-trace list` — show the DB's most-recently-active sessions so the
    # user can pick one when the auto-detected "latest" is wrong.
    if argv and argv[0] in ("list", "ls", "sessions"):
        limit = 15
        for tok in argv[1:]:
            if tok.isdigit():
                limit = max(1, min(int(tok), 100))
        try:
            rows = uploader.list_db_sessions(limit=limit)
        except Exception as exc:  # noqa: BLE001
            return f"⚠️  read state.db failed: {exc}"
        return _format_session_list(rows)

    try:
        opts = _parse_slash_args(argv)
    except ValueError as exc:
        return f"⚠️  {exc}\n\n{_HELP}"
    if opts["help"]:
        return _HELP

    session = opts["session"]
    name = opts["name"]
    note = opts["note"]
    yes = opts["yes"]
    local = opts["local"]
    out_dir = opts["out_dir"]

    try:
        # --- Gather attached work files (unless --no-files) ---
        kept, rejected, changes, session_path, session_label = [], [], [], None, session
        if not opts["no_files"]:
            scanned, rejected, session_path = _scan_and_filter()
            kept, changes = _apply_tweaks(scanned, opts["exclude"],
                                          opts["add"], opts["only"])
            # figure out the human label for the session being saved
            try:
                _sf, session_label = uploader.resolve_sessions(session)
                if _sf:
                    session_path = _sf[0]
            except Exception:
                pass

        # --- Preview mode (no --yes) ---
        if not yes:
            if opts["no_files"]:
                # trace-only preview
                try:
                    sf, lbl = uploader.resolve_sessions(session)
                    sp = sf[0] if sf else None
                except Exception as exc:
                    return f"⚠️  {exc}"
                return (
                    f"session trace: {sp.name if sp else lbl}  (selector: {lbl})\n"
                    f"attach files : none (--no-files)\n\n"
                    "Add --yes to upload  (or --yes --local to save locally)."
                )
            return _format_bundle_preview(session_label, session_path,
                                          kept, rejected, changes)

        # --- Upload / save (--yes) ---
        extra = [str(p) for p in kept]
        res = uploader.save_trace_bundle(
            session=session, extra_files=extra, name=name, note=note,
            local=local, out_dir=out_dir,
        )
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
    "Explicit files (skips scan):\n"
    "  /upload-files a.xlsx b.xlsx           bundle these two, upload\n"
    "  /upload-files a.xlsx --local          save bundle locally, no upload\n"
    "\n"
    "Auto-scan the current session (preview by default):\n"
    "  /upload-files                          scan + show candidate list\n"
    "  /upload-files --yes                    scan + upload (no preview)\n"
    "  /upload-files --yes --local            scan + save locally\n"
    "\n"
    "One-shot tweaks (works with --yes; refine the auto-scan list in one line):\n"
    "  --exclude PATTERN / -x PATTERN         drop matching files (repeatable)\n"
    "  --add PATH        / -a PATH            add an extra file       (repeatable)\n"
    "  --only PATTERN                         keep only matches       (repeatable)\n"
    "  PATTERN: exact path, basename, or glob (e.g. *.log)\n"
    "\n"
    "Examples:\n"
    "  /upload-files --yes -x big.log                 upload scan minus big.log\n"
    "  /upload-files --yes -a extra.csv               upload scan plus extra.csv\n"
    "  /upload-files --yes -x *.log -a report.pdf     drop *.log, add report.pdf\n"
    "  /upload-files --yes --only *.xlsx              scan but keep only .xlsx\n"
    "  /upload-files -n \"weekly analysis\"             attach a note in the manifest\n"
    "\n"
    "Safety filters (always on): drops .env / *.key / *.pem / SSH keys,\n"
    "files > 50 MB, and paths under .hermes / .git / node_modules etc.\n"
)


def _handle_upload_files_slash(raw_args: str):
    argv = (raw_args or "").split()
    try:
        opts = _parse_upload_files_args(argv)
    except ValueError as exc:
        return f"⚠️  {exc}\n\n{_UPLOAD_FILES_HELP}"
    if opts.get("help"):
        return _UPLOAD_FILES_HELP

    paths   = opts["paths"]
    name    = opts["name"]
    note    = opts["note"]
    local   = opts["local"]
    out_dir = opts["out_dir"]
    yes     = opts["yes"]
    ex_pats = opts["exclude"]
    add_ps  = opts["add"]
    only_ps = opts["only"]

    try:
        # --- Explicit paths: bypass scan, no preview needed ---
        if paths:
            if ex_pats or add_ps or only_ps:
                return (
                    "⚠️  --exclude / --add / --only only apply to auto-scan mode "
                    "(you already provided explicit paths).\n\n" + _UPLOAD_FILES_HELP
                )
            res = _do_upload_files(paths=paths, name=name, note=note,
                                   local=local, out_dir=out_dir)
            icon = "💾" if res.get("mode") == "local" else "📎"
            return f"{icon} {res['message']}"

        # --- Auto-scan mode ---
        kept, rejected, session_path = _scan_and_filter()

        # Apply tweaks (works in both preview and --yes flows)
        final, changes = _apply_tweaks(kept, ex_pats, add_ps, only_ps)

        if not final:
            body = _format_tweaked_preview(kept, rejected, changes,
                                           session_path=session_path)
            return (
                "⚠️  No files left to upload after scan + tweaks.\n\n"
                + body
                + "\n\nHint: pass explicit paths, e.g. `/upload-files a.xlsx b.xlsx`."
            )
        if not yes:
            body = _format_tweaked_preview(final, rejected, changes,
                                           session_path=session_path)
            return body + (
                "\n\nTweak flags: --exclude <pat> / --add <path> / --only <pat>. "
                "Add --yes to upload."
            )

        # --yes → actually do it
        res = _do_upload_files(
            paths=[str(p) for p in final],
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


def _match_pattern(path, pattern: str) -> bool:
    """True if *pattern* matches this path.

    Matches on (in this order):
      1. exact full path
      2. basename equality
      3. fnmatch of basename (globs: *.log, foo*, ?.csv)
      4. fnmatch of the full path
    """
    import fnmatch
    from pathlib import Path as _P

    p = _P(path)
    pat = pattern.strip()
    if not pat:
        return False
    if str(p) == pat or str(p.expanduser().resolve() if p.exists() else p) == pat:
        return True
    if p.name == pat:
        return True
    if fnmatch.fnmatch(p.name, pat):
        return True
    if fnmatch.fnmatch(str(p), pat):
        return True
    return False


def _apply_tweaks(kept, exclude_patterns, add_paths, only_patterns):
    """Apply --exclude / --add / --only to the candidate list.

    Returns (final_paths, changes) where changes is a list of
    (marker, path, reason) for the preview:
      ' '  kept from scan
      '-'  removed by --exclude
      '+'  added by --add (survived safety filters)
      '✗'  --add candidate that failed safety filters (with reason)
      ' '  --only trimmed (removed items get a '-' with reason)
    """
    from pathlib import Path as _P

    changes = []  # (marker, path, reason_or_empty)
    final = list(kept)

    # --- --only whitelist (drop anything not matching) ---
    if only_patterns:
        matched = []
        for p in final:
            if any(_match_pattern(p, pat) for pat in only_patterns):
                matched.append(p)
            else:
                changes.append(("-", p, "--only filter"))
        final = matched

    # --- --exclude removals ---
    for pat in exclude_patterns:
        removed = [p for p in final if _match_pattern(p, pat)]
        for p in removed:
            changes.append(("-", p, f"--exclude {pat}"))
        final = [p for p in final if p not in removed]
        if not removed:
            changes.append(("!", _P(pat), f"--exclude {pat} matched nothing"))

    # --- --add additions (run safety filters) ---
    if add_paths:
        add_kept, add_rej = filepicker.filter_paths([_P(p) for p in add_paths])
        existing_resolved = {str(_P(p).expanduser().resolve()) for p in final if _P(p).exists()}
        for p in add_kept:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in existing_resolved:
                changes.append(("!", p, "--add: already in list"))
                continue
            final.append(p)
            changes.append(("+", p, "--add"))
        for p, why in add_rej:
            changes.append(("✗", p, f"--add rejected: {why}"))

    return final, changes


def _format_tweaked_preview(kept, rejected, changes, *, session_path=None):
    """Render preview text showing scan results + explicit tweak markers."""
    base = filepicker.format_preview(kept, rejected, session_path=session_path)
    if not changes:
        return base
    lines = [base, "", "tweaks:"]
    for marker, p, reason in changes:
        lines.append(f"  {marker} {p}  — {reason}")
    return "\n".join(lines)


def _parse_upload_files_args(argv):
    """Slash arg parser. Returns a dict of parsed options.

    Positional args are treated as explicit file paths (bypasses scan mode).
    Flags:
      --yes / -y             confirm auto-scan without preview
      --local / -l           save the zip locally instead of uploading
      -o / --out-dir DIR     local output dir (implies --local)
      --name / -n NAME       leaderboard display name
      --note NOTE            free-text note (stored in zip manifest)
      --exclude PATTERN      drop from the scanned candidate list
                             (repeatable; matches basename, full path,
                             or fnmatch glob like *.log)
      --add PATH             add an extra file to the scanned list
                             (repeatable; still runs safety filters)
      --only PATTERN         keep ONLY candidates matching this pattern
                             (repeatable; same matching rules as --exclude)
    """
    opts = {
        "paths": [], "name": None, "note": "",
        "local": False, "out_dir": None, "yes": False,
        "exclude": [], "add": [], "only": [],
    }
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("help", "-h", "--help"):
            opts["help"] = True
            return opts
        if tok in ("--local", "-l"):
            opts["local"] = True
        elif tok in ("--yes", "-y"):
            opts["yes"] = True
        elif tok in ("--name",):
            if i + 1 >= len(argv):
                raise ValueError("--name needs a value")
            opts["name"] = argv[i + 1]; i += 1
        elif tok == "-n" or tok == "--note":
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a value")
            opts["note"] = argv[i + 1]; i += 1
        elif tok in ("--out-dir", "--out", "-o"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a directory")
            opts["out_dir"] = argv[i + 1]; opts["local"] = True; i += 1
        elif tok.startswith("--out-dir="):
            opts["out_dir"] = tok.split("=", 1)[1]; opts["local"] = True
        elif tok.startswith("--name="):
            opts["name"] = tok.split("=", 1)[1]
        elif tok.startswith("--note="):
            opts["note"] = tok.split("=", 1)[1]
        elif tok in ("--exclude", "--drop", "-x"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a pattern")
            opts["exclude"].append(argv[i + 1]); i += 1
        elif tok.startswith("--exclude="):
            opts["exclude"].append(tok.split("=", 1)[1])
        elif tok in ("--add", "-a"):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} needs a path")
            opts["add"].append(argv[i + 1]); i += 1
        elif tok.startswith("--add="):
            opts["add"].append(tok.split("=", 1)[1])
        elif tok == "--only":
            if i + 1 >= len(argv):
                raise ValueError("--only needs a pattern")
            opts["only"].append(argv[i + 1]); i += 1
        elif tok.startswith("--only="):
            opts["only"].append(tok.split("=", 1)[1])
        else:
            opts["paths"].append(tok)
        i += 1
    return opts


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
            "Zip the Hermes session trace + files touched this session and "
            "upload to the leaderboard (+1). with_files=false for trace only; "
            "local=true to keep the .zip on disk."
        ),
    )
    ctx.register_command(
        "save-trace",
        handler=_handle_slash,
        description=(
            "Bundle the session trace + files touched this session into one "
            "zip. No args = preview; --yes uploads (+1); --local saves locally."
        ),
        args_hint="[--yes] [--local] [--no-files] [-x pat] [-a path] [name]",
    )
    # Agent-only helper: bundle arbitrary/explicit files WITHOUT the trace.
    # (The /upload-files slash command was merged into /save-trace; this tool
    # stays for the pure-files case an agent may still want.)
    ctx.register_tool(
        name="upload_files",
        toolset="trace",
        schema=UPLOAD_FILES_SCHEMA,
        handler=_handle_upload_files_tool,
        check_fn=_check_available,
        emoji="📎",
        description=(
            "Bundle explicit files (or auto-scan the current session for "
            "read/written files) into one zip WITHOUT the session trace, "
            "then upload to the leaderboard."
        ),
    )
