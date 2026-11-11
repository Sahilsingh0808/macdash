# macdash

A local dashboard that shows why your Mac is slow or running out of memory, and fixes the usual causes in one click.

It shows memory, swap, CPU load and free disk; which apps use them; and what fills the disk. Each problem it finds comes with a suggestion and, where there is one, a button that fixes it. A small watchdog checks every 5 minutes and sends a notification before macOS shows "Your system has run out of application memory".

It runs only on your Mac, at `http://127.0.0.1:47811/`, and stops itself after 20 minutes with no page open.

## What it shows

- **Status**: the most urgent problem (memory, swap, disk or CPU load), with live tiles and 8-hour sparklines.
- **Trends**: swap, memory free, disk free and CPU load over 1 hour, 8 hours, 24 hours or 7 days, with the limit drawn on each chart.
- **Where the RAM goes**: app memory, compressed, wired, cached files and free, as Activity Monitor splits it.
- **Who uses it**: memory or CPU per app, with related processes grouped (all Chrome helpers as one row, VS Code, Antigravity, Cursor, Slack, Docker, Spotlight/Media).
- **What's running**: dev servers (`next dev`, Vite, Django and others) with their size and age, Docker and its containers, Homebrew services (including crash-looping `error` services), Claude Code sessions, detached `tmux` shells, and orphaned `tail` watchers.
- **Disk map**: the whole disk, then the biggest known space users: Docker's disk image, Claude conversation history & VM bundles, Gemini/Antigravity data, Codex data, Xcode DerivedData, simulators, Android SDK (`$ANDROID_HOME` & Homebrew), Flutter/pub, HuggingFace models, Gradle, package & updater caches (`ShipIt`, `pytest`), Playwright, Homebrew downloads, Downloads, plus a toggle in **Biggest space users** to inspect top project folders inside `code_dir`.
- **Clean up**: old `node_modules` and other build folders, stale `.next`/`.turbo` caches inside active projects, finished git worktrees (including safe merged `.claude/worktrees`), old AI session logs (>14 days), unused Claude CLI binaries & VM bundles, Playwright/package/temp caches, Docker build cache.

Every chart has a table view. Light and dark mode follow your system.

## Install

```bash
curl -fsSL https://github.com/Sahilsingh0808/macdash/releases/latest/download/install.sh | zsh
```

This installs into your home folder and never asks for an admin password. It adds the `macdash` command, the memory watchdog and a **Mac Health** app in `~/Applications`, then opens the dashboard.

To install the helper tools at the same time (needs [Homebrew](https://brew.sh)):

```bash
curl -fsSL https://github.com/Sahilsingh0808/macdash/releases/latest/download/install.sh | zsh -s -- --with-tools
```

### Installer options

| Option | What it does |
|---|---|
| `--code-dir PATH` | The folder that holds your projects. Default: the first of `~/Codebase`, `~/code`, `~/Code`, `~/Projects`, `~/Developer`, `~/dev`, `~/src` that exists |
| `--with-tools` | Also installs Stats, Pearcleaner, GrandPerspective, kondo, btop and dust with Homebrew |
| `--no-watchdog` | Skips the memory watchdog |
| `--no-app` | Skips the Mac Health app |
| `--no-open` | Doesn't open the dashboard when done |

To install a specific version, set `MACDASH_VERSION`, for example `MACDASH_VERSION=v1.0.0`.

### From source

```bash
git clone https://github.com/Sahilsingh0808/macdash.git
cd macdash
./install.sh
```

### Requirements

- macOS 12 or later, Apple silicon or Intel.
- Python 3.8 or later. macOS has it once Apple's command line tools are installed (`xcode-select --install`); Homebrew's Python works too.
- Nothing else. The helper tools are optional: the dashboard hides the buttons for tools you don't have.

If the installer says `~/.local/bin` is not on your `PATH`, add it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile && source ~/.zprofile
```

## Use

| Command | What it does |
|---|---|
| `macdash` | Opens the dashboard, starting it if needed |
| `macdash --no-open` | Starts the server without opening a browser |
| `macdash --stop` | Stops the server |
| `macdash --version` | Prints the version |
| `memwatch --report` | Prints swap, free memory, free disk and the 10 biggest memory users |
| `memwatch --test` | Sends a test notification |
| `~/.local/share/macdash/mac-cleanup.sh` | Dry run of caches, old AI logs (>14d), finished git worktrees and stale `.next`/`.turbo` caches. Add `--apply` to remove them (`--help` for options) |

Or open **Mac Health** from Spotlight or `~/Applications`.

The first time the watchdog sends a notification, macOS may ask whether Script Editor can send notifications. Allow it.

## Settings

`~/.config/macdash/config.json`, created by the installer. Every key is optional.

```json
{
  "code_dir": "~/Code",
  "folders": [
    { "name": "Photo exports", "path": "~/Pictures/Exports", "hint": "Move old exports to a drive." }
  ],
  "port": 47811
}
```

- `code_dir`: where the build-folder scan, the worktree scan and the folder-size tool look.
- `folders`: your own folders to measure on the disk map, next to the built-in ones.
- `port`: the dashboard's port. You can also set `MACDASH_PORT`.

The watchdog's limits go in `~/.config/macdash/memwatch.env`:

```bash
SWAP_GB_MAX=8      # default: a quarter of your RAM
FREE_PCT_MIN=15    # memory free percentage, as macOS reports it
DISK_GB_MIN=30     # free disk in GB
LOAD_MAX=36        # default: 3x CPU cores (0 disables)
QUIET_MIN=30       # minutes between repeat notifications for the same problem
```

## Safety

- The server listens on `127.0.0.1` only, so other computers can't reach it. It refuses requests whose `Host` is not `127.0.0.1` or `localhost`, which stops other web pages from talking to it.
- The page can run only the fixed list of actions in `server.py`. Each run needs a token that changes every time the server starts.
- Anything that deletes or stops something asks first and shows the exact command it will run.
- The watchdog only reads numbers and sends notifications. It never stops or deletes anything.
- Nothing is sent anywhere. There is no telemetry and no network access other than the installer's download.

The fixes are the ones you would run by hand: `docker image prune`, `npm cache clean`, `brew cleanup`, `git worktree remove` (without `--force`, so git refuses a worktree with changes), stopping an idle Gradle daemon. Build-folder and DerivedData deletes can't be undone, but the files rebuild on the next install or build.

## Optional tools

The dashboard links to these when they are installed. `--with-tools` installs all of them.

| Tool | What it adds |
|---|---|
| [Stats](https://github.com/exelban/stats) | CPU, RAM and disk in the menu bar |
| [Pearcleaner](https://github.com/alienator88/Pearcleaner) | Removes an app together with its leftover files |
| [GrandPerspective](https://grandperspectiv.sourceforge.net) | A map of what fills the disk |
| [kondo](https://github.com/tbillington/kondo) | Finds and deletes `node_modules`, `target` and other build folders |
| [btop](https://github.com/aristocratos/btop) | A live process view in Terminal |
| [dust](https://github.com/bootandy/dust) | Folder sizes in Terminal |

## Update

Run the install command again. It replaces the program files and keeps your settings and history.

## Uninstall

```bash
~/.local/share/macdash/uninstall.sh
```

This removes the program, the watchdog and the app, and keeps your settings and logs. Add `--all` to delete those too.

## Files it installs

| Path | What it is |
|---|---|
| `~/.local/bin/macdash` | Opens the dashboard |
| `~/.local/bin/memwatch` | The watchdog |
| `~/.local/share/macdash/` | The server, the page, the cleanup script and the uninstaller |
| `~/Library/LaunchAgents/io.github.sahilsingh0808.macdash.memwatch.plist` | Runs the watchdog every 5 minutes |
| `~/Applications/Mac Health.app` | Opens the dashboard from Spotlight or the Dock |
| `~/.config/macdash/` | Your settings |
| `~/Library/Logs/memwatch.log` | One line every 5 minutes. The Trends charts read it |
| `~/Library/Logs/macdash.log` | The server's log |

## License

MIT. See [LICENSE](LICENSE).
