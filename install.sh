#!/usr/bin/env bash
# One-click install for the Hermes `trace-saver` plugin.
#
# Local (after `git clone`):
#   bash install.sh
#
# Remote one-liner (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
#
# - Copies the plugin into $HERMES_HOME/plugins/trace-saver/  (default ~/.hermes)
# - Enables it in config.yaml (via `hermes plugins enable`, with a pure-python
#   fallback if the CLI isn't on PATH).
# - No root required — everything lives under your home dir.
set -euo pipefail

PLUGIN_NAME="trace-saver"
REPO_URL="${TRACE_SAVER_REPO:-https://github.com/MichaelYang-lyx/hermes-trace-saver.git}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"

# Resolve where the plugin sources live. When run from a clone, they sit next
# to this script. When piped via `curl | bash`, BASH_SOURCE points at /dev/fd
# or is empty, so we clone the repo into a temp dir first.
SRC_DIR=""
_self="${BASH_SOURCE[0]:-}"
if [ -n "$_self" ] && [ -f "$(dirname "$_self")/plugin.yaml" ] 2>/dev/null; then
  SRC_DIR="$(cd "$(dirname "$_self")" && pwd)"
fi

_TMP_CLONE=""
if [ -z "$SRC_DIR" ]; then
  echo "==> Fetching plugin from $REPO_URL"
  command -v git >/dev/null 2>&1 || { echo "!! git is required for remote install"; exit 1; }
  _TMP_CLONE="$(mktemp -d)"
  git clone --depth 1 "$REPO_URL" "$_TMP_CLONE" >/dev/null 2>&1 \
    || { echo "!! git clone failed: $REPO_URL"; exit 1; }
  SRC_DIR="$_TMP_CLONE"
  trap 'rm -rf "$_TMP_CLONE"' EXIT
fi

echo "==> Installing '$PLUGIN_NAME' into $DEST_DIR"
mkdir -p "$DEST_DIR"

# Copy plugin sources (exclude scripts/tests, keep it lean).
for f in plugin.yaml __init__.py uploader.py filepicker.py README.md config.example.env; do
  if [ -f "$SRC_DIR/$f" ]; then
    cp -f "$SRC_DIR/$f" "$DEST_DIR/$f"
  fi
done
# Drop any stale bytecode from a previous install.
rm -rf "$DEST_DIR/__pycache__" 2>/dev/null || true
echo "    copied: $(ls "$DEST_DIR" | tr '\n' ' ')"

# --- enable the plugin (standalone plugins are opt-in via plugins.enabled) ---
enabled=0
if command -v hermes >/dev/null 2>&1; then
  if hermes plugins enable "$PLUGIN_NAME" 2>/dev/null; then
    enabled=1
  fi
fi

if [ "$enabled" -ne 1 ]; then
  echo "==> 'hermes' CLI not usable; enabling via config.yaml directly"
  CONFIG="$HERMES_HOME/config.yaml" python3 - "$PLUGIN_NAME" <<'PY'
import os, sys
name = sys.argv[1]
cfg_path = os.environ["CONFIG"]
try:
    import yaml
except Exception:
    print("    !! PyYAML not available; add '%s' to plugins.enabled in %s manually"
          % (name, cfg_path))
    sys.exit(0)

data = {}
if os.path.exists(cfg_path):
    with open(cfg_path) as fh:
        data = yaml.safe_load(fh) or {}
plugins = data.setdefault("plugins", {})
enabled = plugins.get("enabled")
if not isinstance(enabled, list):
    enabled = []
if name not in enabled:
    enabled.append(name)
plugins["enabled"] = sorted(set(enabled))
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
with open(cfg_path, "w") as fh:
    yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
print("    enabled in %s -> plugins.enabled=%s" % (cfg_path, plugins["enabled"]))
PY
fi

cat <<EOF

==> Done. '$PLUGIN_NAME' is installed and enabled.

    Takes effect on the NEXT Hermes session (restart hermes / open a new chat).

    Usage:
      • Slash command:  /save-trace                 (upload latest session)
                        /save-trace all             (all sessions in one zip)
                        /save-trace <session-id>    (a specific one)
                        /save-trace latest <name>   (override board name)
      • Tool:           the agent can call  save_trace

    Configure (optional):
      export TRACE_LEADERBOARD_NAME="your-name-on-board"
      export TRACE_LEADERBOARD_URL="http://10.9.66.12:8848"

    Leaderboard: ${TRACE_LEADERBOARD_URL:-http://10.9.66.12:8848}
EOF
