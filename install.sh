#!/bin/zsh
# macdash installer. Installs into your home folder only and never asks for an admin password.
#
#   curl -fsSL https://github.com/Sahilsingh0808/macdash/releases/latest/download/install.sh | zsh
#   curl -fsSL https://github.com/Sahilsingh0808/macdash/releases/latest/download/install.sh | zsh -s -- --with-tools
#   ./install.sh [options]          (from a clone of the repo)
#
# Options:
#   --code-dir PATH   folder that holds your projects (default: the first of ~/Codebase, ~/code, ~/Projects, ... that exists)
#   --with-tools      also install the helper apps and tools with Homebrew (Stats, Pearcleaner, GrandPerspective, kondo, btop, dust)
#   --no-watchdog     skip the memory watchdog that notifies you every 5 minutes when a limit is crossed
#   --no-app          skip the "Mac Health" app in ~/Applications
#   --no-open         don't open the dashboard when done
# Running it again updates macdash and keeps your settings.
set -euo pipefail

REPO=Sahilsingh0808/macdash
TAG=${MACDASH_VERSION:-latest}
LABEL=io.github.sahilsingh0808.macdash.memwatch
DEST=$HOME/.local/share/macdash
BIN=$HOME/.local/bin
CFG_DIR=$HOME/.config/macdash
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
APP_PATH="$HOME/Applications/Mac Health.app"

WATCHDOG=1 APP=1 TOOLS=0 OPEN=1 CODE_DIR=""
while (( $# )); do
  case "$1" in
    --code-dir) CODE_DIR=${2:?--code-dir needs a folder}; shift ;;
    --with-tools) TOOLS=1 ;;
    --no-watchdog) WATCHDOG=0 ;;
    --no-app) APP=0 ;;
    --no-open) OPEN=0 ;;
    -h|--help) print "Options: --code-dir PATH, --with-tools, --no-watchdog, --no-app, --no-open"; exit 0 ;;
    *) print -u2 "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

say() { print -P "%F{blue}==>%f $*"; }
die() { print -u2 -P "%F{red}error:%f $*"; exit 1; }

[[ $(uname -s) == Darwin ]] || die "macdash runs on macOS only."

PY=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x $p ]] && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then PY=$p; break; fi
done
[[ -n $PY ]] || die "macdash needs Python 3.8 or later. Install Apple's command line tools with: xcode-select --install"

# Use the files next to this script (a clone), or download the release.
SRC=""
if [[ ${0:t} == install.sh && -f ${0:A:h}/share/server.py ]]; then
  SRC=${0:A:h}
else
  TMP=$(mktemp -d)
  trap '[[ -n ${TMP:-} && -d $TMP ]] && rm -rf "$TMP"' EXIT
  if [[ $TAG == latest ]]; then url="https://github.com/$REPO/releases/latest/download/macdash.tar.gz"
  else url="https://github.com/$REPO/releases/download/$TAG/macdash.tar.gz"; fi
  say "Downloading $url"
  curl -fsSL "$url" | tar -xz -C "$TMP" || die "Download failed."
  SRC=$TMP/macdash
fi
[[ -f $SRC/share/server.py ]] || die "Install files not found in $SRC."

say "Installing to ${DEST/#$HOME/~} and ${BIN/#$HOME/~}"
mkdir -p "$DEST" "$BIN" "$CFG_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
pkill -f "$DEST/server.py" 2>/dev/null || true   # a running server would keep the old code
install -m 644 "$SRC/share/server.py" "$SRC/share/index.html" "$DEST/"
install -m 755 "$SRC/share/mac-cleanup.sh" "$SRC/uninstall.sh" "$DEST/"
install -m 755 "$SRC/bin/macdash" "$SRC/bin/memwatch" "$BIN/"

# Settings: keep the file if it exists; set code_dir when asked or when it is missing.
CODE_DIR=${CODE_DIR/#\~/$HOME}
[[ -z $CODE_DIR || -d $CODE_DIR ]] || die "Folder not found: $CODE_DIR"
"$PY" - "$CFG_DIR/config.json" "$CODE_DIR" <<'PYEOF'
import json, os, sys
path, code = sys.argv[1], sys.argv[2]
home = os.path.expanduser("~")
try:
    cfg = json.load(open(path))
    if not isinstance(cfg, dict):
        cfg = {}
except (OSError, ValueError):
    cfg = {}
if code:
    cfg["code_dir"] = code.replace(home, "~", 1)
elif not cfg.get("code_dir"):
    found = next((f"~/{n}" for n in ("Codebase", "code", "Code", "Projects", "projects", "Developer",
                                     "dev", "src", "workspace", "repos") if os.path.isdir(f"{home}/{n}")), "~")
    cfg["code_dir"] = found
cfg.setdefault("folders", [])
json.dump(cfg, open(path, "w"), indent=2)
print(f"    code folder: {cfg['code_dir']}  (change it in ~/.config/macdash/config.json)")
PYEOF

if (( WATCHDOG )); then
  say "Starting the memory watchdog (checks every 5 minutes, only notifies)"
  # earlier personal builds used the label local.memwatch
  if [[ -f $HOME/Library/LaunchAgents/local.memwatch.plist ]] && grep -q '.local/bin/memwatch' "$HOME/Library/LaunchAgents/local.memwatch.plist"; then
    launchctl bootout "gui/$UID/local.memwatch" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/local.memwatch.plist"
  fi
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$BIN/memwatch</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/memwatch.err</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$PLIST" || print "    Could not start it now. It starts at your next login."
fi

if (( APP )); then
  say "Adding the Mac Health app to ~/Applications"
  mkdir -p "$HOME/Applications"
  osacompile -o "$APP_PATH" -e 'do shell script "$HOME/.local/bin/macdash >/dev/null 2>&1"' >/dev/null
fi

if (( TOOLS )); then
  if command -v brew >/dev/null; then
    say "Installing helper tools with Homebrew"
    brew install --cask stats pearcleaner grandperspective || true
    brew install kondo btop dust || true
  else
    print "    Homebrew not found (https://brew.sh), so the helper tools were skipped."
  fi
fi

say "Installed macdash $("$PY" "$DEST/server.py" --version | awk '{print $2}')"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) print "    ~/.local/bin is not on your PATH. Add it with:"
     print "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zprofile && source ~/.zprofile" ;;
esac
print "    Open it with: macdash   (or the Mac Health app)"
print "    Remove it with: ~/.local/share/macdash/uninstall.sh"
(( OPEN )) && "$BIN/macdash"
exit 0
