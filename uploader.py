"""trace-saver — package Hermes session traces and upload them to the leaderboard.

Pure logic, no Hermes imports, so it can be unit-tested / invoked standalone.

A "trace" is a Hermes session file: ``~/.hermes/sessions/session_*.json``.
This module resolves which session(s) to save, zips them into a temp file,
and POSTs the zip to the Trace Leaderboard ``/upload`` endpoint (multipart
``name`` + ``file``). Each upload scores +1 on the board.

Everything is configurable via environment variables (see ``defaults``):

* ``TRACE_LEADERBOARD_URL``   – base URL (default ``http://10.9.66.12:8848``)
* ``TRACE_LEADERBOARD_NAME``  – board display name (default: system user)
* ``HERMES_HOME``             – Hermes home (default ``~/.hermes``)
"""

from __future__ import annotations

import getpass
import glob
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_URL = "http://10.9.66.12:8848"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def sessions_dir() -> Path:
    return hermes_home() / "sessions"


def leaderboard_url() -> str:
    return os.environ.get("TRACE_LEADERBOARD_URL", DEFAULT_URL).rstrip("/")


def default_name() -> str:
    name = os.environ.get("TRACE_LEADERBOARD_NAME", "").strip()
    if name:
        return name
    try:
        return getpass.getuser() or "hermes"
    except Exception:
        return "hermes"


def _safe(part: str, fallback: str = "trace") -> str:
    part = _SAFE_RE.sub("_", (part or "").strip()).strip("._")
    return part or fallback


# --------------------------------------------------------------------------- #
# session resolution
# --------------------------------------------------------------------------- #
def list_sessions() -> List[Path]:
    """All session_*.json files, newest first (by mtime)."""
    files = [Path(p) for p in glob.glob(str(sessions_dir() / "session_*.json"))]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files


# --------------------------------------------------------------------------- #
# state.db — modern Hermes stores conversations directly in SQLite. The
# sessions/*.json tree is a legacy/export artifact and is often stale, so we
# read the DB whenever we can.
# --------------------------------------------------------------------------- #
def _state_db_path() -> Path:
    return hermes_home() / "state.db"


def state_db_available() -> bool:
    return _state_db_path().is_file()


def _connect_state_db():
    """Open state.db read-only (works even while Hermes is writing)."""
    import sqlite3
    p = _state_db_path()
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def list_db_sessions(limit: int = 200) -> List[dict]:
    """Sessions from state.db, most-recently-active first (by last message).

    Returns dicts with keys: id, source, model, started_at, ended_at,
    last_msg (unix ts), nmsg.
    """
    if not state_db_available():
        return []
    try:
        with _connect_state_db() as con:
            rows = con.execute("""
                SELECT s.id, s.source, s.model, s.started_at, s.ended_at,
                       COALESCE(MAX(m.timestamp), s.started_at) AS last_msg,
                       COUNT(m.id) AS nmsg
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY last_msg DESC
                LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def most_recent_db_session_id() -> Optional[str]:
    """Session id with the most recent message across the DB."""
    sessions = list_db_sessions(limit=1)
    return sessions[0]["id"] if sessions else None


def export_session_from_db(session_id: str, out_dir: Optional[Path] = None) -> Path:
    """Read sessions+messages from state.db, write a session_<id>.json file.

    Emits the same top-level shape as the legacy ``sessions/*.json``
    exports so the rest of the packaging code works unchanged:
    ``session_id, model, session_start, last_updated, system_prompt,
    message_count, messages``.

    Only ``active`` messages are exported (compacted/observed rows are
    metadata Hermes writes for its own tracking).
    """
    if not state_db_available():
        raise FileNotFoundError(f"state.db not found at {_state_db_path()}")

    with _connect_state_db() as con:
        srow = con.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if srow is None:
            raise FileNotFoundError(
                f"session '{session_id}' not found in state.db"
            )
        # `active` may not exist on very old schemas — be defensive.
        try:
            mrows = con.execute("""
                SELECT role, content, tool_call_id, tool_calls, tool_name,
                       timestamp, finish_reason, reasoning
                FROM messages
                WHERE session_id = ?
                  AND COALESCE(active, 1) = 1
                ORDER BY id ASC""", (session_id,)).fetchall()
        except Exception:
            mrows = con.execute("""
                SELECT role, content, tool_call_id, tool_calls, tool_name,
                       timestamp, finish_reason
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC""", (session_id,)).fetchall()

    def _parse_tool_calls(raw):
        if not raw:
            return None
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw  # keep raw string as fallback

    messages = []
    last_ts = 0.0
    for r in mrows:
        m = {"role": r["role"], "content": r["content"] or ""}
        tc = _parse_tool_calls(r["tool_calls"])
        if tc is not None:
            m["tool_calls"] = tc
        if r["tool_call_id"]:
            m["tool_call_id"] = r["tool_call_id"]
        if r["tool_name"]:
            m["tool_name"] = r["tool_name"]
        if r["finish_reason"]:
            m["finish_reason"] = r["finish_reason"]
        if r["timestamp"]:
            m["timestamp"] = r["timestamp"]
            last_ts = max(last_ts, float(r["timestamp"] or 0))
        try:
            if r["reasoning"]:
                m["reasoning"] = r["reasoning"]
        except (IndexError, KeyError):
            pass
        messages.append(m)

    def _iso(ts):
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(float(ts)).astimezone().isoformat()
        except (TypeError, ValueError):
            return str(ts)

    payload = {
        "session_id": srow["id"],
        "model": srow["model"] if "model" in srow.keys() else None,
        "source": srow["source"] if "source" in srow.keys() else None,
        "session_start": _iso(srow["started_at"]) if "started_at" in srow.keys() else None,
        "last_updated": _iso(last_ts) if last_ts else None,
        "system_prompt": srow["system_prompt"] if "system_prompt" in srow.keys() else None,
        "exported_from": "state.db",
        "exported_at": datetime.now().astimezone().isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }

    out_dir = Path(out_dir).expanduser() if out_dir else Path(tempfile.mkdtemp(
        prefix="trace-saver-dbexport-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"session_{session_id}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out_path


def current_session_file() -> Optional[Path]:
    """Return a Path to the CURRENT session as JSON (best effort).

    Priority:
      1. ``HERMES_TUI_ACTIVE_SESSION_FILE`` env (explicit path on disk).
      2. ``HERMES_SESSION_ID`` env → export from state.db if possible,
         else look for a matching ``sessions/session_<id>.json``.
      3. Most recently active session in ``state.db`` (by last message
         timestamp) → export to a temp json.

    Returns None when nothing works (no env hint, no DB, no on-disk file).
    """
    # 1) explicit active-session file path (most reliable when set)
    active = os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()
    if active:
        p = Path(active).expanduser()
        if p.is_file():
            return p

    # 2) explicit HERMES_SESSION_ID
    sid = os.environ.get("HERMES_SESSION_ID", "").strip()
    if sid:
        exact = sessions_dir() / f"session_{sid}.json"
        if exact.is_file():
            return exact
        # try exporting from DB
        if state_db_available():
            try:
                return export_session_from_db(sid)
            except FileNotFoundError:
                pass
        # last-ditch: substring match against on-disk files
        for p in list_sessions():
            if sid in p.name:
                return p

    # 3) no env hint → most-recently-active session from the DB
    if state_db_available():
        sid = most_recent_db_session_id()
        if sid:
            try:
                return export_session_from_db(sid)
            except FileNotFoundError:
                pass
    return None


def resolve_sessions(session: str) -> Tuple[List[Path], str]:
    """Resolve the ``session`` selector to a list of files + a label for the zip name.

    ``session`` accepts:
      * ``"latest"`` / empty  – the CURRENTLY ACTIVE session (via
        ``HERMES_SESSION_ID`` env or the newest session in ``state.db``)
      * ``"all"``             – every session (DB if available, else *.json)
      * a session id or filename substring – matched against DB then filenames
    """
    session = (session or "latest").strip()

    if session in ("all", "*"):
        db_sessions = list_db_sessions(limit=1000)
        if db_sessions:
            files = []
            for s in db_sessions:
                try:
                    files.append(export_session_from_db(s["id"]))
                except Exception:
                    pass
            if files:
                return files, "all"
        # fall back to on-disk *.json
        all_files = list_sessions()
        if not all_files:
            raise FileNotFoundError(
                f"No Hermes sessions found (state.db empty, {sessions_dir()} empty)"
            )
        return all_files, "all"

    if session in ("latest", "", "last", "current"):
        cur = current_session_file()
        if cur is not None:
            return [cur], _safe(cur.stem, "current")
        # no DB and no env hint → best-effort newest .json by mtime
        all_files = list_sessions()
        if not all_files:
            raise FileNotFoundError(
                f"No Hermes sessions found (state.db unavailable, "
                f"{sessions_dir()} empty)"
            )
        return [all_files[0]], _safe(all_files[0].stem, "latest")

    # explicit selector: prefer DB (may include sessions not yet on disk)
    if state_db_available():
        db_matches = [s for s in list_db_sessions(limit=1000) if session in s["id"]]
        if db_matches:
            files = []
            for s in db_matches:
                try:
                    files.append(export_session_from_db(s["id"]))
                except Exception:
                    pass
            if files:
                return files, _safe(session)

    # last: substring match against on-disk filenames
    all_files = list_sessions()
    matches = [p for p in all_files if session in p.name]
    if not matches:
        raise FileNotFoundError(
            f"No session matching '{session}' in state.db or "
            f"{sessions_dir()}. Try `/save-trace list` to see available IDs."
        )
    return matches, _safe(session)


# --------------------------------------------------------------------------- #
# packaging
# --------------------------------------------------------------------------- #
def default_save_dir() -> Path:
    """Where local-mode zips are written when the caller doesn't specify a path.

    ``TRACE_SAVE_DIR`` env var overrides; default ``~/hermes-traces``.
    """
    raw = os.environ.get("TRACE_SAVE_DIR", "").strip()
    return Path(raw).expanduser() if raw else (Path.home() / "hermes-traces")


def build_zip(files: List[Path], label: str, name: str, now: Optional[datetime] = None,
              out_dir: Optional[Path] = None) -> Path:
    """Zip the given session files into a .zip; return its path.

    Adds a small ``manifest.json`` describing what was packaged. If
    ``out_dir`` is ``None``, writes to a fresh temp dir (caller must
    delete). If ``out_dir`` is given, writes there (caller keeps the file).
    """
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    zip_name = f"hermes_trace_{_safe(name)}_{_safe(label)}_{ts}.zip"

    if out_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="trace-saver-"))
    else:
        target_dir = Path(out_dir).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / zip_name

    manifest = {
        "board_name": name,
        "selector": label,
        "created_at": now.astimezone().isoformat(),
        "session_count": len(files),
        "sessions": [f.name for f in files],
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for f in files:
            if f.exists():
                zf.write(f, arcname=f"sessions/{f.name}")
    return zip_path


def build_bundle_zip(files: List[Path], name: str, label: str = "files",
                     note: str = "",
                     now: Optional[datetime] = None,
                     out_dir: Optional[Path] = None) -> Path:
    """Zip an arbitrary list of files (not sessions) for upload.

    Layout inside the zip::

        manifest.json          # {name, created_at, note, files:[{name,size}]}
        files/<basename>       # each source file, flattened by basename

    Basename collisions get suffixed with ``__<n>``.
    """
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    zip_name = f"hermes_files_{_safe(name)}_{_safe(label)}_{ts}.zip"

    if out_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="trace-saver-files-"))
    else:
        target_dir = Path(out_dir).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / zip_name

    used_names: dict[str, int] = {}
    file_entries = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            f = Path(f)
            if not f.exists() or not f.is_file():
                continue
            base = f.name or "unnamed"
            if base in used_names:
                used_names[base] += 1
                stem, dot, ext = base.rpartition(".")
                base = (
                    f"{stem}__{used_names[base]}.{ext}" if dot else f"{base}__{used_names[base]}"
                )
            else:
                used_names[base] = 0
            arc = f"files/{base}"
            zf.write(f, arcname=arc)
            file_entries.append({
                "arcname": arc,
                "source": str(f),
                "size": f.stat().st_size,
            })

        manifest = {
            "kind": "hermes-file-bundle",
            "board_name": name,
            "created_at": now.astimezone().isoformat(),
            "note": note or "",
            "file_count": len(file_entries),
            "files": file_entries,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return zip_path


def build_combined_zip(session_files: List[Path], extra_files: List[Path],
                       name: str, label: str = "latest", note: str = "",
                       now: Optional[datetime] = None,
                       out_dir: Optional[Path] = None) -> Path:
    """Zip session trace(s) AND arbitrary work files into one archive.

    Layout::

        manifest.json
        sessions/<name>.json    # the Hermes session trace(s)
        files/<basename>        # work files (input/output/etc.)

    Used by the merged /save-trace which bundles the trace together with
    files touched during the session.
    """
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    zip_name = f"hermes_trace_{_safe(name)}_{_safe(label)}_{ts}.zip"

    if out_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="trace-saver-combined-"))
    else:
        target_dir = Path(out_dir).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / zip_name

    used: dict[str, int] = {}
    file_entries = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in session_files:
            f = Path(f)
            if f.exists():
                zf.write(f, arcname=f"sessions/{f.name}")
        for f in extra_files:
            f = Path(f)
            if not f.exists() or not f.is_file():
                continue
            base = f.name or "unnamed"
            if base in used:
                used[base] += 1
                stem, dot, ext = base.rpartition(".")
                base = f"{stem}__{used[base]}.{ext}" if dot else f"{base}__{used[base]}"
            else:
                used[base] = 0
            arc = f"files/{base}"
            zf.write(f, arcname=arc)
            file_entries.append({"arcname": arc, "source": str(f),
                                 "size": f.stat().st_size})

        manifest = {
            "kind": "hermes-trace+files",
            "board_name": name,
            "selector": label,
            "created_at": now.astimezone().isoformat(),
            "note": note or "",
            "session_count": len([f for f in session_files if Path(f).exists()]),
            "sessions": [Path(f).name for f in session_files if Path(f).exists()],
            "file_count": len(file_entries),
            "files": file_entries,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return zip_path


# --------------------------------------------------------------------------- #
# HTTP: healthz + multipart upload (requests preferred, urllib fallback)
# --------------------------------------------------------------------------- #
def _healthz(base_url: str, timeout: float = 5.0) -> None:
    """Raise a clear error if the leaderboard is unreachable."""
    url = f"{base_url}/healthz"
    try:
        import requests  # type: ignore

        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"leaderboard healthz returned {r.status_code}")
        return
    except ImportError:
        pass
    except Exception as exc:  # requests present but failed
        raise ConnectionError(f"Trace Leaderboard unreachable at {url}: {exc}") from exc

    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                raise RuntimeError(f"leaderboard healthz returned {resp.status}")
    except Exception as exc:
        raise ConnectionError(f"Trace Leaderboard unreachable at {url}: {exc}") from exc


def _upload_requests(base_url: str, name: str, zip_path: Path, timeout: float) -> int:
    """Login (POST /login) to get the session cookie, then POST /upload with
    the ``files`` multipart field. The leaderboard reads the name from the
    signed cookie — the form no longer carries a ``name`` field."""
    import requests  # type: ignore

    s = requests.Session()
    r = s.post(f"{base_url}/login", data={"name": name}, timeout=timeout,
               allow_redirects=False)
    if r.status_code not in (200, 303):
        raise RuntimeError(f"login failed: HTTP {r.status_code} {r.text[:200]}")

    with zip_path.open("rb") as fh:
        r = s.post(
            f"{base_url}/upload",
            files=[("files", (zip_path.name, fh, "application/zip"))],
            timeout=timeout,
            allow_redirects=False,
        )
    if r.status_code not in (200, 303):
        raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:200]}")
    return r.status_code


def _upload_urllib(base_url: str, name: str, zip_path: Path, timeout: float) -> int:
    """Zero-dependency login + multipart upload, cookie carried by CookieJar."""
    import http.cookiejar
    import urllib.parse
    import urllib.request

    # --- redirect handler that preserves cookies but never follows redirects ---
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: D401,ANN001
            return None

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirect
    )

    # ---- 1) login: POST /login with name= (form-urlencoded) ----
    login_body = urllib.parse.urlencode({"name": name}).encode()
    login_req = urllib.request.Request(  # noqa: S310
        f"{base_url}/login",
        data=login_body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(login_req, timeout=timeout) as resp:  # noqa: S310
            login_status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 303:
            login_status = 303  # expected success
        else:
            raise RuntimeError(f"login failed: HTTP {exc.code} {exc.read()[:200]!r}") from exc
    if login_status not in (200, 303):
        raise RuntimeError(f"login failed: HTTP {login_status}")

    # ---- 2) upload: POST /upload with files= multipart ----
    boundary = "----trace-saver-boundary-7MA4YWxkTrZu0gW"
    crlf = b"\r\n"
    body = bytearray()
    body += b"--" + boundary.encode() + crlf
    body += (
        b'Content-Disposition: form-data; name="files"; filename="'
        + zip_path.name.encode("utf-8")
        + b'"'
        + crlf
    )
    body += b"Content-Type: application/zip" + crlf + crlf
    body += zip_path.read_bytes() + crlf
    body += b"--" + boundary.encode() + b"--" + crlf

    req = urllib.request.Request(  # noqa: S310
        f"{base_url}/upload",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 303:
            return 303
        raise RuntimeError(f"upload failed: HTTP {exc.code} {exc.read()[:200]!r}") from exc


def upload_zip(zip_path: Path, name: str, base_url: Optional[str] = None,
               timeout: float = 300.0) -> int:
    """Upload a prepared zip. Returns the HTTP status (200 or 303 = success)."""
    base_url = (base_url or leaderboard_url()).rstrip("/")
    _healthz(base_url)
    try:
        import requests  # noqa: F401

        return _upload_requests(base_url, name, zip_path, timeout)
    except ImportError:
        return _upload_urllib(base_url, name, zip_path, timeout)


# --------------------------------------------------------------------------- #
# top-level entry point used by the plugin
# --------------------------------------------------------------------------- #
def save_trace(session: str = "latest", name: Optional[str] = None,
               base_url: Optional[str] = None, local: bool = False,
               out_dir: Optional[str] = None) -> dict:
    """Resolve → zip → (upload OR keep locally).

    * ``local=False`` (default) — zip to a temp file, upload to the
      leaderboard, delete the temp file. Scores +1.
    * ``local=True`` — zip to ``out_dir`` (or ``$TRACE_SAVE_DIR``, or
      ``~/hermes-traces``) and stop. Nothing is uploaded, nothing is
      deleted; the caller can share the .zip however they want.
    """
    name = (name or default_name()).strip()
    if not name:
        raise ValueError("board name is empty; set TRACE_LEADERBOARD_NAME")

    files, label = resolve_sessions(session)

    if local:
        target_dir = Path(out_dir).expanduser() if out_dir else default_save_dir()
        zip_path = build_zip(files, label, name, out_dir=target_dir)
        size = zip_path.stat().st_size
        return {
            "success": True,
            "mode": "local",
            "name": name,
            "selector": label,
            "sessions_saved": len(files),
            "zip_bytes": size,
            "zip_path": str(zip_path),
            "message": (
                f"Saved {len(files)} trace(s) locally as '{name}' "
                f"({size / 1024:.1f} KB) -> {zip_path}"
            ),
        }

    base_url = (base_url or leaderboard_url()).rstrip("/")
    zip_path = build_zip(files, label, name)
    try:
        size = zip_path.stat().st_size
        status = upload_zip(zip_path, name, base_url=base_url)
    finally:
        # clean the temp zip + its dir
        try:
            zip_path.unlink(missing_ok=True)
            zip_path.parent.rmdir()
        except Exception:
            pass

    return {
        "success": True,
        "mode": "upload",
        "name": name,
        "selector": label,
        "sessions_uploaded": len(files),
        "zip_bytes": size,
        "http_status": status,
        "leaderboard": base_url,
        "user_page": f"{base_url}/u/{name}",
        "message": (
            f"Uploaded {len(files)} trace(s) as '{name}' "
            f"({size / 1024:.1f} KB). See {base_url}/u/{name}"
        ),
    }


def upload_files(files: List[Path], name: Optional[str] = None,
                 note: str = "", label: str = "files",
                 base_url: Optional[str] = None,
                 local: bool = False,
                 out_dir: Optional[str] = None) -> dict:
    """Bundle an arbitrary file list into a zip and upload it (or keep local).

    Mirrors :func:`save_trace` but for any files the caller supplies,
    not for Hermes session traces. On the leaderboard it looks like an
    ordinary upload (+1 point when ``local=False``).
    """
    name = (name or default_name()).strip()
    if not name:
        raise ValueError("board name is empty; set TRACE_LEADERBOARD_NAME")
    if not files:
        raise ValueError("no files to upload")

    resolved = [Path(f) for f in files]
    missing = [str(f) for f in resolved if not f.exists() or not f.is_file()]
    if missing:
        raise FileNotFoundError("file(s) not found: " + ", ".join(missing))

    if local:
        target_dir = Path(out_dir).expanduser() if out_dir else default_save_dir()
        zip_path = build_bundle_zip(resolved, name=name, label=label, note=note,
                                    out_dir=target_dir)
        size = zip_path.stat().st_size
        return {
            "success": True,
            "mode": "local",
            "name": name,
            "files_saved": len(resolved),
            "zip_bytes": size,
            "zip_path": str(zip_path),
            "message": (
                f"Saved {len(resolved)} file(s) locally as '{name}' "
                f"({size / 1024:.1f} KB) -> {zip_path}"
            ),
        }

    base_url = (base_url or leaderboard_url()).rstrip("/")
    zip_path = build_bundle_zip(resolved, name=name, label=label, note=note)
    try:
        size = zip_path.stat().st_size
        status = upload_zip(zip_path, name, base_url=base_url)
    finally:
        try:
            zip_path.unlink(missing_ok=True)
            zip_path.parent.rmdir()
        except Exception:
            pass

    return {
        "success": True,
        "mode": "upload",
        "name": name,
        "files_uploaded": len(resolved),
        "zip_bytes": size,
        "http_status": status,
        "leaderboard": base_url,
        "user_page": f"{base_url}/u/{name}",
        "message": (
            f"Uploaded {len(resolved)} file(s) as '{name}' "
            f"({size / 1024:.1f} KB). See {base_url}/u/{name}"
        ),
    }


def save_trace_bundle(session: str = "latest", extra_files: Optional[List[Path]] = None,
                      name: Optional[str] = None, note: str = "",
                      base_url: Optional[str] = None,
                      local: bool = False, out_dir: Optional[str] = None) -> dict:
    """Bundle session trace(s) + work files into one zip, then upload or save.

    This is the backend for the merged ``/save-trace``. ``extra_files`` is the
    already-filtered list of work files to include (may be empty).
    """
    name = (name or default_name()).strip()
    if not name:
        raise ValueError("board name is empty; set TRACE_LEADERBOARD_NAME")
    extra_files = [Path(f) for f in (extra_files or [])]

    session_files, label = resolve_sessions(session)

    if local:
        target_dir = Path(out_dir).expanduser() if out_dir else default_save_dir()
        zip_path = build_combined_zip(session_files, extra_files, name=name,
                                      label=label, note=note, out_dir=target_dir)
        size = zip_path.stat().st_size
        return {
            "success": True, "mode": "local", "name": name, "selector": label,
            "sessions_saved": len(session_files), "files_saved": len(extra_files),
            "zip_bytes": size, "zip_path": str(zip_path),
            "message": (
                f"Saved trace + {len(extra_files)} file(s) locally as '{name}' "
                f"({size / 1024:.1f} KB) -> {zip_path}"
            ),
        }

    base_url = (base_url or leaderboard_url()).rstrip("/")
    zip_path = build_combined_zip(session_files, extra_files, name=name,
                                  label=label, note=note)
    try:
        size = zip_path.stat().st_size
        status = upload_zip(zip_path, name, base_url=base_url)
    finally:
        try:
            zip_path.unlink(missing_ok=True)
            zip_path.parent.rmdir()
        except Exception:
            pass

    return {
        "success": True, "mode": "upload", "name": name, "selector": label,
        "sessions_uploaded": len(session_files), "files_uploaded": len(extra_files),
        "zip_bytes": size, "http_status": status, "leaderboard": base_url,
        "user_page": f"{base_url}/u/{name}",
        "message": (
            f"Uploaded trace + {len(extra_files)} file(s) as '{name}' "
            f"({size / 1024:.1f} KB). See {base_url}/u/{name}"
        ),
    }
