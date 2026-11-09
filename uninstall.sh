#!/bin/zsh
# Removes macdash. Your settings and logs are kept unless you pass --all.
#   ~/.local/share/macdash/uninstall.sh [--all]
LABEL=io.github.sahilsingh0808.macdash.memwatch

pkill -f "$HOME/.local/share/macdash/server.py" 2>/dev/null
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/.local/bin/macdash" "$HOME/.local/bin/memwatch"
rm -rf "$HOME/Applications/Mac Health.app" "$HOME/.local/share/macdash" "$HOME/Library/Caches/memwatch"

if [[ "$1" == "--all" ]]; then
  rm -rf "$HOME/.config/macdash"
  rm -f "$HOME/Library/Logs/memwatch.log" "$HOME/Library/Logs/memwatch.err" "$HOME/Library/Logs/macdash.log"
  echo "macdash removed, with its settings and logs."
else
  echo "macdash removed. Kept your settings (~/.config/macdash) and logs (~/Library/Logs/memwatch.log, macdash.log)."
  echo "Run again with --all to delete those too."
fi
