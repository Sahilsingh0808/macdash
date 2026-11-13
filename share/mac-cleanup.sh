#!/bin/zsh
# Frees disk space on this Mac. Dry run by default: it only lists what it would do.
#   ./mac-cleanup.sh [options]
#   ./mac-cleanup.sh --apply    do it
#
# Options:
#   --apply                   actually delete / prune (default: dry run)
#   --caches-only             only clean package/temp/Docker caches
#   --worktrees-only          only scan/remove safe git worktrees
#   --worktree-days N         min days since last commit for worktrees (default: 3)
#   --log-days N              min age in days for AI session logs/backups (default: 14)
#   --build-days N            min age in days for stale .next/.turbo build caches (default: 3)
#   --skip-claude-worktrees   skip worktrees inside .claude/worktrees/
#
# Env overrides: MACDASH_CODE_DIR, SKIP_CACHES=1, SKIP_LOGS=1, SKIP_WORKTREES=1, SKIP_BUILD=1,
#   WORKTREE_DAYS, LOG_DAYS, BUILD_DAYS, SKIP_CLAUDE_WORKTREES=1.
#
# What it never touches: Docker volumes (database data), source code, uncommitted changes,
# unmerged branches, active processes/sessions, photos/media assets.

APPLY=0
WORKTREE_DAYS=${WORKTREE_DAYS:-3}
LOG_DAYS=${LOG_DAYS:-14}
BUILD_DAYS=${BUILD_DAYS:-3}
SKIP_CACHES=${SKIP_CACHES:-0}
SKIP_LOGS=${SKIP_LOGS:-0}
SKIP_WORKTREES=${SKIP_WORKTREES:-0}
SKIP_BUILD=${SKIP_BUILD:-0}
SKIP_CLAUDE_WORKTREES=${SKIP_CLAUDE_WORKTREES:-0}

while (( $# )); do
  case "$1" in
    --apply) APPLY=1 ;;
    --caches-only) SKIP_WORKTREES=1; SKIP_LOGS=1; SKIP_BUILD=1 ;;
    --worktrees-only) SKIP_CACHES=1; SKIP_LOGS=1; SKIP_BUILD=1 ;;
    --worktree-days) WORKTREE_DAYS=${2:?--worktree-days needs a number}; shift ;;
    --log-days) LOG_DAYS=${2:?--log-days needs a number}; shift ;;
    --build-days) BUILD_DAYS=${2:?--build-days needs a number}; shift ;;
    --skip-claude-worktrees) SKIP_CLAUDE_WORKTREES=1 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) print -u2 "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

setopt NULL_GLOB
cd "$HOME" 2>/dev/null || true

CODE=${MACDASH_CODE_DIR:-$(plutil -extract code_dir raw -o - ~/.config/macdash/config.json 2>/dev/null)}
CODE=${CODE:-~/Codebase}; CODE=${CODE/#\~/$HOME}; CODE=${CODE%/}
[[ -d "$CODE" ]] || { echo "Code folder not found: $CODE (set code_dir in ~/.config/macdash/config.json)"; exit 1; }

free() { df -k /System/Volumes/Data | awk 'NR==2{printf "%.1f GB", $4/1048576}'; }
size() { du -sk "$@" 2>/dev/null | awk '{s+=$1} END{printf "%.2f GB", s/1048576}'; }
kb_of() { du -sk "$@" 2>/dev/null | awk '{s+=$1} END{print s+0}'; }
run()  { if (( APPLY )); then echo "  run: $*"; eval "$@"; else echo "  would run: $*"; fi; }

echo "Free disk before: $(free)"; echo

# ---------------------------------------------------------------- 1. Caches & Temp
if (( ! SKIP_CACHES )); then
  echo "== 1. Caches & Temp Files =="
  command -v pnpm >/dev/null && { echo "pnpm store"; run "cd '$HOME' && pnpm store prune"; }
  [[ -d ~/.npm/_cacache ]] && { echo "npm cache ($(size ~/.npm/_cacache))"; run "npm cache clean --force"; }
  command -v uv >/dev/null && [[ -d ~/.cache/uv ]] && { echo "uv cache ($(size ~/.cache/uv))"; run "uv cache prune"; }
  if [[ -d ~/Library/Caches/pip ]]; then
    echo "pip cache ($(size ~/Library/Caches/pip))"
    if command -v pip3 >/dev/null; then run "pip3 cache purge"; else run "rm -rf ~/Library/Caches/pip/*"; fi
  fi
  if command -v brew >/dev/null && [[ -d ~/Library/Caches/Homebrew ]]; then
    hb_kb=$(kb_of ~/Library/Caches/Homebrew)
    if (( hb_kb > 51200 )); then
      echo "Homebrew cache ($(size ~/Library/Caches/Homebrew))"
      run "brew cleanup --prune=all"
    fi
  fi
  [[ -d ~/Library/Caches/com.openai.codex ]] && { echo "Codex cache ($(size ~/Library/Caches/com.openai.codex))"; run "rm -rf ~/Library/Caches/com.openai.codex"; }

  shipit=(~/Library/Caches/*.ShipIt/update.*(N))
  if (( ${#shipit} )); then
    echo "Pending app updater caches ($(size "${shipit[@]}"))"
    run "rm -rf ~/Library/Caches/*.ShipIt/update.*"
  fi

  pytest_dirs=(/private/var/folders/*/*/T/pytest-of-${USER}(N))
  if (( ${#pytest_dirs} )); then
    if pgrep -qf '[p]ytest'; then
      echo "pytest temp ($(size "${pytest_dirs[@]}")) — skipped (pytest running)"
    else
      echo "pytest temp ($(size "${pytest_dirs[@]}"))"
      run "rm -rf /private/var/folders/*/*/T/pytest-of-${USER}"
    fi
  fi

  pw_dirs=(~/Library/Caches/ms-playwright ~/Library/Caches/ms-playwright-mcp ~/Library/Caches/ms-playwright-go)
  pw_found=()
  for d in "${pw_dirs[@]}"; do [[ -d "$d" ]] && pw_found+=("$d"); done
  if (( ${#pw_found} )); then
    if pgrep -qf 'playwright|chrome-headless'; then
      echo "Playwright browser caches ($(size "${pw_found[@]}")) — skipped (Playwright running)"
    else
      echo "Playwright browser caches ($(size "${pw_found[@]}"))"
      run "rm -rf ~/Library/Caches/ms-playwright ~/Library/Caches/ms-playwright-mcp ~/Library/Caches/ms-playwright-go"
    fi
  fi

  chrome_caches=(~/Library/Caches/Google/Chrome/*/Cache(N))
  if (( ${#chrome_caches} )) && (( $(kb_of "${chrome_caches[@]}") > 51200 )); then
    echo "Chrome HTTP cache ($(size "${chrome_caches[@]}"))"
    run "rm -rf ~/Library/Caches/Google/Chrome/*/Cache/*"
  fi

  if [[ -d ~/.local/share/claude/versions ]]; then
    active_bin=$(readlink -f ~/.local/bin/claude 2>/dev/null || true)
    open_bins=$(lsof +D ~/.local/share/claude/versions 2>/dev/null | awk 'NR>1{print $NF}' | sort -u)
    old_bins=()
    for f in ~/.local/share/claude/versions/*(N.); do
      [[ "$f" == "$active_bin" ]] && continue
      print -r -- "$open_bins" | /usr/bin/grep -qxF "$f" && continue
      old_bins+=("$f")
    done
    if (( ${#old_bins} )); then
      echo "Unused Claude CLI binaries (${#old_bins}, $(size "${old_bins[@]}"))"
      for f in "${old_bins[@]}"; do run "rm -f '$f'"; done
    fi
  fi

  if docker info >/dev/null 2>&1; then
    echo "Docker build cache and images no container uses (volumes are kept)"
    run "docker builder prune -af"
    run "docker image prune -af"
  else
    echo "Docker not running: skipped"
  fi
  echo
fi

# ---------------------------------------------------------------- 2. Old AI Session Logs
if (( ! SKIP_LOGS )); then
  echo "== 2. Old AI Session Logs & Backups (>${LOG_DAYS}d) =="
  log_found=0
  for base in ~/.claude/projects ~/.gemini/antigravity/brain; do
    [[ -d "$base" ]] || continue
    read -r cnt kb < <(find "$base" -type f -mtime +${LOG_DAYS} -exec stat -f "%z" {} + 2>/dev/null | awk '{s+=$1; n++} END{printf "%d %d\n", n+0, int(s/1024)}')
    if (( cnt > 0 && kb > 1024 )); then
      log_found=1
      echo "${base/#$HOME/~}: $cnt files ($(awk "BEGIN{printf \"%.2f GB\", $kb/1048576}"))"
      run "find '$base' -type f -mtime +${LOG_DAYS} -delete && find '$base' -type d -empty -delete"
    fi
  done
  for old_dir in ~/.gemini/antigravity-backup ~/.gemini/antigravity-ide; do
    if [[ -d "$old_dir" ]] && (( $(date +%s) - $(stat -f %m "$old_dir") > LOG_DAYS * 86400 )); then
      log_found=1
      echo "${old_dir/#$HOME/~} ($(size "$old_dir"))"
      run "rm -rf '$old_dir'"
    fi
  done
  (( log_found )) || echo "None older than ${LOG_DAYS} days."
  echo
fi

# ---------------------------------------------------------------- 3. Worktrees & Build Caches
INUSE_CWDS=$(lsof -u "$(id -u)" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | sort -u)
PROC_ARGS=$(ps -u $(id -u) -o args= 2>/dev/null)

if (( ! SKIP_WORKTREES )); then
  echo "== 3. Worktrees in ${CODE/#$HOME/~} (>${WORKTREE_DAYS}d, clean, merged, unused) =="
  cutoff=$(date -v-${WORKTREE_DAYS}d +%s)
  total=0; count=0; skipped=0
  repos=()
  while IFS= read -r g; do
    repos+=("${g:h}")
  done < <(find "$CODE" -maxdepth 4 \( -name node_modules -o -name .claude -o -name .venv -o -name venv -o -name .next -o -name .turbo -o -name target -o -name build \) -prune -o -name .git -type d -print 2>/dev/null)

  for repo in "${repos[@]}"; do
    def=$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
    [[ -z "$def" ]] && for b in origin/main origin/master main master staging; do
      git -C "$repo" rev-parse -q --verify "$b" >/dev/null 2>&1 && { def=$b; break; }
    done
    [[ -z "$def" ]] && continue
    while IFS= read -r wt; do
      [[ "$wt" == "$repo" || ! -d "$wt" ]] && continue
      (( SKIP_CLAUDE_WORKTREES )) && [[ "$wt" == */.claude/worktrees/* ]] && continue
      reason=""
      if ! git -C "$wt" rev-parse --git-dir >/dev/null 2>&1; then
        reason="broken git link (inspect manually)"
      fi
      [[ -z "$reason" && -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]] && reason="has changes"
      [[ -z "$reason" ]] && ! git -C "$repo" merge-base --is-ancestor "$(git -C "$wt" rev-parse HEAD 2>/dev/null)" "$def" 2>/dev/null && reason="not merged into $def"
      if [[ -z "$reason" ]] && (( WORKTREE_DAYS > 0 )); then
        wt_ts=$(git -C "$wt" log -1 --format=%ct 2>/dev/null)
        (( ${wt_ts:-0} > cutoff )) && reason="commit under ${WORKTREE_DAYS}d old"
      fi
      [[ -z "$reason" ]] && print -r -- "$INUSE_CWDS" | /usr/bin/grep -qF "$wt" && reason="cwd in use"
      [[ -z "$reason" ]] && print -r -- "$PROC_ARGS" | /usr/bin/grep -qF "$wt" && reason="referenced by running process"
      if [[ -n "$reason" ]]; then
        skipped=$((skipped + 1))
        continue
      fi
      kb=$(du -sk "$wt" 2>/dev/null | cut -f1); total=$((total + kb)); count=$((count + 1))
      echo "${wt#$CODE/}  ($(size "$wt"))"
      run "git -C '$repo' worktree remove '$wt'"
    done < <(git -C "$repo" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0,10)}')
    (( APPLY )) && git -C "$repo" worktree prune 2>/dev/null || true
  done
  echo "Worktrees: $count to remove ($(awk "BEGIN{printf \"%.2f\", $total/1048576}") GB). Kept: $skipped."
  echo
fi

if (( ! SKIP_BUILD )); then
  echo "== 4. Stale .next / .turbo Caches in ${CODE/#$HOME/~} (>${BUILD_DAYS}d, >=100 MB, unused) =="
  b_total=0; b_count=0
  while IFS= read -r bdir; do
    [[ -d "$bdir" ]] || continue
    parent=${bdir:h}
    print -r -- "$INUSE_CWDS" | /usr/bin/grep -qF "$parent" && continue
    print -r -- "$PROC_ARGS" | /usr/bin/grep -qF "$parent" && continue
    kb=$(du -sk "$bdir" 2>/dev/null | cut -f1)
    (( kb < 102400 )) && continue
    b_total=$((b_total + kb)); b_count=$((b_count + 1))
    echo "${bdir#$CODE/}  ($(size "$bdir"))"
    run "rm -rf '$bdir'"
  done < <(find "$CODE" -maxdepth 12 \( -name node_modules -o -name .git -o -name .venv -o -name venv -o -name __pycache__ -o -name target -o -name .dart_tool \) -prune -o \( -name .next -o -name .turbo \) -type d -mtime +${BUILD_DAYS} -print 2>/dev/null)
  echo "Build caches: $b_count to remove ($(awk "BEGIN{printf \"%.2f\", $b_total/1048576}") GB)."
  echo
fi

echo "Free disk after: $(free)"
(( APPLY )) || echo "Dry run only. Run with --apply to do it."
