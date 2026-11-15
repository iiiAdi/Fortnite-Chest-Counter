"""
Fortnite Chest Counter - small always-on-top overlay with global hotkeys.
Windows only, standard library only (tkinter + ctypes).
It never reads or touches the game; you press a hotkey yourself.

Hotkeys (edit below if they clash with your keybinds):
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

Friends leaderboard: on first run a settings window asks for the leaderboard link and your
name (you can reopen it from the right-click menu). Your season total is then sent to the
group's leaderboard page. When the group owner starts a new season, your season total resets
automatically the next time the app syncs. Settings live in config.json in the same folder.
"""

import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes

# ---------------- Settings ----------------
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
VK_SUBTRACT, VK_F9, VK_F10, VK_F12 = 0x6D, 0x78, 0x79, 0x7B  # numpad -, F9, F10, F12

HOTKEYS = {
    3: ("new_game",     0, VK_SUBTRACT, "Numpad -"),
    4: ("reset_season", MOD_CONTROL | MOD_SHIFT, VK_F9, "Ctrl+Shift+F9"),
    5: ("toggle_lock",  MOD_CONTROL | MOD_SHIFT, VK_F10, "Ctrl+Shift+F10"),
    6: ("quit",         MOD_CONTROL | MOD_SHIFT, VK_F12, "Ctrl+Shift+F12"),
}

# Mouse side buttons: 1 = "back" (XBUTTON1), 2 = "forward" (XBUTTON2).
# On most mice the upper/front button is 2. If yours is the other way round, swap the numbers.
MOUSE_ADD_BUTTON = 2
MOUSE_UNDO_BUTTON = 1
# True = the game never receives these two buttons (so they can't trigger game actions).
SWALLOW_MOUSE_BUTTONS = True

MOUSE_ACTIONS = {MOUSE_ADD_BUTTON: "add", MOUSE_UNDO_BUTTON: "undo"}

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
    cfg = {"sync_url": "", "sync_key": "", "player_name": "", "player_id": "", "setup_shown": False}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
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


def hotkey_listener(events, failed):
    """Runs in its own thread: registers global hotkeys + mouse side buttons and forwards presses."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    for hk_id, (_, mods, vk, label) in HOTKEYS.items():
        if not user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk):
            failed.append(label)

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    def mouse_proc(n_code, w_param, l_param):
        if n_code == 0 and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            action = MOUSE_ACTIONS.get((info.mouseData >> 16) & 0xFFFF)
            if action:
                if w_param == WM_XBUTTONDOWN:
                    events.put(action)
                if SWALLOW_MOUSE_BUTTONS:
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    mouse_callback = HOOKPROC(mouse_proc)  # keep a reference so it isn't garbage collected
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        failed.append("Mouse buttons")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
            events.put(HOTKEYS[msg.wParam][0])


class ChestCounter:
    def __init__(self):
        self.data = load_data()
        self.cfg = load_config()
        self.sync_in = queue.Queue()
        self.sync = Syncer(self.cfg, self.sync_in)
        self.settings_win = None
        self.game = 0
        self.session = 0
        self.last_reset_press = 0.0
        self.events = queue.Queue()
        self.failed = []

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
        self.menu.add_command(label="New game (Numpad -)", command=self.new_game)
        self.menu.add_command(label="Reset season", command=self.reset_season)
        self.menu.add_command(label="Lock (Ctrl+Shift+F10)", command=self.toggle_lock)
        self.menu.add_command(label="Leaderboard settings...", command=self.open_settings)
        self.menu.add_command(label="Open data folder",
                              command=lambda: os.startfile(DATA_DIR) if os.path.isdir(DATA_DIR) else None)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)

        for widget in [self.root] + list(self.root.winfo_children()):
            self._bind_drag(widget)

        self.root.update_idletasks()
        self.hwnd = None
        self._make_no_activate()
        self.set_locked(True)
        self.refresh()

        threading.Thread(target=hotkey_listener, args=(self.events, self.failed),
                         daemon=True).start()
        self.root.after(200, self._show_hotkey_errors)
        self.root.after(50, self.poll)
        self.root.after(HEARTBEAT_MS, self.heartbeat)
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
        self.root.after(50, self.poll)

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

    def _show_hotkey_errors(self):
        if self.failed:
            self.status.config(text="Key in use: " + ", ".join(self.failed))

    def quit(self):
        self.save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ChestCounter().run()
