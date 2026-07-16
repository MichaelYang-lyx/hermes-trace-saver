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


def resolve_sessions(session: str) -> Tuple[List[Path], str]:
    """Resolve the ``session`` selector to a list of files + a label for the zip name.

    ``session`` accepts:
      * ``"latest"`` / empty  – most recently modified session
      * ``"all"``             – every session file
      * a session id or filename substring – matched against filenames
    """
    session = (session or "latest").strip()
    all_files = list_sessions()
    if not all_files:
        raise FileNotFoundError(
            f"No Hermes session traces found under {sessions_dir()}"
        )

    if session in ("all", "*"):
        return all_files, "all"

    if session in ("latest", "", "last"):
        return [all_files[0]], _safe(all_files[0].stem, "latest")

    # match by id / substring against filename
    matches = [p for p in all_files if session in p.name]
    if not matches:
        raise FileNotFoundError(
            f"No session matching '{session}' under {sessions_dir()} "
            f"({len(all_files)} sessions available; use 'latest' or 'all')"
        )
    return matches, _safe(session)


# --------------------------------------------------------------------------- #
# packaging
# --------------------------------------------------------------------------- #
def build_zip(files: List[Path], label: str, name: str, now: Optional[datetime] = None) -> Path:
    """Zip the given session files into a temp .zip; return its path.

    Adds a small ``manifest.json`` describing what was packaged.
    """
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    zip_name = f"hermes_trace_{_safe(name)}_{_safe(label)}_{ts}.zip"
    tmp_dir = Path(tempfile.mkdtemp(prefix="trace-saver-"))
    zip_path = tmp_dir / zip_name

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
    import requests  # type: ignore

    with zip_path.open("rb") as fh:
        r = requests.post(
            f"{base_url}/upload",
            data={"name": name},
            files={"file": (zip_path.name, fh, "application/zip")},
            timeout=timeout,
            allow_redirects=False,
        )
    if r.status_code not in (200, 303):
        raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:200]}")
    return r.status_code


def _upload_urllib(base_url: str, name: str, zip_path: Path, timeout: float) -> int:
    """Zero-dependency multipart/form-data POST."""
    import urllib.request

    boundary = "----trace-saver-boundary-7MA4YWxkTrZu0gW"
    crlf = b"\r\n"
    body = bytearray()

    body += b"--" + boundary.encode() + crlf
    body += b'Content-Disposition: form-data; name="name"' + crlf + crlf
    body += name.encode("utf-8") + crlf

    body += b"--" + boundary.encode() + crlf
    body += (
        b'Content-Disposition: form-data; name="file"; filename="'
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
    # We must not auto-follow the 303 redirect (it would GET the user page).
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: D401,ANN001
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 303:  # redirect = success
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
               base_url: Optional[str] = None) -> dict:
    """Resolve → zip → upload. Returns a result dict (never raises for the caller
    to format; raises only on genuine errors that callers turn into tool_error)."""
    name = (name or default_name()).strip()
    if not name:
        raise ValueError("board name is empty; set TRACE_LEADERBOARD_NAME")
    base_url = (base_url or leaderboard_url()).rstrip("/")

    files, label = resolve_sessions(session)
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
