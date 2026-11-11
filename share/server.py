#!/usr/bin/env python3
"""macdash (Mac Health): a small local dashboard for memory, swap, CPU and disk, with one-click fixes.

Serves http://127.0.0.1:47811/ to this Mac only. The page can run only the fixed commands in
ACTIONS below, and only with the token printed into the page it served. The server stops itself
after 20 minutes with no page open, so it costs nothing when you are not looking at it.

Start it with `macdash` (or the "Mac Health" app). Log: ~/Library/Logs/macdash.log
Settings: ~/.config/macdash/config.json (see README).
"""
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.1.0"
HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
UID = os.getuid()
GB = 1 << 30
IDLE_EXIT_S = 20 * 60
TOKEN = secrets.token_urlsafe(24)
MEMWATCH_LOG = f"{HOME}/Library/Logs/memwatch.log"
AGENT = "io.github.sahilsingh0808.macdash.memwatch"
AGENT_PLIST = f"{HOME}/Library/LaunchAgents/{AGENT}.plist"
os.environ["PATH"] = ":".join([
    f"{HOME}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", f"{HOME}/Library/pnpm",
    os.environ.get("PATH", ""), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


def load_config():
    """~/.config/macdash/config.json, every key optional:
    {"code_dir": "~/Code", "folders": [{"name": "Photo exports", "path": "~/Exports", "hint": "Move to a drive."}]}"""
    try:
        with open(f"{HOME}/.config/macdash/config.json") as f:
            c = json.load(f)
            return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


CONFIG = load_config()
PORT = int(os.environ.get("MACDASH_PORT") or CONFIG.get("port") or 47811)


def find_code_dir():
    """Where your projects live: MACDASH_CODE_DIR, then the config file, then the first common folder that exists."""
    for p in (os.environ.get("MACDASH_CODE_DIR"), CONFIG.get("code_dir")):
        if p and os.path.isdir(os.path.expanduser(p)):
            return os.path.expanduser(p).rstrip("/")
    for name in ("Codebase", "code", "Code", "Projects", "projects", "Developer", "dev", "src", "workspace", "repos"):
        if os.path.isdir(f"{HOME}/{name}"):
            return f"{HOME}/{name}"
    return HOME


CODE_DIR = find_code_dir()
CODE = CODE_DIR.replace(HOME, "~", 1)  # for display
os.environ["MACDASH_CODE_DIR"] = CODE_DIR  # mac-cleanup.sh reads it

last_seen = time.time()


def sh(cmd, timeout=10):
    """Run a command and return its stdout, or "" if it fails or times out."""
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except Exception:
        return ""


# ---------------------------------------------------------------- readings

def memory():
    total = int(sh(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    free_pct = int(sh(["sysctl", "-n", "kern.memorystatus_level"]).strip() or 0)
    swap = dict(re.findall(r"(total|used) = ([\d.]+)M", sh(["sysctl", "-n", "vm.swapusage"])))
    vm = sh(["vm_stat"])
    page = int((re.search(r"page size of (\d+)", vm) or [0, 16384])[1])
    pg = lambda name: int((re.search(name + r":\s+(\d+)", vm) or [0, 0])[1]) * page / GB
    comp = pg("occupied by compressor")
    app = max(0.0, pg("Anonymous pages") - pg("Pages purgeable"))
    wired = pg("Pages wired down")
    cached = pg("File-backed pages") + pg("Pages purgeable")
    rest = max(0.0, total / GB - app - comp - wired - cached)
    return {"total_gb": total / GB, "free_pct": free_pct,
            "swap_used_gb": float(swap.get("used", 0)) / 1024,
            "swap_total_gb": float(swap.get("total", 0)) / 1024,
            "compressed_gb": comp,
            # the same split Activity Monitor shows; "free" is what is left of physical RAM
            "parts": {"app": app, "compressed": comp, "wired": wired, "cached": cached, "free": rest}}


def cpu():
    load = [float(x) for x in re.findall(r"[\d.]+", sh(["sysctl", "-n", "vm.loadavg"]))[:3]] or [0, 0, 0]
    return {"load": load, "ncpu": int(sh(["sysctl", "-n", "hw.ncpu"]).strip() or 1)}


def disk():
    st = os.statvfs("/System/Volumes/Data")
    return {"free_gb": st.f_bavail * st.f_frsize / GB, "total_gb": st.f_blocks * st.f_frsize / GB}


GROUPS = [  # first match wins; Claude first because its command line names other tools
    ("Claude sessions", r"claude-code/.*/claude|/\.local/share/claude/|/\.local/bin/claude"),
    ("Claude app", r"/Claude\.app/"),
    ("Docker / OrbStack", r"Virtualization\.VirtualMachine|com\.docker|OrbStack"),
    ("Android emulator", r"qemu-system|/emulator/emulator"),
    ("Gradle / Kotlin", r"GradleDaemon|KotlinCompileDaemon|GradleWrapperMain"),
    ("vitest", r"vitest"),
    ("next dev", r"next-server|next dev|next/dist"),
    ("tsc", r"/tsc\b|typescript/lib"),
    ("Django / Celery", r"manage\.py|celery"),
    ("Flutter / Dart", r"dartaotruntime|flutter|/dart "),
    ("CodeGraph MCP", r"codegraph"),
    ("Playwright", r"playwright|chrome-headless"),
    ("Medley", r"medley"),
    ("Spotlight & Media", r"mediaanalysisd|photolibraryd|spotlightknowledged|mds_stores|mdworker"),
    ("Chrome", r"/Google Chrome\.app/"),
    ("VS Code", r"/Visual Studio Code\.app/"),
    ("Antigravity", r"/Antigravity\.app/"),
    ("Cursor", r"/Cursor\.app/"),
    ("Slack", r"/Slack\.app/"),
    ("WhatsApp", r"/WhatsApp\.app/"),
    ("ChatGPT", r"/ChatGPT\.app/"),
    ("Comet", r"/Comet\.app/"),
    ("Python", r"[Pp]ython"),
    ("Node", r"\bnode\b"),
]
DEV_SERVER = re.compile(r"next-server|next dev|vite|webpack|react-scripts|astro|nuxt|storybook|"
                        r"manage\.py runserver|uvicorn|gunicorn|flask|expo|metro|turbo")


def _proc_group(args):
    for g, rx in GROUPS:
        if re.search(rx, args):
            return g
    m = re.search(r"/([^/]+)\.app/", args)
    if m:
        return m[1][:40]
    return os.path.basename(args.split(" ")[0])[:40]


def to_bytes(s):
    m = re.match(r"([\d.]+)([BKMG])", s)
    if not m:
        return 0
    return float(m[1]) * {"B": 1, "K": 1 << 10, "M": 1 << 20, "G": GB}[m[2]]


def etime_s(s):
    d, _, rest = s.strip().rpartition("-")
    parts = [int(x) for x in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(d) if d else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def processes():
    """Every process with its true memory (top's MEM, which counts swapped and compressed
    pages; ps RSS hides them), CPU, age and command line."""
    procs = {}
    for line in sh(["ps", "-Ao", "pid=,ppid=,pcpu=,etime=,args="]).splitlines():
        f = line.split(None, 4)
        if len(f) == 5:
            procs[int(f[0])] = {"pid": int(f[0]), "ppid": int(f[1]), "cpu": float(f[2]),
                                "age_s": etime_s(f[3]), "args": f[4], "mem": 0}
    for line in sh(["top", "-l", "1", "-o", "mem", "-n", "200", "-stats", "pid,mem"], 15).splitlines():
        m = re.match(r"\s*(\d+)\s+([\d.]+[BKMG])", line)
        if m and int(m[1]) in procs:
            procs[int(m[1])]["mem"] = to_bytes(m[2])
    for p in procs.values():
        p["group"] = _proc_group(p["args"])
    return procs


def claude_parent(procs, pid):
    seen = 0
    while pid in procs and seen < 40:
        if procs[pid]["group"] == "Claude sessions":
            return pid
        pid, seen = procs[pid]["ppid"], seen + 1
    return None


def dev_servers(procs):
    out, pid = [], None
    for line in sh(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], 8).splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid in procs and DEV_SERVER.search(procs[pid]["args"]):
            port = line.rsplit(":", 1)[-1]
            if any(s["pid"] == pid for s in out):
                continue
            cwd = sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).split("\nn")[-1].strip()
            p = procs[pid]
            out.append({"pid": pid, "port": port, "cwd": cwd.replace(HOME, "~"),
                        "mem_gb": p["mem"] / GB, "age_s": p["age_s"], "cmd": p["args"][:120],
                        "claude": claude_parent(procs, pid)})
    return out


_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def docker(procs):
    running = any(re.search(r"com\.docker\.(backend|virtualization)|Docker\.app/Contents/MacOS",
                            p["args"]) for p in procs.values())
    vm = sum(p["mem"] for p in procs.values() if p["group"] == "Docker / OrbStack")
    info = {"running": running, "vm_gb": vm / GB, "containers": 0, "stacks": [], "ok": True}
    if running:
        out = sh(["docker", "ps", "--format", '{{.Label "com.docker.compose.project"}}|{{.Names}}'], 5)
        if not out and sh(["docker", "info", "--format", "{{.ServerVersion}}"], 5) == "":
            info["ok"] = False
        rows = [r.split("|", 1) for r in out.splitlines() if "|" in r]
        info["containers"] = len(rows)
        info["stacks"] = sorted({proj or name.split("_")[-1] for proj, name in rows})
    return info


def brew_services():
    svcs = []
    for line in sh(["brew", "services", "list"], 20).splitlines()[1:]:
        f = line.split()
        if len(f) > 1 and f[1] in ("started", "error"):
            svcs.append({"name": f[0], "status": f[1]})
    return svcs


def tmux_info(procs):
    if not shutil.which("tmux"):
        return {"total": 0, "attached": 0, "detached": 0, "dead_shells": []}
    out = sh(["tmux", "list-sessions", "-F", "#{session_name}|#{session_attached}"], 5)
    if not out:
        return {"total": 0, "attached": 0, "detached": 0, "dead_shells": []}
    children = {}
    for p in procs.values():
        children.setdefault(p["ppid"], []).append(p["pid"])
    total = attached = detached = 0
    dead_shells = []
    for line in out.splitlines():
        name, _, att = line.partition("|")
        if not name:
            continue
        total += 1
        if att == "1":
            attached += 1
            continue
        detached += 1
        panes = sh(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}|#{pane_current_command}"], 3).splitlines()
        if panes and all(
            cmd in ("bash", "zsh", "sh", "fish") and pid.isdigit() and not children.get(int(pid))
            for pid, cmd in (r.split("|", 1) for r in panes if "|" in r)
        ):
            dead_shells.append(name)
    return {"total": total, "attached": attached, "detached": detached, "dead_shells": dead_shells}


def orphan_watchers(procs):
    out = []
    for p in procs.values():
        if p["ppid"] == 1 and p["age_s"] > 1800 and re.search(r"\btail\s+.*-[fF]\b|until grep -q", p["args"]):
            out.append({"pid": p["pid"], "age_s": p["age_s"], "cmd": p["args"][:100]})
    return out


def _expand_paths(spec):
    raw = [spec] if isinstance(spec, str) else list(spec)
    seen, out = set(), []
    for item in raw:
        if not item:
            continue
        expanded = os.path.expanduser(item)
        matches = glob.glob(expanded) if any(ch in expanded for ch in "*?[") else [expanded]
        for m in matches:
            if os.path.exists(m):
                rp = os.path.realpath(m)
                if rp not in seen:
                    seen.add(rp)
                    out.append(m)
    return out


def _any_path_exists(spec):
    return bool(_expand_paths(spec))


def _du_kb(paths):
    if not paths:
        return 0
    total = 0
    for line in sh(["du", "-sk"] + paths, 300).splitlines():
        parts = line.split(None, 1)
        if parts and parts[0].isdigit():
            total += int(parts[0])
    return total


def _android_paths():
    return [p for p in (os.environ.get("ANDROID_HOME"), "/opt/homebrew/share/android-commandlinetools",
                        "~/Library/Android/sdk") if p]


BIG = [  # name, path_spec, hint, action
    ("Docker disk image", "~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw",
     "Shrinks after Docker prune; `docker volume prune` for old databases.", "docker-prune"),
    ("OrbStack data", "~/.orbstack", "Container and Linux VM data.", None),
    ("Colima VMs", "~/.colima", "Lima/Colima VM disk images. Remove if you use Docker Desktop or OrbStack.", None),
    ("Claude conversation history", "~/.claude/projects",
     "Session transcripts. Prune logs older than 14 days.", "ai-logs-clean"),
    ("Claude app VM bundles", "~/Library/Application Support/Claude/vm_bundles",
     "Cowork Linux VM image. Re-downloads automatically when needed.", "claude-vm-clean"),
    ("Claude CLI binaries", "~/.local/share/claude/versions",
     "Old CLI binaries kept beside the active version.", "claude-versions-clean"),
    ("Gemini / Antigravity data", "~/.gemini",
     "Brain artifacts, conversations and old IDE backups.", "ai-logs-clean"),
    ("Codex data & images", "~/.codex",
     "Session logs and generated tool images (`~/.codex/generated_images`).", None),
    ("Playwright browsers", ["~/Library/Caches/ms-playwright", "~/Library/Caches/ms-playwright-mcp",
                             "~/Library/Caches/ms-playwright-go"],
     "Headless browsers downloaded for tests and Playwright MCP.", "playwright-clean"),
    ("Android SDK & tools", _android_paths(), "Platforms, NDKs and system images.", None),
    ("Android emulator images", "~/.android/avd", "Delete unused emulators in Android Studio.", None),
    ("Gradle caches", "~/.gradle/caches", "Rebuilds on the next build.", None),
    ("Flutter & Dart pub cache", ["~/flutter", "~/.pub-cache"], "Flutter SDK and cached Dart packages.", None),
    ("HuggingFace models", "~/.cache/huggingface", "Local ML models (Whisper, TTS, embeddings).", None),
    ("Updater & temp caches", ["~/Library/Caches/*.ShipIt/update.*", "/private/var/folders/*/*/T/pytest-of-*",
                               "~/Library/Caches/Google/Chrome/*/Cache"],
     "Pending app updates, pytest temp files and Chrome HTTP cache.", "caches"),
    ("Xcode DerivedData", "~/Library/Developer/Xcode/DerivedData", "Build products. Xcode rebuilds them.", "derived-data"),
    ("iOS simulators", "~/Library/Developer/CoreSimulator/Devices", "Removes simulators for iOS versions you no longer have.", "sim-prune"),
    ("npm cache", "~/.npm", "Rebuilds on the next install.", "caches"),
    ("pnpm store", "~/Library/pnpm/store", "Rebuilds on the next install.", "caches"),
    ("uv cache", "~/.cache/uv", "Rebuilds on the next install.", "caches"),
    ("Homebrew downloads", "~/Library/Caches/Homebrew", "Old downloads. `brew cleanup` removes them.", "brew-cleanup"),
    ("iOS device support", "~/Library/Developer/Xcode/iOS DeviceSupport",
     "Debug symbols for iPhones you once plugged in. Xcode fetches them again.", None),
    ("Xcode archives", "~/Library/Developer/Xcode/Archives", "Old app builds. Delete them in Xcode's Organizer.", None),
    ("WhatsApp media", "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared",
     "Downloaded chat photos and videos.", None),
    ("Downloads", "~/Downloads", "Your files. Sort them yourself.", None),
]
# your own folders come from the config file, so private paths never live in this code
BIG += [(f.get("name") or f["path"], f["path"], f.get("hint") or "Your files.", None)
        for f in CONFIG.get("folders", []) if isinstance(f, dict) and f.get("path")]
big_folders = {"at": 0, "items": [], "code_items": [], "scanning": False}


def scan_big_folders():
    big_folders["scanning"] = True
    items = []
    for name, path_spec, hint, action in BIG:
        paths = _expand_paths(path_spec)
        if not paths:
            continue
        kb = _du_kb(paths)
        if kb:
            disp = path_spec if isinstance(path_spec, str) else ", ".join(path_spec)
            items.append({"name": name, "path": disp, "gb": kb / (1 << 20), "hint": hint, "action": action})
    code_items = []
    if CODE_DIR != HOME and os.path.isdir(CODE_DIR):
        subdirs = [e.path for e in os.scandir(CODE_DIR) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
        for line in sh(["du", "-sk"] + subdirs, 300).splitlines() if subdirs else []:
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                gb_val = int(parts[0]) / (1 << 20)
                if gb_val >= 0.05:
                    code_items.append({"name": os.path.basename(parts[1]), "path": parts[1].replace(HOME, "~", 1),
                                       "gb": gb_val, "hint": "Project folder (check worktrees & .next builds).",
                                       "action": None})
        code_items.sort(key=lambda x: -x["gb"])
    big_folders.update(items=sorted(items, key=lambda x: -x["gb"]), code_items=code_items[:15],
                       at=time.time(), scanning=False)


def big_folders_loop():
    while True:
        scan_big_folders()
        time.sleep(3600)


# ---------------------------------------------------------------- suggestions

def suggestions(s):
    out = []
    add = lambda sev, title, detail, actions=(): out.append(
        {"sev": sev, "title": title, "detail": detail, "actions": list(actions)})
    d, m, g = s["disk"], s["memory"], {x["name"]: x for x in s["groups"]}
    if d["free_gb"] < 15:
        add("crit", f"Disk almost full: {d['free_gb']:.1f} GB free",
            "macOS swaps memory to disk. With no free disk, swap can't grow and apps freeze. "
            "Free space with the steps below.", ["caches", "worktrees-scan", "ai-logs-clean", "docker-prune"])
    elif d["free_gb"] < 30:
        add("warn", f"Disk getting low: {d['free_gb']:.1f} GB free",
            "Keep at least 30 GB free so swap has room.", ["worktrees-scan", "next-scan", "caches", "ai-logs-clean"])
    if m["free_pct"] < 15:
        add("crit", f"Memory nearly full: {m['free_pct']}% free",
            "Quit the biggest users in 'Memory by app' now.", ["open-activity"])
    if m["swap_used_gb"] > max(2, m["total_gb"] / 4):  # a quarter of RAM: 6 GB on a 24 GB Mac
        top3 = ", ".join(f"{x['name']} {x['gb']:.1f} GB" for x in s["groups"][:3])
        add("warn", f"Swap is high: {m['swap_used_gb']:.1f} GB",
            f"Biggest users: {top3}. Stopping one of them brings swap down.")
    c = s["cpu"]
    if c["load"][0] > c["ncpu"] and s["cpu_groups"]:
        top_cpu = s["cpu_groups"][0]
        if any(p["cpu"] >= 80 and "mediaanalysisd" in p["args"] for p in s["_procs"]):
            add("warn", "mediaanalysisd is burning CPU in the background",
                "macOS Photos/Visual Look Up indexing is spinning a core. Restarting it often clears a wedged scan.",
                ["mediaanalysis-restart"])
        elif top_cpu["cpu"] >= 80:
            add("warn" if c["load"][0] > 2 * c["ncpu"] else "info",
                f"{top_cpu['name']} is using {top_cpu['cpu']:.0f}% CPU",
                f"Load average is {c['load'][0]:.1f} on {c['ncpu']} cores. Check the CPU view to see what is spinning.")
    dk = s["docker"]
    if dk["running"] and not dk["ok"]:
        add("crit", "Docker is not responding",
            "It holds memory and can fill the disk while stuck. Quit it and start it again when needed.",
            ["docker-quit"])
    elif dk["running"] and dk["containers"] == 0:
        add("warn", f"Docker is running with no containers ({dk['vm_gb']:.1f} GB)",
            "Nothing uses it. Quitting gives the memory back.", ["docker-quit"])
    elif dk["running"]:
        add("info", f"Docker: {dk['containers']} containers, {dk['vm_gb']:.1f} GB",
            "Stacks: " + (", ".join(dk["stacks"]) or "none") +
            ". Quit Docker when you are not using these; volumes are kept.", ["docker-quit"])
    gr = [p for p in s["_procs"] if p["group"] == "Gradle / Kotlin"]
    if gr and all(p["cpu"] < 1 for p in gr):
        add("warn", f"Idle Gradle daemons: {sum(p['mem'] for p in gr) / GB:.1f} GB",
            "No build is running. The next build starts them again.", ["gradle-stop"])
    if "Android emulator" in g and g["Android emulator"]["gb"] > 0.5:
        add("info", f"Android emulator running: {g['Android emulator']['gb']:.1f} GB",
            "Shut it down when you are done testing.", ["emulator-stop"])
    for srv in s["dev_servers"]:
        if srv["mem_gb"] > 4 or srv["age_s"] > 4 * 3600:
            who = "started by a Claude session" if srv["claude"] else "not started by Claude"
            add("warn", f"Dev server on :{srv['port']} is {srv['mem_gb']:.1f} GB, up {srv['age_s'] // 3600} h",
                f"{srv['cwd']} ({who}). Dev servers grow and never give memory back. "
                "Stop it if nobody is using it.", [f"stop-server:{srv['pid']}"])
    cs = g.get("Claude sessions")
    if cs and cs.get("sessions", 0) > 8:
        mcp = sum(g[k]["gb"] for k in ("CodeGraph MCP", "Playwright", "Medley") if k in g)
        add("warn", f"{cs['sessions']} Claude sessions open: {cs['gb']:.1f} GB",
            f"Their MCP servers add {mcp:.1f} GB. Finish or close idle sessions; each keeps its own MCP servers alive.")
    tm = s["tmux"]
    if tm["dead_shells"]:
        add("info", f"{len(tm['dead_shells'])} idle detached tmux shell(s)",
            "Detached tmux sessions sitting at a bare prompt with no active command.",
            ["tmux-scan", "tmux-clean-dead"])
    elif tm["detached"] >= 4:
        add("info", f"{tm['detached']} detached tmux sessions in background",
            "Check if finished sessions are keeping agents or dev servers alive.", ["tmux-scan"])
    if len(s["orphans"]) >= 2:
        add("info", f"{len(s['orphans'])} orphaned log watchers (parent exited)",
            "Old tail -f / polling watchers left running after their parent session closed.", ["orphans-clean"])
    if "vitest" in g and g["vitest"]["count"] > 6:
        add("info", f"vitest is running {g['vitest']['count']} processes ({g['vitest']['gb']:.1f} GB)",
            "Cap workers with maxWorkers: 4 in that repo's vitest config.")
    for svc in s["brew_services"]:
        if svc["status"] == "error":
            add("warn", f"Homebrew {svc['name']} is in an error loop",
                "It may be crash-looping every 10 seconds (often from a stale lockfile like postmaster.pid). "
                "Stop it to clear the lockfile and stop the retries.", [f"brew-stop:{svc['name']}"])
        elif re.match(r"postgresql|redis|mysql|mongodb", svc["name"]):
            add("info", f"Homebrew {svc['name']} runs all the time",
                "Docker already runs its own copy for most projects. Stop it if you don't use it.",
                [f"brew-stop:{svc['name']}"])
    if not s["memwatch"]:
        add("warn", "Memory watchdog is off", "It warns you before the Mac freezes.", ["memwatch-load"])
    if not out:
        add("ok", "All good", "Memory, swap and disk are within limits.")
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    return sorted(out, key=lambda x: order[x["sev"]])


HIST_RX = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) swap=([\d.]+)G free=(\d+)% disk=([\d.]+)G(?: load=([\d.]+))?")


def history(hours=8):
    """Swap, memory free and disk free from the watchdog log, one point every 5 minutes."""
    try:
        with open(os.path.expanduser("~/Library/Logs/memwatch.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - min(2_000_000, int(hours * 2000) + 20_000)))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return []
    cutoff = time.time() - hours * 3600
    out = []
    for ln in lines:
        m = HIST_RX.match(ln)
        if not m:
            continue
        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        if t >= cutoff:
            out.append({"t": t, "swap": float(m.group(2)), "free": int(m.group(3)), "disk": float(m.group(4)),
                        "load": float(m.group(5)) if m.group(5) else None})
    return out


_status_lock = threading.Lock()


def status():
    with _status_lock:
        hit = _cache.get("status")
        if hit and time.time() - hit[0] < 3:
            return hit[1]
        procs = processes()
        groups = {}
        for p in procs.values():
            e = groups.setdefault(p["group"], {"name": p["group"], "gb": 0, "cpu": 0, "count": 0})
            e["gb"] += p["mem"] / GB
            e["cpu"] += p["cpu"]
            e["count"] += 1
        # a session's own helpers share its group; count only the top process of each session
        sessions = [p for p in procs.values() if p["group"] == "Claude sessions"
                    and procs.get(p["ppid"], {}).get("group") != "Claude sessions"]
        if "Claude sessions" in groups:
            groups["Claude sessions"]["sessions"] = len(sessions)
        s = {
            "at": time.time(),
            "memory": memory(),
            "disk": disk(),
            "groups": sorted(groups.values(), key=lambda x: -x["gb"])[:12],
            "cpu_groups": [g for g in sorted(groups.values(), key=lambda x: -x["cpu"])[:10] if g["cpu"] >= 0.5],
            "cpu": cpu(),
            "dev_servers": dev_servers(procs),
            "docker": cached("docker", 15, lambda: docker(procs)),
            "brew_services": cached("brew", 300, brew_services),
            "tmux": tmux_info(procs),
            "orphans": orphan_watchers(procs),
            "memwatch": cached("memwatch", 60, lambda: subprocess.run(
                ["launchctl", "print", f"gui/{UID}/{AGENT}"], capture_output=True).returncode == 0),
            "version": VERSION,
            "code_dir": CODE,
            "big_folders": big_folders,
            "scans": scans,
            "history": cached("history", 60, history),
            "_procs": list(procs.values()),
        }
        s["suggestions"] = suggestions(s)
        del s["_procs"]
        _cache["status"] = (time.time(), s)
        return s


# ---------------------------------------------------------------- actions

def open_app(name):
    return {"label": f"Open {name}", "cmd": ["open", "-a", name], "confirm": False,
            "desc": f"Opens {name}.", "app": name}


def has_app(name):
    return any(os.path.isdir(f"{d}/{name}.app") for d in
               ("/Applications", f"{HOME}/Applications", "/System/Applications", "/System/Applications/Utilities"))


def package_caches_cmd():
    parts = [f"cd '{HOME}'"]
    for tool, c in (("npm", "npm cache clean --force"), ("pnpm", "pnpm store prune"),
                    ("uv", "uv cache prune"), ("pip3", "pip3 cache purge")):
        if shutil.which(tool):
            parts.append(c)
    parts.append("rm -rf ~/Library/Caches/*.ShipIt/update.* /private/var/folders/*/*/T/pytest-of-${USER} ~/Library/Caches/com.openai.codex 2>/dev/null || true")
    return "; ".join(parts)


def gradle_stop(_arg):
    procs = processes()
    pids = [p["pid"] for p in procs.values() if p["group"] == "Gradle / Kotlin"]
    busy = [p["pid"] for p in procs.values() if p["group"] == "Gradle / Kotlin" and p["cpu"] >= 1]
    if busy:
        return 1, f"Not stopped: a Gradle process is using CPU (pid {busy}), so a build may be running."
    for pid in pids:
        os.kill(pid, 15)
    return 0, f"Sent a stop to {len(pids)} Gradle/Kotlin daemons: {pids}"


def stop_tree(arg):
    pid = int(arg)
    if pid not in {s["pid"] for s in status()["dev_servers"]}:
        return 1, "That process is no longer a listed dev server."
    procs = processes()

    def kids(p):
        return [c["pid"] for c in procs.values() if c["ppid"] == p]

    order, stack = [], [pid]
    while stack:
        p = stack.pop()
        order.append(p)
        stack += kids(p)
    for p in reversed(order):
        try:
            os.kill(p, 15)
        except ProcessLookupError:
            pass
    return 0, f"Sent a stop to {len(order)} processes: {order}"


def brew_stop(arg):
    if arg not in {s["name"] for s in brew_services()}:
        return 1, f"{arg} is not started or errored."
    r = subprocess.run(["brew", "services", "stop", arg], capture_output=True, text=True, cwd=HOME)
    notes = []
    if arg.startswith("postgresql"):
        for base in ("/opt/homebrew/var", "/usr/local/var"):
            pidfile = os.path.join(base, arg, "postmaster.pid")
            if os.path.isfile(pidfile):
                try:
                    pid = open(pidfile).readline().strip()
                    comm = sh(["ps", "-o", "comm=", "-p", pid]).strip() if pid.isdigit() else ""
                    if "postgres" not in comm:
                        os.remove(pidfile)
                        notes.append(f"Removed stale lockfile: {pidfile}")
                except OSError:
                    pass
    _cache.pop("brew", None)
    return r.returncode, (r.stdout + r.stderr + ("\n" + "\n".join(notes) if notes else "")).strip()


def playwright_clean(_arg):
    procs = processes()
    if any(re.search(r"playwright|chrome-headless", p["args"], re.I) for p in procs.values()):
        return 1, "Skipped: a Playwright or headless Chromium process is currently running."
    targets = _expand_paths(["~/Library/Caches/ms-playwright", "~/Library/Caches/ms-playwright-mcp",
                             "~/Library/Caches/ms-playwright-go"])
    if not targets:
        return 0, "No Playwright caches found."
    kb = _du_kb(targets)
    for t in targets:
        shutil.rmtree(t, ignore_errors=True)
    return 0, f"Removed {len(targets)} Playwright cache folder(s) (~{kb / (1 << 20):.2f} GB)."


def claude_versions_clean(_arg):
    vdir = os.path.expanduser("~/.local/share/claude/versions")
    if not os.path.isdir(vdir):
        return 0, "No Claude CLI versions folder found."
    active = os.path.realpath(os.path.expanduser("~/.local/bin/claude")) if os.path.exists(
        os.path.expanduser("~/.local/bin/claude")) else ""
    opened = set()
    for ln in sh(["lsof", "+D", vdir], 10).splitlines()[1:]:
        parts = ln.split()
        if parts:
            opened.add(os.path.realpath(parts[-1]))
    removed, freed_kb = [], 0
    for entry in os.scandir(vdir):
        if not entry.is_file(follow_symlinks=False):
            continue
        rp = os.path.realpath(entry.path)
        if rp == active or rp in opened:
            continue
        freed_kb += _du_kb([entry.path])
        os.remove(entry.path)
        removed.append(entry.name)
    if not removed:
        return 0, "No unused Claude CLI binaries found (active or open versions kept)."
    return 0, f"Removed {len(removed)} unused Claude binary/binaries ({', '.join(removed)}), ~{freed_kb / (1 << 20):.2f} GB."


def claude_vm_clean(_arg):
    vmdir = os.path.expanduser("~/Library/Application Support/Claude/vm_bundles")
    if not os.path.isdir(vmdir):
        return 0, "No Claude VM bundles folder found."
    procs = processes()
    if any("Claude" in p["args"] and "Virtualization" in p["args"] for p in procs.values()):
        return 1, "Skipped: a Claude VM process is currently running."
    kb = _du_kb([vmdir])
    shutil.rmtree(vmdir, ignore_errors=True)
    return 0, f"Removed Claude vm_bundles (~{kb / (1 << 20):.2f} GB)."


def tmux_scan(_arg):
    if not shutil.which("tmux"):
        return 1, "tmux is not installed."
    out = sh(["tmux", "list-sessions", "-F",
              "#{session_name}\tattached=#{session_attached}\tcreated=#{t:session_created}"], 5)
    if not out.strip():
        return 0, "No tmux server or sessions running."
    procs = processes()
    dead = set(tmux_info(procs)["dead_shells"])
    lines = []
    for row in out.splitlines():
        name = row.split("\t", 1)[0]
        panes = sh(["tmux", "list-panes", "-t", name, "-F", "#{pane_current_command} #{pane_current_path}"], 3).splitlines()
        tag = " [idle detached shell]" if name in dead else ""
        lines.append(f"{row}{tag} | {', '.join(panes)}")
    return 0, "\n".join(lines)


def tmux_clean_dead(_arg):
    if not shutil.which("tmux"):
        return 1, "tmux is not installed."
    dead = tmux_info(processes())["dead_shells"]
    if not dead:
        return 0, "No idle detached bare-shell tmux sessions to clean."
    for s in dead:
        subprocess.run(["tmux", "kill-session", "-t", s], capture_output=True, text=True)
    return 0, f"Killed {len(dead)} idle detached tmux shell(s):\n" + "\n".join(dead)


def orphans_clean(_arg):
    orphans = orphan_watchers(processes())
    if not orphans:
        return 0, "No orphaned log watchers found."
    killed = []
    for o in orphans:
        try:
            os.kill(o["pid"], 15)
            killed.append(f"{o['pid']} ({o['cmd'][:60]})")
        except ProcessLookupError:
            pass
    return 0, f"Stopped {len(killed)} orphaned watcher(s):\n" + "\n".join(killed)


def mediaanalysis_restart(_arg):
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{UID}/com.apple.mediaanalysisd"],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip() or "Restarted com.apple.mediaanalysisd."


ACTIONS = {
    "open-stats": open_app("Stats"),
    "open-grandperspective": open_app("GrandPerspective"),
    "open-pearcleaner": open_app("Pearcleaner"),
    "open-orbstack": open_app("OrbStack"),
    "open-activity": open_app("Activity Monitor"),
    "open-docker": open_app("Docker"),
    "btop": {"label": "Open btop", "confirm": False, "desc": "Opens btop in a Terminal window.", "bin": "btop",
             "cmd": ["osascript", "-e", 'tell application "Terminal" to do script "btop"',
                     "-e", 'tell application "Terminal" to activate']},
    "dust": {"label": "Biggest folders in your code folder", "confirm": False, "timeout": 300, "bin": "dust",
             "desc": f"dust -d 1 -n 25 {CODE}. Deletes nothing.",
             "cmd": ["dust", "-d", "1", "-n", "25", "-b", CODE_DIR]},
    "memwatch-log": {"label": "Watchdog log", "confirm": False, "desc": "Last 40 watchdog checks.",
                     "cmd": ["tail", "-n", "40", MEMWATCH_LOG]},
    "memwatch-report": {"label": "Watchdog report", "confirm": False, "bin": "memwatch",
                        "desc": "memwatch --report", "cmd": ["memwatch", "--report"]},
    "memwatch-load": {"label": "Turn watchdog on", "confirm": False, "file": AGENT_PLIST,
                      "desc": f"Loads ~/Library/LaunchAgents/{AGENT}.plist (runs every 5 minutes).",
                      "cmd": ["launchctl", "bootstrap", f"gui/{UID}", AGENT_PLIST]},
    "gradle-stop": {"label": "Stop Gradle daemons", "confirm": True, "fn": gradle_stop,
                    "desc": "Sends a normal stop to every GradleDaemon and KotlinCompileDaemon. "
                            "Refuses if one is using CPU (a build may be running). The next build starts them again."},
    "emulator-stop": {"label": "Shut down emulator", "confirm": True, "cmd": ["adb", "emu", "kill"], "bin": "adb",
                      "desc": "adb emu kill: shuts the Android emulator down like closing its window."},
    "docker-quit": {"label": "Quit Docker", "confirm": True, "app": "Docker",
                    "cmd": ["osascript", "-e", 'quit app "Docker"'],
                    "desc": "Quits Docker Desktop. Every container stops, including Supabase stacks. "
                            "Their data (volumes) is kept. A session that needs a database will fail until Docker starts again."},
    "docker-prune": {"label": "Prune Docker", "confirm": True, "timeout": 900, "bin": "docker",
                     "cmd": "docker builder prune -af; docker image prune -af",
                     "desc": "Deletes Docker build cache and images no container uses. Volumes (database data) "
                             "are kept. Images download again when a stack needs them. Docker must be running."},
    "caches": {"label": "Clear package & temp caches", "confirm": True, "timeout": 900,
               "cmd": package_caches_cmd(),
               "desc": "Clears npm, pnpm, uv, pip caches, pending ShipIt updates, and pytest temp folders. Everything rebuilds when needed."},
    "brew-cleanup": {"label": "Clean Homebrew", "confirm": True, "timeout": 900, "bin": "brew",
                     "cmd": ["brew", "cleanup", "--prune=all"],
                     "desc": "brew cleanup --prune=all: deletes old downloads and old versions of installed packages."},
    "playwright-clean": {"label": "Clear Playwright caches", "confirm": True, "fn": playwright_clean,
                         "file": ["~/Library/Caches/ms-playwright", "~/Library/Caches/ms-playwright-mcp",
                                  "~/Library/Caches/ms-playwright-go"],
                         "desc": "Removes cached Playwright and headless MCP browser binaries. Skips if Playwright is running."},
    "ai-logs-clean": {"label": "Prune AI logs (>14d)", "confirm": True, "timeout": 600,
                      "file": ["~/.claude/projects", "~/.gemini/antigravity/brain"],
                      "cmd": "find ~/.claude/projects ~/.gemini/antigravity/brain -type f -mtime +14 -delete 2>/dev/null; "
                             "find ~/.claude/projects ~/.gemini/antigravity/brain -type d -empty -delete 2>/dev/null; "
                             "echo 'Pruned AI session files older than 14 days.'",
                      "desc": "Deletes Claude Code and Antigravity session transcripts/artifacts older than 14 days. Keeps the last 2 weeks."},
    "claude-versions-clean": {"label": "Remove old Claude CLIs", "confirm": True, "fn": claude_versions_clean,
                              "file": "~/.local/share/claude/versions",
                              "desc": "Deletes unused binaries in ~/.local/share/claude/versions while keeping the active and open versions."},
    "claude-vm-clean": {"label": "Remove Claude Cowork VM", "confirm": True, "fn": claude_vm_clean,
                        "file": "~/Library/Application Support/Claude/vm_bundles",
                        "desc": "Deletes Claude Desktop's Linux VM image (~10+ GB). Re-downloads automatically if Cowork is used."},
    "kondo-scan": {"label": "Scan build folders", "confirm": False, "timeout": 900, "bin": "kondo",
                   "cmd": ["kondo", "-n", "-o", "14d", CODE_DIR],
                   "desc": f"Lists node_modules, .dart_tool, __pycache__, target and similar in {CODE} projects "
                           "untouched for 14 days. Deletes nothing."},
    "kondo-clean": {"label": "Delete build folders", "confirm": True, "timeout": 1800, "bin": "kondo",
                    "cmd": ["kondo", "-a", "-o", "14d", CODE_DIR],
                    "desc": "Deletes the build folders from the scan (projects untouched for 14 days). Each project "
                            "needs its install command (npm install, flutter pub get) before its next build. Cannot be undone."},
    "next-scan": {"label": "Scan .next / .turbo", "confirm": False, "timeout": 900,
                  "cmd": f"SKIP_CACHES=1 SKIP_LOGS=1 SKIP_WORKTREES=1 '{HERE}/mac-cleanup.sh'",
                  "desc": f"Lists stale .next and .turbo caches (>=100 MB, 3+ days old, unused) in {CODE}. Deletes nothing."},
    "next-clean": {"label": "Clean .next / .turbo", "confirm": True, "timeout": 1800,
                   "cmd": f"SKIP_CACHES=1 SKIP_LOGS=1 SKIP_WORKTREES=1 '{HERE}/mac-cleanup.sh' --apply",
                   "desc": f"Deletes stale unused .next and .turbo folders (3+ days old, >=100 MB) in {CODE}. Rebuilds on next dev/build."},
    "worktrees-scan": {"label": "Scan worktrees", "confirm": False, "timeout": 900, "bin": "git",
                       "cmd": f"SKIP_CACHES=1 SKIP_LOGS=1 SKIP_BUILD=1 '{HERE}/mac-cleanup.sh'",
                       "desc": f"Lists git worktrees under {CODE} (including .claude/worktrees) that are merged, clean, unused "
                               "and 3+ days old. Deletes nothing."},
    "worktrees-clean": {"label": "Remove worktrees", "confirm": True, "timeout": 1800, "bin": "git",
                        "cmd": f"SKIP_CACHES=1 SKIP_LOGS=1 SKIP_BUILD=1 '{HERE}/mac-cleanup.sh' --apply",
                        "desc": "Removes those worktrees with git worktree remove (no --force, so git refuses anything "
                                "with changes or active processes). Branches are kept."},
    "tmux-scan": {"label": "Inspect tmux sessions", "confirm": False, "bin": "tmux", "fn": tmux_scan,
                  "desc": "Lists tmux sessions, attached state, active command and working folder. Deletes nothing."},
    "tmux-clean-dead": {"label": "Close idle tmux shells", "confirm": True, "bin": "tmux", "fn": tmux_clean_dead,
                        "desc": "Kills only detached tmux sessions sitting at a bare shell prompt with no running child processes."},
    "orphans-clean": {"label": "Stop orphan watchers", "confirm": True, "fn": orphans_clean,
                      "desc": "Stops PPID=1 tail -f / polling watchers left running for 30+ minutes after their parent closed."},
    "mediaanalysis-restart": {"label": "Restart mediaanalysisd", "confirm": True, "fn": mediaanalysis_restart,
                              "desc": "Restarts macOS mediaanalysisd via launchctl kickstart -k to clear a wedged CPU loop."},
    "derived-data": {"label": "Clear DerivedData", "confirm": True,
                     "cmd": "rm -rf ~/Library/Developer/Xcode/DerivedData/*",
                     "file": f"{HOME}/Library/Developer/Xcode/DerivedData",
                     "desc": "Deletes Xcode DerivedData (build products). Xcode rebuilds them; the next build is slower."},
    "sim-prune": {"label": "Remove old simulators", "confirm": True, "file": f"{HOME}/Library/Developer/CoreSimulator",
                  "cmd": ["xcrun", "simctl", "delete", "unavailable"],
                  "desc": "xcrun simctl delete unavailable: removes simulators for iOS versions no longer installed."},
    "stop-server": {"label": "Stop server", "confirm": True, "fn": stop_tree,
                    "desc": "Stops this dev server and the processes it started. If a Claude session is using it "
                            "(for example mid-test), that session loses it."},
    "brew-stop": {"label": "Stop service", "confirm": True, "fn": brew_stop, "bin": "brew",
                  "desc": "brew services stop: stops it now and at login (and removes stale Postgres lockfiles if safe)."},
}


def available(a):
    """An action shows only when what it needs is on this Mac."""
    return ((not a.get("bin") or shutil.which(a["bin"]) is not None)
            and (not a.get("app") or has_app(a["app"]))
            and (not a.get("file") or _any_path_exists(a["file"])))

jobs, scans = [], {}
SCAN_SUMMARY = {
    "kondo-scan": r"Bytes deleted: [^/]+/ ?(\S+)",
    "worktrees-scan": r"(Worktrees: .*)",
    "next-scan": r"(Build caches: .*)",
}


def run_job(action_id, arg):
    a = ACTIONS[action_id]
    job = {"id": len(jobs) + 1, "action": action_id, "label": a["label"] + (f" {arg}" if arg else ""),
           "start": time.time(), "end": None, "status": "running", "output": ""}
    jobs.append(job)

    def work():
        try:
            if "fn" in a:
                code, out = a["fn"](arg)
            else:
                cmd = a["cmd"]
                if isinstance(cmd, str):
                    cmd = ["/bin/zsh", "-c", cmd]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.get("timeout", 120),
                                   stdin=subprocess.DEVNULL, cwd=HOME)
                code, out = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            code, out = 1, "Stopped: took too long."
        except Exception as e:  # report, never crash the server
            code, out = 1, f"Failed: {e}"
        job.update(output=out[-20000:], status="ok" if code == 0 else "failed", end=time.time())
        if action_id in SCAN_SUMMARY:
            m = re.findall(SCAN_SUMMARY[action_id], out)
            scans[action_id] = {"at": time.time(), "summary": m[-1] if m else "done"}
        _cache.pop("status", None)
        _cache.pop("docker", None)

    threading.Thread(target=work, daemon=True).start()
    return job


def action_meta():
    out = {}
    for k, a in ACTIONS.items():
        if not available(a):
            continue
        cmd = a.get("cmd")
        out[k] = {"label": a["label"], "desc": a["desc"], "confirm": a["confirm"],
                  "cmd": cmd if isinstance(cmd, str) else (" ".join(cmd) if cmd else "")}
    return out


# ---------------------------------------------------------------- web

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def ok_host(self):
        # Refuse other hostnames (blocks DNS-rebinding pages from reading or driving this server).
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        global last_seen
        if not self.ok_host():
            return self.send(403, {"error": "host"})
        last_seen = time.time()
        if self.path in ("/", "/index.html"):
            html = open(os.path.join(HERE, "index.html")).read()
            html = html.replace("__TOKEN__", TOKEN).replace("__ACTIONS__", json.dumps(action_meta()).replace("</", "<\\/"))
            return self.send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/status":
            return self.send(200, status())
        if self.path.startswith("/api/history"):
            m = re.search(r"hours=(\d+)", self.path)
            hours = min(24 * 14, max(1, int(m[1]))) if m else 8
            return self.send(200, cached(f"history{hours}", 60, lambda: history(hours)))
        if self.path == "/api/jobs":
            return self.send(200, jobs[-15:][::-1])
        if self.path == "/api/ping":
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        global last_seen
        if not self.ok_host() or self.headers.get("X-Token") != TOKEN:
            return self.send(403, {"error": "token"})
        last_seen = time.time()
        if self.path == "/api/run":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            action_id, arg = body.get("action", ""), str(body.get("arg") or "")
            if action_id not in ACTIONS:
                return self.send(400, {"error": "unknown action"})
            if not available(ACTIONS[action_id]):
                return self.send(400, {"error": "not installed on this Mac"})
            if any(j["action"] == action_id and j["status"] == "running" for j in jobs):
                return self.send(409, {"error": "already running"})
            if arg and not re.fullmatch(r"[\w@.\-]{1,64}", arg):
                return self.send(400, {"error": "bad argument"})
            return self.send(200, run_job(action_id, arg))
        if self.path == "/api/rescan":
            if not big_folders["scanning"]:
                threading.Thread(target=scan_big_folders, daemon=True).start()
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


def idle_watch():
    while True:
        time.sleep(30)
        if time.time() - last_seen > IDLE_EXIT_S and not any(j["status"] == "running" for j in jobs):
            os._exit(0)


if __name__ == "__main__":
    import sys
    if "--version" in sys.argv:
        sys.exit(print(f"macdash {VERSION}"))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=big_folders_loop, daemon=True).start()
    threading.Thread(target=idle_watch, daemon=True).start()
    print(f"Mac Health on http://127.0.0.1:{PORT}/", flush=True)
    server.serve_forever()
