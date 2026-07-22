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

# Absolute or home-relative path token in a terminal command / output.
_ABS_OR_HOME_PATH_RE = re.compile(r"(?<![\w])(/[^\s'\"`;|&<>()]+|~/[^\s'\"`;|&<>()]+)")


def _paths_from_tool_call(tc: dict) -> List[str]:
    """Extract candidate file paths from a single assistant tool_call dict."""
    fn = tc.get("function") or {}
    name = fn.get("name", "")
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        return []

    out: List[str] = []
    if name in _PATH_ARG_TOOLS:
        for key in ("path", "file_path"):
            v = args.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    elif name in _TERMINAL_TOOLS:
        cmd = args.get("command")
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
    return out


def scan_session(session_path: Optional[Path] = None) -> List[str]:
    """Return the list of candidate file paths mentioned in *session_path*.

    Duplicates are collapsed; order roughly follows first appearance. This
    is the RAW set — caller runs :func:`filter_paths` to drop unsafe/missing
    entries.
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
    for msg in data.get("messages", []) or []:
        for tc in (msg.get("tool_calls") or []):
            for p in _paths_from_tool_call(tc):
                if p not in seen:
                    seen.add(p)
                    ordered.append(p)
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
