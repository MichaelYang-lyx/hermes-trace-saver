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
