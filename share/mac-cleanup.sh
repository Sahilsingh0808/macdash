#!/bin/zsh
# Frees disk space on this Mac. Dry run by default: it only lists what it would do.
#   ./mac-cleanup.sh            list only
#   ./mac-cleanup.sh --apply    do it
#   SKIP_CACHES=1 ./mac-cleanup.sh   worktrees only (used by the Mac Health dashboard)
#
# Your code folder: $MACDASH_CODE_DIR, else "code_dir" in ~/.config/macdash/config.json, else ~/Codebase.
#
# What it touches:
#   1. Caches that rebuild themselves (pnpm, npm, uv, Codex, Docker build cache, unused Docker images).
#   2. Git worktrees under your code folder that are safe to drop: merged into the default branch,
#      no modified or untracked files, no process working in them, last commit 3+ days old.
#      Removed with `git worktree remove` (no --force), so git itself refuses anything dirty.
#      Branches are kept. Worktrees under .claude/worktrees are skipped: the Claude app owns them.
#
# What it never touches: Docker volumes (database data), source repos, photos/shoots,
# Claude conversation history, the Claude app's data folder.

APPLY=0; [[ "$1" == "--apply" ]] && APPLY=1
setopt NULL_GLOB
CODE=${MACDASH_CODE_DIR:-$(plutil -extract code_dir raw -o - ~/.config/macdash/config.json 2>/dev/null)}
CODE=${CODE:-~/Codebase}; CODE=${CODE/#\~/$HOME}; CODE=${CODE%/}
[[ -d "$CODE" ]] || { echo "Code folder not found: $CODE (set code_dir in ~/.config/macdash/config.json)"; exit 1; }
free() { df -k /System/Volumes/Data | awk 'NR==2{printf "%.1f GB", $4/1048576}'; }
size() { du -sk "$1" 2>/dev/null | awk '{printf "%.2f GB", $1/1048576}'; }
run()  { if (( APPLY )); then echo "  run: $*"; eval "$@"; else echo "  would run: $*"; fi; }

echo "Free disk before: $(free)"; echo

if [[ -z "$SKIP_CACHES" ]]; then
echo "== 1. Caches =="
command -v pnpm >/dev/null && { echo "pnpm store"; run "pnpm store prune"; }
[[ -d ~/.npm/_cacache ]] && { echo "npm cache ($(size ~/.npm/_cacache))"; run "npm cache clean --force"; }
command -v uv >/dev/null && { echo "uv cache ($(size ~/.cache/uv))"; run "uv cache prune"; }
[[ -d ~/Library/Caches/com.openai.codex ]] && { echo "Codex cache ($(size ~/Library/Caches/com.openai.codex))"; run "rm -rf ~/Library/Caches/com.openai.codex"; }
if docker info >/dev/null 2>&1; then
  echo "Docker build cache and images no container uses (volumes are kept)"
  run "docker builder prune -af"
  run "docker image prune -af"
else
  echo "Docker not running: skipped"
fi
echo
fi

echo "== 2. Worktrees in ${CODE/#$HOME/~} =="
INUSE=$(for p in $(pgrep -u $(id -u)); do lsof -a -p $p -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'; done | sort -u)
cutoff=$(date -v-3d +%s)
total=0; count=0; skipped=0
for g in "$CODE"/*/.git(/) "$CODE"/*/*/.git(/) "$CODE"/*/*/*/.git(/); do
  repo=${g:h}
  def=$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  [[ -z "$def" ]] && for b in main master staging; do git -C "$repo" rev-parse -q --verify "$b" >/dev/null && { def=$b; break; }; done
  [[ -z "$def" ]] && continue
  git -C "$repo" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0,10)}' | while read -r wt; do
    [[ "$wt" == "$repo" || ! -d "$wt" ]] && continue
    [[ "$wt" == */.claude/worktrees/* ]] && continue
    reason=""
    [[ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]] && reason="has changes"
    [[ -z "$reason" ]] && ! git -C "$repo" merge-base --is-ancestor "$(git -C "$wt" rev-parse HEAD)" "$def" 2>/dev/null && reason="not merged into $def"
    [[ -z "$reason" ]] && (( $(git -C "$wt" log -1 --format=%ct) > cutoff )) && reason="commit under 3 days old"
    [[ -z "$reason" ]] && print -r -- "$INUSE" | /usr/bin/grep -q "^$wt" && reason="in use"
    if [[ -n "$reason" ]]; then skipped=$((skipped+1)); continue; fi
    kb=$(du -sk "$wt" 2>/dev/null | cut -f1); total=$((total+kb)); count=$((count+1))
    echo "${wt#$CODE/}  ($(size "$wt"))"
    run "git -C '$repo' worktree remove '$wt'"
  done
  (( APPLY )) && git -C "$repo" worktree prune
done
echo; echo "Worktrees: $count to remove, $(awk "BEGIN{printf \"%.1f\", $total/1048576}") GB. Kept: $skipped (changes, unmerged, recent or in use)."
echo; echo "Free disk after: $(free)"
(( APPLY )) || echo "Dry run only. Run with --apply to do it."
