#!/usr/bin/env bash
# Uninstall the Hermes `trace-saver` plugin.
set -euo pipefail

PLUGIN_NAME="trace-saver"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"

echo "==> Disabling '$PLUGIN_NAME'"
if command -v hermes >/dev/null 2>&1; then
  hermes plugins disable "$PLUGIN_NAME" 2>/dev/null || true
fi

# Also remove it from plugins.enabled in config.yaml (best-effort).
CONFIG="$HERMES_HOME/config.yaml" python3 - "$PLUGIN_NAME" <<'PY' || true
import os, sys
name = sys.argv[1]
cfg_path = os.environ["CONFIG"]
try:
    import yaml
except Exception:
    sys.exit(0)
if not os.path.exists(cfg_path):
    sys.exit(0)
with open(cfg_path) as fh:
    data = yaml.safe_load(fh) or {}
plugins = data.get("plugins") or {}
enabled = plugins.get("enabled")
if isinstance(enabled, list) and name in enabled:
    plugins["enabled"] = [p for p in enabled if p != name]
    with open(cfg_path, "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    print("    removed from plugins.enabled")
PY

echo "==> Removing $DEST_DIR"
rm -rf "$DEST_DIR"
echo "==> Done. Takes effect on the next Hermes session."
