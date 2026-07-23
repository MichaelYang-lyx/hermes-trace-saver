"""File collection / filtering / bundling for the ``/upload-files`` command.

Two entry points:

* :func:`scan_session` — inspect the latest Hermes session JSON and return
  the set of file paths that ``read_file`` / ``write_file`` / ``patch`` /
  ``terminal`` tool calls touched and that still exist on disk.
* :func:`filter_paths` — apply the safety filters (sensitive names,
  size cap, tool-owned directories).

Pairs with :mod:`uploader` — the caller usually does
``scan_session`` → ``filter_paths`` → preview → ``uploader.build_zip``
(with the explicit paths passed via an ``extra_files`` bridge).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

# Reuse session discovery from uploader so both modules agree on which
# session is "the latest".
try:
    from . import uploader  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - standalone test path
    import uploader  # type: ignore[no-redef]


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per file

_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[/.])(?:"
    r"\.env(\..+)?|"
    r"\.netrc|"
    r"id_rsa|id_ed25519|id_ecdsa|"
    r".+\.pem|.+\.key|"
    r".+(secret|token|credential|password)s?.*"
    r")$",
    re.IGNORECASE,
)

# Directories we never want to sweep into an upload. Paths whose
# resolved location is under any of these get skipped.
_EXCLUDED_DIR_NAMES = {
    ".hermes",
    ".claude",
    ".cache",
    ".ssh",
    ".gnupg",
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
}


def _is_sensitive_name(path: Path) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(path.name))


def _in_excluded_dir(path: Path) -> bool:
    """True if any component of the resolved path matches a blocked dir."""
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = path
    return any(part in _EXCLUDED_DIR_NAMES for part in resolved.parts)


def _too_big(path: Path, max_bytes: int) -> bool:
    try:
        return path.stat().st_size > max_bytes
    except OSError:
        return False


def filter_paths(
    paths: Iterable[Path],
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """Split *paths* into (kept, rejected). Each rejected entry has a reason.

    Filters applied, in order:

    1. Path must exist and be a regular file.
    2. Not under an excluded directory (`.hermes`, `.git`, `node_modules`, ...).
    3. Name doesn't look sensitive (`.env`, `*.key`, `*_secret*`, ssh keys).
    4. Size <= ``max_size_bytes`` (default 50 MB).
    """
    kept: List[Path] = []
    rejected: List[Tuple[Path, str]] = []
    seen: Set[Path] = set()

    for raw in paths:
        try:
            p = Path(raw).expanduser()
        except Exception:
            rejected.append((Path(str(raw)), "bad path"))
            continue

        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)

        if not p.exists() or not p.is_file():
            rejected.append((p, "missing or not a regular file"))
            continue
        if _in_excluded_dir(p):
            rejected.append((p, "under an excluded directory"))
            continue
        if _is_sensitive_name(p):
            rejected.append((p, "sensitive filename"))
            continue
        if _too_big(p, max_size_bytes):
            rejected.append((p, f"> {max_size_bytes // (1024*1024)} MB"))
            continue
        kept.append(p)

    return kept, rejected


# --------------------------------------------------------------------------- #
# Session scan
# --------------------------------------------------------------------------- #
# Tool names whose ``arguments.path`` (or ``arguments.file_path``) is the
# file the tool acted on. read/write/patch all follow this shape.
_PATH_ARG_TOOLS = {
    "read_file",
    "write_file",
    "patch",
    "edit_file",
    "edit",
    "view_file",
    "append_file",
}
_TERMINAL_TOOLS = {"terminal", "bash", "shell"}
# Tools that search/list files. Their ``target`` arg is a directory the
# search ran under, and their RESULT (in the paired role=tool message)
# contains the actual matched paths (often ./relative).
_SEARCH_TOOLS = {"search_files", "glob", "find_files", "list_files", "ls"}

# Absolute or home-relative path token in a terminal command / output.
_ABS_OR_HOME_PATH_RE = re.compile(r"(?<![\w])(/[^\s'\"`;|&<>()]+|~/[^\s'\"`;|&<>()]+)")
# ./relative or plain filename-with-extension token — used when scanning
# search-tool RESULTS where paths often lack a leading slash.
_REL_PATH_RE = re.compile(r"(?:\./|(?<![\w./]))([\w.\-一-鿿]+\.[A-Za-z0-9]{1,8})")


def _paths_from_tool_call(tc: dict) -> Tuple[List[str], Optional[str]]:
    """Extract candidate file paths + a base directory hint from one tool_call.

    Returns (paths, base_dir). ``base_dir`` is a directory the tool implicitly
    treats as cwd (e.g. ``search_files``'s ``target``) — used later to resolve
    relative paths appearing in the tool's RESULT message.
    """
    fn = tc.get("function") or {}
    name = fn.get("name", "")
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        return [], None

    out: List[str] = []
    base: Optional[str] = None

    if name in _PATH_ARG_TOOLS:
        for key in ("path", "file_path"):
            v = args.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    elif name in _TERMINAL_TOOLS:
        cmd = args.get("command")
        cwd = args.get("cwd") or args.get("workdir")
        if isinstance(cwd, str) and cwd.strip():
            base = cwd.strip()
        if isinstance(cmd, str):
            # 1) tokenise: catches `cat /path/x.log` even when the path is quoted
            try:
                for tok in shlex.split(cmd, posix=True):
                    if tok.startswith(("/", "~")):
                        out.append(tok)
            except ValueError:
                pass
            # 2) regex sweep: catches paths embedded in larger tokens
            for m in _ABS_OR_HOME_PATH_RE.findall(cmd):
                out.append(m)
    elif name in _SEARCH_TOOLS:
        # target/dir/path arg may be a search-base directory OR an enum-ish
        # value like "files"/"directories" (Hermes search_files' target).
        # Only treat it as a base when it *looks* like a path.
        for key in ("cwd", "root", "dir", "directory", "path", "target"):
            v = args.get(key)
            if isinstance(v, str) and v.strip():
                s = v.strip()
                if s.startswith(("/", "~", "./", "../")) or "/" in s:
                    base = s
                    break
    return out, base


def _paths_from_tool_result(content, base: Optional[str]) -> List[str]:
    """Pull file paths out of a role='tool' message's content.

    Handles two shapes seen in Hermes:
      * JSON: {"files": ["./a.png", "./b.png"], ...}
      * Plain text listing (find/ls-style output)

    Relative entries (``./x``, ``x.ext``) are resolved against ``base`` when
    given, so results from ``search_files target=files`` become absolute.
    """
    if not content:
        return []
    text = content if isinstance(content, str) else str(content)
    out: List[str] = []

    # Try JSON first — search_files returns {"files": [...]}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("files", "paths", "results", "matches"):
                v = data.get(key)
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.strip():
                            out.append(item.strip())
                        elif isinstance(item, dict):
                            for k2 in ("path", "file", "name"):
                                s = item.get(k2)
                                if isinstance(s, str) and s.strip():
                                    out.append(s.strip())
                                    break
    except (TypeError, ValueError):
        pass

    # Fall back to plain-text sweep (line-per-path or ./relative tokens)
    if not out:
        for line in text.splitlines():
            ln = line.strip()
            if not ln:
                continue
            if ln.startswith(("/", "~")):
                out.append(ln)
            elif ln.startswith("./") or _REL_PATH_RE.search(ln):
                out.append(ln)

    # Resolve relatives against base dir; fall back to Hermes process cwd
    # when no explicit base was given by the tool call.
    from pathlib import Path as _P
    resolved: List[str] = []
    base_p = _P(base).expanduser() if base else None
    if base_p is None:
        try:
            base_p = uploader.hermes_cwd()  # /proc-based, best-effort
        except Exception:
            base_p = None
    for p in out:
        pp = _P(p)
        if pp.is_absolute() or p.startswith("~"):
            resolved.append(p)
        elif base_p is not None:
            rel = p[2:] if p.startswith("./") else p
            resolved.append(str(base_p / rel))
        else:
            # No cwd hint — leave the raw relative; filter_paths will drop it.
            resolved.append(p)
    return resolved


def scan_session(session_path: Optional[Path] = None) -> List[str]:
    """Return the list of candidate file paths mentioned in *session_path*.

    Reads BOTH tool_call arguments AND the paired tool RESULT messages, so
    search/list tools (whose match set only appears in the result) are
    covered. Duplicates collapsed; order roughly follows first appearance.
    """
    if session_path is None:
        # Prefer the live session (HERMES_SESSION_ID) over newest-by-mtime, so
        # we scan the CURRENT conversation's files, not an older one's.
        session_path = uploader.current_session_file()
    if session_path is None:
        sessions = uploader.list_sessions()
        if not sessions:
            raise FileNotFoundError(
                f"No Hermes session file under {uploader.sessions_dir()}"
            )
        session_path = sessions[0]

    with open(session_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    seen: Set[str] = set()
    ordered: List[str] = []
    # Map tool_call_id → base_dir hint so we can resolve relative paths in the
    # paired result message.
    call_bases: dict = {}

    def _remember(paths):
        for p in paths:
            if p and p not in seen:
                seen.add(p)
                ordered.append(p)

    for msg in data.get("messages", []) or []:
        role = msg.get("role")
        if role == "assistant":
            for tc in (msg.get("tool_calls") or []):
                paths, base = _paths_from_tool_call(tc)
                _remember(paths)
                tcid = tc.get("id") or tc.get("call_id")
                if tcid and base:
                    call_bases[tcid] = base
        elif role == "tool":
            base = call_bases.get(msg.get("tool_call_id"))
            _remember(_paths_from_tool_result(msg.get("content"), base))
    return ordered


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def format_preview(kept: Sequence[Path], rejected: Sequence[Tuple[Path, str]],
                   *, session_path: Optional[Path] = None) -> str:
    """Human-readable listing for the confirmation step of `/upload-files`."""
    lines: List[str] = []
    if session_path is not None:
        lines.append(f"scanned session: {Path(session_path).name}")
    total = sum(_safe_size(p) for p in kept)
    lines.append(
        f"will upload: {len(kept)} file(s), ~{total / 1024:.1f} KB total"
    )
    for p in kept:
        lines.append(f"  ✓ {p}  ({_safe_size(p)} B)")
    if rejected:
        lines.append(f"skipped: {len(rejected)} file(s)")
        for p, why in rejected[:20]:
            lines.append(f"  ✗ {p}  — {why}")
        if len(rejected) > 20:
            lines.append(f"  ... and {len(rejected) - 20} more")
    if kept:
        lines.append("")
        lines.append("Run `/upload-files --yes` to upload them, "
                     "or `/upload-files --yes --local` to save the zip locally.")
    return "\n".join(lines)


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
