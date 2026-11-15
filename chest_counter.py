"""
Fortnite Chest Counter - small always-on-top overlay with global hotkeys.
Windows only, standard library only (tkinter + ctypes).
It never reads or touches the game; you press a hotkey yourself.

Default controls (change any of them from the right-click menu: Keybinds...):
    Mouse side button (upper)   +1 chest
    Mouse side button (lower)   -1 chest (undo)
    Numpad -                    new game (resets the "Found chests" count)
    Ctrl+Shift+F9   reset season total (press it twice within 3 seconds)
    Ctrl+Shift+F10  lock / unlock the overlay
    Ctrl+Shift+F12  quit the app (progress is saved first)

The overlay starts LOCKED: mouse clicks pass straight through it to the game,
so it can't be moved by accident. Unlock it (Ctrl+Shift+F10) to drag it with the
left mouse button or right-click it for a menu (including Quit), then lock it again.
Progress is saved automatically to %APPDATA%\\ChestCounter\\data.json

Updates: the app checks GitHub for a newer release at startup and tells you when there is one.

Friends leaderboard: on first run a settings window asks for the leaderboard link and your
name (you can reopen it from the right-click menu). Your season total is then sent to the
group's leaderboard page. When the group owner starts a new season, your season total resets
automatically the next time the app syncs. Settings live in config.json in the same folder.
"""

import ctypes
import json
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import uuid
import webbrowser
from ctypes import wintypes

# ---------------- Settings ----------------
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
VK_SUBTRACT, VK_F9, VK_F10, VK_F12 = 0x6D, 0x78, 0x79, 0x7B  # numpad -, F9, F10, F12

# Default controls. Everyone can change them in the app: right-click menu > Keybinds...
# Mouse button 1 = "back" side button, 2 = "forward" side button (the upper one on most mice).
DEFAULT_BINDS = {
    "add":          {"type": "mouse", "button": 2},
    "undo":         {"type": "mouse", "button": 1},
    "new_game":     {"type": "key", "mods": 0, "vk": VK_SUBTRACT},
    "reset_season": {"type": "key", "mods": MOD_CONTROL | MOD_SHIFT, "vk": VK_F9},
    "toggle_lock":  {"type": "key", "mods": MOD_CONTROL | MOD_SHIFT, "vk": VK_F10},
    "quit":         {"type": "key", "mods": MOD_CONTROL | MOD_SHIFT, "vk": VK_F12},
}
ACTIONS = [("add", "Add a chest"), ("undo", "Remove a chest"), ("new_game", "New game"),
           ("reset_season", "Reset season total (press twice)"),
           ("toggle_lock", "Lock / unlock the overlay"), ("quit", "Quit the app")]

# True = the game never receives a mouse side button that is bound to an action.
SWALLOW_MOUSE_BUTTONS = True

# Update check: compares this version with the newest GitHub release tag (e.g. "v1.1").
# When you publish a new release, change APP_VERSION to match its tag before building the exe.
APP_VERSION = "1.2"
GITHUB_REPO = "iiiAdi/Fortnite-Chest-Counter"
UPDATE_CHECK_MS = 6 * 60 * 60 * 1000   # also re-check every 6 hours while the app stays open

HEARTBEAT_MS = 90_000          # tells the leaderboard you're online (green dot), every 90 s

START_X, START_Y = 10, 10      # top-left corner
OPACITY = 0.85
BG, FG, ACCENT = "#101418", "#FFFFFF", "#7CFC9A"
# ------------------------------------------

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ChestCounter")
DATA_FILE = os.path.join(DATA_DIR, "data.json")
WM_HOTKEY = 0x0312


def load_data():
    data = {"season": 0, "x": START_X, "y": START_Y}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return data


def save_data(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, DATA_FILE)  # atomic, so a crash can't corrupt the save
    except OSError:
        pass


CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
MAX_NAME = 24


def save_config(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except OSError:
        pass


def load_config():
    """Leaderboard settings, kept in config.json. Created on first run."""
    cfg = {"sync_url": "", "sync_key": "", "player_name": "", "player_id": "", "setup_shown": False,
           "skip_version": "", "binds": {}}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    cfg["binds"] = clean_binds(cfg.get("binds"))
    if not cfg.get("player_id"):
        cfg["player_id"] = uuid.uuid4().hex   # identifies this install on the leaderboard
        save_config(cfg)
    return cfg


def parse_link(link):
    """Turns  https://script.google.com/.../exec?key=SECRET  into (url, key). ('', '') if invalid."""
    try:
        parts = urllib.parse.urlsplit(link.strip())
    except ValueError:
        return "", ""
    key = urllib.parse.parse_qs(parts.query).get("key", [""])[0]
    if parts.scheme != "https" or parts.netloc != "script.google.com" or not key:
        return "", ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), key


MODIFIER_VKS = (16, 17, 18, 91, 92, 160, 161, 162, 163, 164, 165)


def clean_binds(raw):
    """Keeps valid saved binds and fills anything missing or broken with the defaults."""
    binds = {}
    for action, default in DEFAULT_BINDS.items():
        b = raw.get(action) if isinstance(raw, dict) else None
        good = None
        if isinstance(b, dict):
            if b.get("type") == "mouse" and b.get("button") in (1, 2):
                good = {"type": "mouse", "button": b["button"]}
            elif (b.get("type") == "key" and type(b.get("vk")) is int and 0 < b["vk"] < 256
                  and type(b.get("mods")) is int):
                good = {"type": "key", "mods": b["mods"] & (MOD_CONTROL | MOD_SHIFT), "vk": b["vk"]}
        binds[action] = good or dict(default)
    return binds


def vk_name(vk):
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"Numpad {vk - 0x60}"
    names = {0x6A: "Numpad *", 0x6B: "Numpad +", 0x6D: "Numpad -", 0x6E: "Numpad .",
             0x6F: "Numpad /", 0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x08: "Backspace",
             0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "Page Up",
             0x22: "Page Down", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
             0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
             0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0x13: "Pause", 0x91: "Scroll Lock",
             0x2C: "Print Screen", 0x14: "Caps Lock", 0x90: "Num Lock"}
    return names.get(vk, f"Key {vk}")


def bind_label(b):
    if b["type"] == "mouse":
        return "Mouse " + ("Forward" if b["button"] == 2 else "Back") + " side button"
    parts = []
    if b["mods"] & MOD_CONTROL:
        parts.append("Ctrl")
    if b["mods"] & MOD_SHIFT:
        parts.append("Shift")
    parts.append(vk_name(b["vk"]))
    return "+".join(parts)


RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def version_tuple(version):
    """'v1.2' -> (1, 2, 0), so versions compare correctly (1.10 is newer than 1.9)."""
    nums = []
    for part in str(version).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def fetch_latest_release():
    """Returns (tag, page_url) of the newest GitHub release, or None if it can't be checked."""
    try:
        req = urllib.request.Request(RELEASES_API, headers={
            "User-Agent": "ChestCounter", "Accept": "application/vnd.github+json"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        url = str(data.get("html_url") or RELEASES_PAGE)
        if not url.startswith("https://github.com/"):
            url = RELEASES_PAGE
        return str(data["tag_name"]), url
    except Exception:
        return None


class Syncer:
    """Sends your season total to the friends' leaderboard in the background.
    Never blocks the overlay; if you're offline it keeps retrying with the newest numbers."""

    def __init__(self, cfg, incoming):
        self.cfg = cfg
        self.incoming = incoming          # (season_id, reset) messages for the main thread
        self.latest = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.started = False
        self._start_if_enabled()

    def enabled(self):
        c = self.cfg
        return bool(c.get("sync_url") and c.get("sync_key") and c.get("player_name"))

    def _start_if_enabled(self):
        if self.enabled() and not self.started:
            self.started = True
            threading.Thread(target=self._run, daemon=True).start()

    def configure(self, cfg):
        self.cfg = cfg
        self._start_if_enabled()

    def make_payload(self, found, session, season, season_id):
        return {"key": self.cfg["sync_key"], "id": self.cfg["player_id"],
                "name": self.cfg["player_name"], "found": found, "session": session,
                "season": season, "seasonId": season_id}

    def push(self, found, session, season, season_id):
        if not self.enabled():
            return
        with self.lock:
            self.latest = self.make_payload(found, session, season, season_id)
        self.wake.set()

    def send(self, payload):
        """Returns ("ok" | "rejected" | "offline", message)."""
        try:
            req = urllib.request.Request(
                self.cfg["sync_url"], data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "text/plain"}, method="POST")
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        except Exception:
            return "offline", "Can't reach the server"
        if not resp.get("ok"):
            err = str(resp.get("error", ""))
            return "rejected", "Wrong group key" if err == "bad key" else (err or "Server refused")
        self.incoming.put((resp.get("seasonId"), bool(resp.get("reset"))))
        return "ok", "Connected"

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            while True:
                with self.lock:
                    payload, self.latest = self.latest, None
                if payload is None:
                    break
                state, _ = self.send(payload)
                if state == "offline":
                    with self.lock:
                        if self.latest is None:   # keep it unless a newer update arrived
                            self.latest = payload
                    time.sleep(10)


WH_MOUSE_LL = 14
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


WM_REBIND = 0x8001   # WM_APP + 1: tells the listener thread to re-read the binds


class InputListener:
    """One background thread: global hotkeys + mouse side buttons. Puts action names in `events`."""

    def __init__(self, events):
        self.events = events
        self.lock = threading.Lock()
        self.binds = {}
        self.failed = []                 # bindings Windows refused (already used by another app)
        self.capturing = False           # True while the keybind window is waiting for a new key
        self.captured_buttons = queue.Queue()
        self.mouse_actions = {}
        self.hotkey_ids = {}
        self._next_id = 1
        self.thread_id = None
        self.mouse_hook_ok = True
        self._ready = threading.Event()

    def start(self, binds):
        self.binds = dict(binds)
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(3)

    def set_binds(self, binds):
        with self.lock:
            self.binds = dict(binds)
        self._poke()

    def set_capture(self, on):
        self.capturing = on
        while True:                      # drop any stale captured button
            try:
                self.captured_buttons.get_nowait()
            except queue.Empty:
                break
        self._poke()

    def _poke(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_REBIND, 0, 0)

    def _register(self):
        """Runs in the listener thread: (re)registers every hotkey from the current binds."""
        user32 = ctypes.windll.user32
        for hk_id in list(self.hotkey_ids):
            user32.UnregisterHotKey(None, hk_id)
        self.hotkey_ids = {}
        failed, mouse_actions = [], {}
        with self.lock:
            binds = dict(self.binds)
        if not self.capturing:           # while capturing, nothing is registered so keys reach the window
            for action, b in binds.items():
                if b["type"] == "mouse":
                    mouse_actions[b["button"]] = action
                    if not self.mouse_hook_ok:
                        failed.append(bind_label(b))
                else:
                    hk_id = self._next_id
                    self._next_id += 1
                    if user32.RegisterHotKey(None, hk_id, b["mods"] | MOD_NOREPEAT, b["vk"]):
                        self.hotkey_ids[hk_id] = action
                    else:
                        failed.append(bind_label(b))
        self.mouse_actions = mouse_actions
        self.failed = failed

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        def mouse_proc(n_code, w_param, l_param):
            try:
                if n_code == 0 and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    button = (info.mouseData >> 16) & 0xFFFF
                    if self.capturing:
                        if w_param == WM_XBUTTONDOWN:
                            self.captured_buttons.put(button)
                        return 1
                    action = self.mouse_actions.get(button)
                    if action:
                        if w_param == WM_XBUTTONDOWN:
                            self.events.put(action)
                        if SWALLOW_MOUSE_BUTTONS:
                            return 1
            except Exception:
                pass                     # never let an error here disturb the system-wide mouse
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        mouse_callback = HOOKPROC(mouse_proc)  # keep a reference so it isn't garbage collected
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, kernel32.GetModuleHandleW(None), 0)
        self.mouse_hook_ok = bool(hook)

        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0x400, 0x400, 0)  # makes sure the thread has a message queue
        self.thread_id = kernel32.GetCurrentThreadId()
        self._register()
        self._ready.set()

        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                action = self.hotkey_ids.get(msg.wParam)
                if action:
                    self.events.put(action)
            elif msg.message == WM_REBIND:
                self._register()


class ChestCounter:
    def __init__(self):
        self.data = load_data()
        self.cfg = load_config()
        self.sync_in = queue.Queue()
        self.sync = Syncer(self.cfg, self.sync_in)
        self.settings_win = None
        self.update_in = queue.Queue()
        self.notified_tag = None
        self.game = 0
        self.session = 0
        self.last_reset_press = 0.0
        self.events = queue.Queue()
        self.input = InputListener(self.events)
        self.keybind_win = None

        self.root = tk.Tk()
        self.root.title("Chest Counter")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", OPACITY)
        self.root.configure(bg=BG)
        self.root.geometry(f"+{int(self.data['x'])}+{int(self.data['y'])}")

        self.labels = {}
        for key, title in (("game", "Found chests"), ("session", "Current Session chests"), ("season", "Season")):
            row = tk.Frame(self.root, bg=BG)
            row.pack(fill="x", padx=8, pady=1)
            tk.Label(row, text=f"{title}:", bg=BG, fg=FG, font=("Segoe UI", 10),
                     width=22, anchor="w").pack(side="left")
            val = tk.Label(row, text="0", bg=BG, fg=ACCENT, font=("Segoe UI", 11, "bold"),
                           width=5, anchor="e")
            val.pack(side="right")
            self.labels[key] = val
        self.status = tk.Label(self.root, text="", bg=BG, fg="#FF8080", font=("Segoe UI", 8))
        self.status.pack(fill="x", padx=8)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="New game", command=self.new_game)
        self.menu.add_command(label="Reset season", command=self.reset_season)
        self.menu.add_command(label="Lock overlay", command=self.toggle_lock)
        self.menu.add_command(label="Keybinds...", command=self._later(self.open_keybinds))
        self.menu.add_command(label="Leaderboard settings...", command=self._later(self.open_settings))
        self.menu.add_command(label=f"Check for updates (v{APP_VERSION})",
                              command=self._later(lambda: self.check_updates(manual=True)))
        self.menu.add_command(label="Open data folder", command=self._later(self.open_data_folder))
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)

        for widget in [self.root] + list(self.root.winfo_children()):
            self._bind_drag(widget)

        self.root.update_idletasks()
        self.hwnd = None
        self._make_no_activate()
        self.set_locked(True)
        self.refresh()

        self.input.start(self.cfg["binds"])
        self.root.after(300, self._show_hotkey_errors)
        self.root.after(50, self.poll)
        self.root.after(HEARTBEAT_MS, self.heartbeat)
        self.root.after(5000, self._auto_update_check)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        if not self.cfg.get("setup_shown") and not self.sync.enabled():
            self.cfg["setup_shown"] = True        # only pop up automatically once
            save_config(self.cfg)
            self.root.after(600, self.open_settings)

    # ---- window behaviour ----
    def _make_no_activate(self):
        """Keep the overlay from stealing focus from the game when clicked."""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            self.hwnd = hwnd
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def set_locked(self, locked):
        """Locked = click-through, so the mouse can never move or click the overlay."""
        self.locked = locked
        try:
            user32 = ctypes.windll.user32
            GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x00000020
            style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            if locked:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass
        self.status.config(text="" if locked else "Unlocked: drag to move")
        if locked:
            self.save()

    def toggle_lock(self):
        self.set_locked(not self.locked)

    def _bind_drag(self, widget):
        for child in widget.winfo_children():
            self._bind_drag(child)
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", lambda e: self.save())
        widget.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---- logic ----
    def poll(self):
        try:
            while True:
                action = self.events.get_nowait()
                getattr(self, action)()
        except queue.Empty:
            pass
        try:
            while True:
                season_id, reset = self.sync_in.get_nowait()
                self.apply_season(season_id, reset)
        except queue.Empty:
            pass
        try:
            while True:
                manual, result = self.update_in.get_nowait()
                self.handle_update_result(manual, result)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    # ---- update check ----
    def _auto_update_check(self):
        self.check_updates()
        self.root.after(UPDATE_CHECK_MS, self._auto_update_check)

    def check_updates(self, manual=False):
        threading.Thread(target=lambda: self.update_in.put((manual, fetch_latest_release())),
                         daemon=True).start()

    def _flash_status(self, text):
        self.status.config(text=text)
        self.root.after(3000, self._clear_status)

    def handle_update_result(self, manual, result):
        if result is None:
            if manual:
                self._flash_status("Couldn't check for updates")
            return
        tag, url = result
        if version_tuple(tag) <= version_tuple(APP_VERSION):
            if manual:
                self._flash_status("You're up to date")
            return
        if not manual and (tag == self.cfg.get("skip_version") or tag == self.notified_tag):
            return
        self.notified_tag = tag
        self.show_update(tag, url)

    def show_update(self, tag, url):
        win = tk.Toplevel(self.root)
        win.title("Chest Counter - Update available")
        win.configure(bg=BG, padx=16, pady=14)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text=f"A new version is available: {tag}", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(win, text=f"You have version {APP_VERSION}.", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 12))

        def download():
            webbrowser.open(url)
            win.destroy()

        def skip():
            self.cfg["skip_version"] = tag
            save_config(self.cfg)
            win.destroy()

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(anchor="w")
        tk.Button(buttons, text="Download", width=12, command=download).pack(side="left")
        tk.Button(buttons, text="Later", width=10, command=win.destroy).pack(side="left", padx=6)
        tk.Button(buttons, text="Skip this version", command=skip).pack(side="left")
        win.focus_force()

    def heartbeat(self):
        """Regular check-in so the leaderboard can show you as online."""
        self.sync.push(self.game, self.session, self.data["season"], self.data.get("season_id"))
        self.root.after(HEARTBEAT_MS, self.heartbeat)

    def apply_season(self, season_id, reset):
        """The server tells us which season it is. A new season wipes our season total."""
        if season_id is None:
            return
        if reset:
            self.data["season"] = 0
            self.data["season_id"] = season_id
            self.refresh()                       # saves and re-sends with the new season
            self.status.config(text="New season started!")
            self.root.after(4000, self._clear_status)
        elif self.data.get("season_id") != season_id:
            self.data["season_id"] = season_id   # first sync: just adopt the server's season
            self.save()

    def _clear_status(self):
        self.status.config(text="" if self.locked else "Unlocked: drag to move")

    def add(self):
        self.game += 1
        self.session += 1
        self.data["season"] += 1
        self.refresh()

    def undo(self):
        if self.game > 0:
            self.game -= 1
            self.session = max(0, self.session - 1)
            self.data["season"] = max(0, self.data["season"] - 1)
            self.refresh()

    def new_game(self):
        self.game = 0
        self.refresh()

    def reset_season(self):
        now = time.time()
        if now - self.last_reset_press <= 3:
            self.data["season"] = 0
            self.last_reset_press = 0
            self.status.config(text="")
            self.refresh()
        else:
            self.last_reset_press = now
            self.status.config(text="Press again to reset season")
            self.root.after(3000, lambda: self.status.config(text=""))

    def refresh(self):
        self.labels["game"].config(text=str(self.game))
        self.labels["session"].config(text=str(self.session))
        self.labels["season"].config(text=str(self.data["season"]))
        self.save()
        self.sync.push(self.game, self.session, self.data["season"], self.data.get("season_id"))

    def save(self):
        self.data["x"], self.data["y"] = self.root.winfo_x(), self.root.winfo_y()
        save_data(self.data)

    # ---- leaderboard settings window ----
    def open_settings(self):
        try:
            if self.settings_win is not None and self.settings_win.winfo_exists():
                self.settings_win.lift()
                return
        except tk.TclError:
            pass

        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.title("Chest Counter - Leaderboard")
        win.configure(bg=BG, padx=14, pady=12)
        win.attributes("-topmost", True)
        win.resizable(False, False)

        url, key = self.cfg.get("sync_url", ""), self.cfg.get("sync_key", "")
        link_var = tk.StringVar(value=f"{url}?key={key}" if url and key else "")
        name_var = tk.StringVar(value=self.cfg.get("player_name", ""))
        label_font = ("Segoe UI", 10)

        tk.Label(win, text="Leaderboard link (from whoever runs your group):", bg=BG, fg=FG,
                 font=label_font).grid(row=0, column=0, sticky="w")
        tk.Entry(win, textvariable=link_var, width=58).grid(row=1, column=0, sticky="w", pady=(2, 10))
        tk.Label(win, text="Your name on the leaderboard:", bg=BG, fg=FG,
                 font=label_font).grid(row=2, column=0, sticky="w")
        tk.Entry(win, textvariable=name_var, width=30).grid(row=3, column=0, sticky="w", pady=(2, 10))
        msg = tk.Label(win, text="Leave the link empty to turn the leaderboard off.", bg=BG,
                       fg="#9aa4ad", font=("Segoe UI", 9), wraplength=440, justify="left")
        msg.grid(row=4, column=0, sticky="w", pady=(0, 8))

        buttons = tk.Frame(win, bg=BG)
        buttons.grid(row=5, column=0, sticky="w")
        tk.Button(buttons, text="Save", width=10,
                  command=lambda: self._settings_save(win, link_var, name_var, msg)).pack(side="left")
        tk.Button(buttons, text="Close", width=10, command=win.destroy).pack(side="left", padx=8)
        win.focus_force()

    def _settings_save(self, win, link_var, name_var, msg):
        link = link_var.get().strip()
        if not link:                                  # turn the leaderboard off
            self.cfg.update(sync_url="", sync_key="")
            save_config(self.cfg)
            self.sync.configure(self.cfg)
            msg.config(text="Leaderboard is off.", fg="#9aa4ad")
            return

        url, key = parse_link(link)
        name = name_var.get().strip()[:MAX_NAME]
        if not url or not key:
            msg.config(text="That doesn't look like a leaderboard link. Paste the whole link.", fg="#FF8080")
            return
        if not name:
            msg.config(text="Enter your name first.", fg="#FF8080")
            return

        self.cfg.update(sync_url=url, sync_key=key, player_name=name)
        save_config(self.cfg)
        self.sync.configure(self.cfg)
        msg.config(text="Connecting...", fg="#9aa4ad")

        payload = self.sync.make_payload(self.game, self.session, self.data["season"],
                                         self.data.get("season_id"))
        result = queue.Queue()
        threading.Thread(target=lambda: result.put(self.sync.send(payload)), daemon=True).start()

        def check():
            try:
                if not win.winfo_exists():
                    return
                state, text = result.get_nowait()
            except queue.Empty:
                win.after(200, check)
                return
            except tk.TclError:
                return
            if state == "ok":
                msg.config(text="Connected! You're on the leaderboard.", fg=ACCENT)
            else:
                msg.config(text=text + (". Your counts will keep retrying." if state == "offline" else ""),
                           fg="#FF8080")
                if state == "offline":
                    self.sync.push(self.game, self.session, self.data["season"],
                                   self.data.get("season_id"))
        check()

    # ---- menu helpers ----
    def _later(self, fn):
        """Runs a menu action a moment after the menu has closed (opening windows or Explorer
        while the menu still holds the mouse can freeze Tk)."""
        return lambda: self.root.after(150, fn)

    def open_data_folder(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            subprocess.Popen(["explorer", DATA_DIR])   # separate process, never blocks the overlay
        except Exception:
            pass

    # ---- keybind settings window ----
    def open_keybinds(self):
        try:
            if self.keybind_win is not None and self.keybind_win.winfo_exists():
                self.keybind_win.lift()
                return
        except tk.TclError:
            pass

        win = tk.Toplevel(self.root)
        self.keybind_win = win
        win.title("Chest Counter - Keybinds")
        win.configure(bg=BG, padx=14, pady=12)
        win.attributes("-topmost", True)
        win.resizable(False, False)

        titles = dict(ACTIONS)
        rows = {}
        state = {"action": None}
        msg = tk.Label(win, text="Click Change, then press the key (or mouse side button) you want.",
                       bg=BG, fg="#9aa4ad", font=("Segoe UI", 9), wraplength=470, justify="left")

        def refresh_rows():
            for action, (value, button) in rows.items():
                if state["action"] == action:
                    value.config(text="Press a key or side button...  (Esc = cancel)", fg="#FFD166")
                else:
                    value.config(text=bind_label(self.cfg["binds"][action]), fg=ACCENT)

        def end_capture():
            if state["action"] is not None:
                state["action"] = None
                self.input.set_capture(False)
            refresh_rows()

        def begin(action):
            if state["action"] is None:
                self.input.set_capture(True)
            state["action"] = action
            msg.config(text="Waiting for a key or mouse side button...", fg="#9aa4ad")
            refresh_rows()
            win.focus_force()
            poll_mouse()

        def apply_bind(bind):
            action = state["action"]
            for other, existing in self.cfg["binds"].items():
                if other != action and existing == bind:
                    msg.config(text=f'{bind_label(bind)} is already used for "{titles[other]}". Pick another.',
                               fg="#FF8080")
                    return
            self.cfg["binds"][action] = bind
            save_config(self.cfg)
            self.input.set_binds(self.cfg["binds"])
            end_capture()
            msg.config(text="Saved.", fg=ACCENT)
            win.after(400, check_failed)

        def check_failed():
            try:
                if win.winfo_exists() and self.input.failed:
                    msg.config(text="Another program already uses: " + ", ".join(self.input.failed)
                                    + ". Choose a different key.", fg="#FF8080")
            except tk.TclError:
                pass

        def on_key(event):
            if state["action"] is None:
                return None
            if event.keycode == 27:                       # Esc cancels
                end_capture()
                msg.config(text="Cancelled.", fg="#9aa4ad")
                return "break"
            if event.keycode in MODIFIER_VKS:             # wait for the real key
                return "break"
            mods = (MOD_CONTROL if event.state & 0x4 else 0) | (MOD_SHIFT if event.state & 0x1 else 0)
            apply_bind({"type": "key", "mods": mods, "vk": event.keycode})
            return "break"

        def poll_mouse():
            try:
                if not win.winfo_exists() or state["action"] is None:
                    return
                while True:
                    try:
                        button = self.input.captured_buttons.get_nowait()
                    except queue.Empty:
                        break
                    if button in (1, 2):
                        apply_bind({"type": "mouse", "button": button})
                        if state["action"] is None:
                            return
                win.after(50, poll_mouse)
            except tk.TclError:
                pass

        def reset_defaults():
            end_capture()
            self.cfg["binds"] = {a: dict(b) for a, b in DEFAULT_BINDS.items()}
            save_config(self.cfg)
            self.input.set_binds(self.cfg["binds"])
            refresh_rows()
            msg.config(text="Back to the default keys.", fg=ACCENT)

        def close():
            end_capture()
            win.destroy()

        for i, (action, title) in enumerate(ACTIONS):
            tk.Label(win, text=title, bg=BG, fg=FG, font=("Segoe UI", 10),
                     anchor="w").grid(row=i, column=0, sticky="w", pady=3)
            value = tk.Label(win, text="", bg="#1a2027", fg=ACCENT, font=("Segoe UI", 10, "bold"),
                             width=36, anchor="w", padx=8, pady=3)
            value.grid(row=i, column=1, padx=10, pady=3)
            button = tk.Button(win, text="Change", width=9, takefocus=0,
                               command=lambda a=action: begin(a))
            button.grid(row=i, column=2, pady=3)
            rows[action] = (value, button)
        refresh_rows()

        msg.grid(row=len(ACTIONS), column=0, columnspan=3, sticky="w", pady=(10, 2))
        tk.Label(win, text="Keys you bind are taken over while this app runs, so they won't reach the "
                           "game or other programs. Avoid keys you use in Fortnite.",
                 bg=BG, fg="#7b858e", font=("Segoe UI", 8), wraplength=470, justify="left"
                 ).grid(row=len(ACTIONS) + 1, column=0, columnspan=3, sticky="w")

        buttons = tk.Frame(win, bg=BG)
        buttons.grid(row=len(ACTIONS) + 2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        tk.Button(buttons, text="Reset to defaults", takefocus=0, command=reset_defaults).pack(side="left")
        tk.Button(buttons, text="Close", width=10, takefocus=0, command=close).pack(side="left", padx=8)

        win.bind("<KeyPress>", on_key)
        win.protocol("WM_DELETE_WINDOW", close)
        win.focus_force()

    def _show_hotkey_errors(self):
        if self.input.failed:
            self.status.config(text="Key in use: " + ", ".join(self.input.failed))

    def quit(self):
        self.save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ChestCounter().run()
