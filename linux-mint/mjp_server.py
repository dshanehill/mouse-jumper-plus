#!/usr/bin/env python3
"""Mouse Jumper+ server for Linux Mint (phase 5: edge switching, clipboard, Reader+ for Mint).

Mint owns the keyboard and mouse. On "mac" it grabs every keyboard/mouse at kernel
level (EVIOCGRAB) and streams raw input to mjp-hid on the Mac; X/Cinnamon see nothing,
so Mint's pointer and magnifier stay still. On "mint" (or any failure) it releases.

Safety exits (any one returns control to Mint immediately):
  * Control + Alt + Escape pressed twice within 1 s (never forwarded)
  * no heartbeat from the Mac for 1.5 s, or the connection drops
  * phase-2 test limit: automatic return after TEST_LIMIT seconds
  * process exit/crash: the kernel drops the grab automatically

Commands (localhost UDP 24859):  mac | mint | status
Standard library only. Key contents are never logged.
"""

import fcntl
import os
import re
import select
import signal
import socket
import struct
import sys
import base64
import threading
import subprocess
import time

CONFIG_DIR = os.path.expanduser("~/.config/mouse-jumper")


def _read_hosts():
    """~/.config/mouse-jumper/mac-host: the Mac's address on the first line; any other
    addresses the Mac may connect from (e.g. its LAN address) on the lines after it."""
    try:
        with open(os.path.join(CONFIG_DIR, "mac-host")) as f:
            hosts = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        hosts = []
    return hosts or ["127.0.0.1"]


MAC_HOSTS = _read_hosts()
MAC_HOST = MAC_HOSTS[0]
MAC_PORT = 24851
CMD_PORT = 24859
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
STATE_DIR = os.path.expanduser("~/.local/state/mouse-jumper")
LOG = os.path.join(STATE_DIR, "server.log")
HEARTBEAT_TIMEOUT = 1.5
TEST_LIMIT = float(os.environ.get("MJP_TEST_LIMIT", "0"))     # 0 = no limit
CTRL_PORT = 24850
ALLOWED_MACS = set(MAC_HOSTS)
PUSH_COUNTS = 60          # raw mouse counts of deliberate push past the edge
PUSH_WINDOW = 0.4         # ...within this many seconds
SWITCH_COOLDOWN = 0.8     # no new switch this soon after the last one
ARRIVE_INSET = 40         # arrive this many pixels in from the edge
CLIP_HELPER = os.path.expanduser("~/.local/bin/mjp_clip.py")
CLIP_MAX = 512 * 1024
READER_PORT = 24860                       # mjp_reader.py listens here (localhost)
SPEAK_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "mjp-speak.sock")
KEY_LEFTMETA, KEY_RIGHTMETA = 125, 126
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
OVERLAY_CANCEL_KEYCODE = 207              # X keycode with no symbol: stops Cinnamon's menu opening after Cmd-click

EVENT_FMT = "llHHi"                    # struct input_event on 64-bit Linux
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EVIOCGRAB = 0x40044590
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTALT, KEY_RIGHTALT, KEY_ESC = 29, 97, 56, 100, 1
BUTTONS = {0x110: 1, 0x111: 2, 0x112: 3, 0x113: 4, 0x114: 5}   # left right middle side extra

# Linux key code -> USB HID usage, keyboard page 0x07
KEYBOARD = {
    1: 0x29, 2: 0x1E, 3: 0x1F, 4: 0x20, 5: 0x21, 6: 0x22, 7: 0x23, 8: 0x24, 9: 0x25, 10: 0x26,
    11: 0x27, 12: 0x2D, 13: 0x2E, 14: 0x2A, 15: 0x2B, 16: 0x14, 17: 0x1A, 18: 0x08, 19: 0x15,
    20: 0x17, 21: 0x1C, 22: 0x18, 23: 0x0C, 24: 0x12, 25: 0x13, 26: 0x2F, 27: 0x30, 28: 0x28,
    29: 0xE0, 30: 0x04, 31: 0x16, 32: 0x07, 33: 0x09, 34: 0x0A, 35: 0x0B, 36: 0x0D, 37: 0x0E,
    38: 0x0F, 39: 0x33, 40: 0x34, 41: 0x35, 42: 0xE1, 43: 0x31, 44: 0x1D, 45: 0x1B, 46: 0x06,
    47: 0x19, 48: 0x05, 49: 0x11, 50: 0x10, 51: 0x36, 52: 0x37, 53: 0x38, 54: 0xE5, 55: 0x55,
    56: 0xE2, 57: 0x2C, 58: 0x39, 59: 0x3A, 60: 0x3B, 61: 0x3C, 62: 0x3D, 63: 0x3E, 64: 0x3F,
    65: 0x40, 66: 0x41, 67: 0x42, 68: 0x43, 69: 0x53, 70: 0x47, 71: 0x5F, 72: 0x60, 73: 0x61,
    74: 0x56, 75: 0x5C, 76: 0x5D, 77: 0x5E, 78: 0x57, 79: 0x59, 80: 0x5A, 81: 0x5B, 82: 0x62,
    83: 0x63, 85: 0x94, 86: 0x64, 87: 0x44, 88: 0x45, 89: 0x87, 90: 0x92, 91: 0x93, 92: 0x8A,
    93: 0x88, 94: 0x8B, 95: 0x8C, 96: 0x58, 97: 0xE4, 98: 0x54, 99: 0x46, 100: 0xE6, 102: 0x4A,
    103: 0x52, 104: 0x4B, 105: 0x50, 106: 0x4F, 107: 0x4D, 108: 0x51, 109: 0x4E, 110: 0x49,
    111: 0x4C, 117: 0x67, 119: 0x48, 121: 0x85, 122: 0x90, 123: 0x91, 124: 0x89, 125: 0xE3,
    126: 0xE7, 127: 0x65, 183: 0x68, 184: 0x69, 185: 0x6A, 186: 0x6B, 187: 0x6C, 188: 0x6D,
    189: 0x6E, 190: 0x6F, 191: 0x70, 192: 0x71, 193: 0x72, 194: 0x73,
}
# Linux key code -> USB HID usage, consumer page 0x0C (media keys)
CONSUMER = {
    113: 0xE2, 114: 0xEA, 115: 0xE9, 163: 0xB5, 164: 0xCD, 165: 0xB6, 166: 0xB7,
    224: 0x70, 225: 0x6F, 161: 0xB8, 200: 0xB0, 201: 0xB1, 208: 0xB3, 168: 0xB4,
}


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def frame(kind, page=0, code=0, v1=0, v2=0):
    clamp = lambda v: max(-32768, min(32767, v))
    return struct.pack("<BBHhh", ord(kind), page, code, clamp(v1), clamp(v2))


def translate_key(code, value):
    """evdev key event -> list of frames (empty for unknown keys). value: 1 down, 0 up, 2 repeat."""
    if value == 2:
        return []                           # macOS makes its own key repeat
    if code in BUTTONS:
        return [frame("B", 0, BUTTONS[code], 1 if value else 0)]
    if code in KEYBOARD:
        return [frame("K", 0x07, KEYBOARD[code], 1 if value else 0)]
    if code in CONSUMER:
        return [frame("K", 0x0C, CONSUMER[code], 1 if value else 0)]
    return []


# ---------------------------------------------------------------- devices
def input_devices():
    """[(path, name, kind)] for real keyboards/mice and their sibling nodes."""
    text = open("/proc/bus/input/devices").read()
    devs = []
    for block in text.strip().split("\n\n"):
        name = re.search(r'N: Name="([^"]*)"', block)
        handlers = re.search(r"H: Handlers=([^\n]*)", block)
        phys = re.search(r"P: Phys=([^\n]*)", block)
        ev = re.search(r"B: EV=([0-9a-f]+)", block)
        key = re.search(r"B: KEY=([0-9a-f ]+)", block)
        rel = re.search(r"B: REL=([0-9a-f]+)", block)
        if not (name and handlers and ev):
            continue
        event = re.search(r"event(\d+)", handlers.group(1))
        if not event:
            continue
        ev_bits = int(ev.group(1), 16)
        key_words = key.group(1).split() if key else []
        key_bits = int("".join(w.rjust(16, "0") for w in key_words), 16) if key_words else 0
        rel_bits = int(rel.group(1), 16) if rel else 0
        is_kbd = bool(ev_bits & (1 << EV_KEY)) and bool(key_bits & (1 << 30))     # has KEY_A
        is_mouse = bool(ev_bits & (1 << EV_REL)) and bool(rel_bits & (1 << REL_X))
        devs.append({"path": f"/dev/input/event{event.group(1)}", "name": name.group(1),
                     "phys": (phys.group(1) if phys else "").split("/input")[0],
                     "kbd": is_kbd, "mouse": is_mouse,
                     "has_keys": bool(ev_bits & (1 << EV_KEY))})
    skip = re.compile(r"virtual|xwaykeyz|uinput|power button|sleep button|video bus|wmi|lid switch", re.I)
    main_phys = {d["phys"] for d in devs if (d["kbd"] or d["mouse"]) and d["phys"] and not skip.search(d["name"])}
    out = []
    for d in devs:
        if skip.search(d["name"]):
            continue
        if d["kbd"] or d["mouse"] or (d["has_keys"] and d["phys"] in main_phys):
            out.append((d["path"], d["name"], "kbd" if d["kbd"] else "mouse" if d["mouse"] else "aux"))
    return out


# ---------------------------------------------------------------- X11 (pointer read + one warp on return)
import ctypes
import ctypes.util


class X11:
    def __init__(self):
        lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
        lib.XWarpPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong] + [ctypes.c_int] * 2 + [ctypes.c_uint] * 2 + [ctypes.c_int] * 2
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XDisplayWidth.argtypes = lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib = lib
        self.dpy = lib.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open X display (DISPLAY not set?)")
        self.root = lib.XDefaultRootWindow(self.dpy)

    def size(self):
        return self.lib.XDisplayWidth(self.dpy, 0), self.lib.XDisplayHeight(self.dpy, 0)

    def pointer(self):
        r, c = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, m = (ctypes.c_int() for _ in range(5))
        self.lib.XQueryPointer(self.dpy, self.root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx),
                               ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(m))
        return rx.value, ry.value

    def _xtest(self):
        if not hasattr(self, "xt"):
            xt = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
            xt.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            xt.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.xt = xt
        return self.xt

    def move_by(self, dx, dy):
        self._xtest().XTestFakeRelativeMotionEvent(self.dpy, int(dx), int(dy), 0)
        self.lib.XFlush(self.dpy)

    def button(self, b, down):
        self._xtest().XTestFakeButtonEvent(self.dpy, b, 1 if down else 0, 0)
        self.lib.XFlush(self.dpy)

    def tap_keycode(self, kc):
        xt = self._xtest()
        xt.XTestFakeKeyEvent(self.dpy, kc, 1, 0)
        xt.XTestFakeKeyEvent(self.dpy, kc, 0, 0)
        self.lib.XFlush(self.dpy)

    def warp(self, x, y):
        self.lib.XWarpPointer(self.dpy, 0, self.root, 0, 0, 0, 0, int(x), int(y))
        self.lib.XFlush(self.dpy)


# ---------------------------------------------------------------- devices (always open; grabbed only while on the Mac)
class Devices:
    def __init__(self):
        self.fds = {}            # path -> fd
        self.kinds = {}          # path -> "kbd" | "mouse" | "aux"
        self.grabbed = False
        self.mice_grabbed = False

    def rescan(self):
        present = {p for p, _n, _k in input_devices()}
        for path in list(self.fds):
            if path not in present:
                self.drop(path)
        for path, name, kind in input_devices():
            self.kinds[path] = kind
            if path in self.fds:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                if self.grabbed or (self.mice_grabbed and kind == "mouse"):
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                self.fds[path] = fd
            except OSError as e:
                log(f"could not open {name}: {e}")

    def drop(self, path):
        fd = self.fds.pop(path, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def set_grab(self, on):
        ok = True
        for path, fd in list(self.fds.items()):
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
            except OSError as e:
                log(f"{'grab' if on else 'ungrab'} failed on {path}: {e}")
                ok = False
        self.grabbed = on
        self.mice_grabbed = False
        return ok and bool(self.fds)

    def grab_mice(self, on):
        """While Cmd is held on Mint: hold back mouse clicks (we relay motion ourselves)."""
        if self.grabbed or on == self.mice_grabbed:
            return
        for path, fd in self.fds.items():
            if self.kinds.get(path) == "mouse":
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
                except OSError:
                    pass
        self.mice_grabbed = on


# ---------------------------------------------------------------- server
class Server:
    def __init__(self):
        self.dev = Devices()
        self.x = X11()
        self.sock = None                 # input stream to mjp-hid (only while on the Mac)
        self.ctrl = None                 # control channel from the Mac app
        self.ctrl_buf = b""
        self.on_mac = False
        self.paused = False
        self.mac_pinned = False
        self.mac_y = 0.5
        self.last_hb = self.last_ctrl = self.mac_since = 0.0
        self.held = set()                # keys/buttons currently down (from raw events)
        self.mods = set()
        self.esc_presses = []
        self.esc_swallowed = False
        self.motion = [0, 0, 0, 0]
        self.push = 0
        self.push_start = 0.0
        self.last_ping = self.last_scan = 0.0
        self.last_switch = 0.0
        self.cmd_down = False             # Cmd (Super) held on Mint -> reader gestures
        self.cmd_used = False
        self.ctrl_tap = None              # time Control went down with nothing else pressed
        self.reader = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.push_counts = PUSH_COUNTS
        self.arrive_inset = ARRIVE_INSET
        self.ctrl_lock = threading.Lock()
        self.mac_clip = None             # latest text copied on the Mac (bytes)
        self.mac_clip_applied = None
        self.mint_clip_sent = None

    # ---- clipboard (text) ---------------------------------------------------
    def mint_clip_get(self):
        try:
            r = subprocess.run(["python3", CLIP_HELPER, "get"], capture_output=True, timeout=1.5)
            return r.stdout[:CLIP_MAX] if r.returncode == 0 else b""
        except (subprocess.TimeoutExpired, OSError):
            return b""

    def mint_clip_set(self, data):
        try:
            subprocess.run(["python3", CLIP_HELPER, "set"], input=data[:CLIP_MAX], timeout=3)
        except (subprocess.TimeoutExpired, OSError) as e:
            log(f"clipboard set failed: {e}")

    def push_mint_clip(self):
        """Background: send Mint's clipboard text to the Mac if it is new."""
        clip = self.mint_clip_get()
        if clip and clip != self.mint_clip_sent and clip != self.mac_clip_applied:
            self.mint_clip_sent = clip
            self.ctrl_send("CLIP " + base64.b64encode(clip).decode())

    # ---- control channel -------------------------------------------------
    def ctrl_send(self, line):
        conn = self.ctrl
        if conn:
            try:
                with self.ctrl_lock:
                    conn.sendall((line + "\n").encode())
            except OSError:
                if threading.current_thread() is threading.main_thread():
                    self.ctrl_close("send failed")

    def ctrl_close(self, why):
        if self.ctrl:
            try:
                self.ctrl.close()
            except OSError:
                pass
            self.ctrl = None
            self.ctrl_buf = b""
            self.mac_pinned = False
            log(f"mac app disconnected ({why})")
            if self.on_mac:
                self.go_mint("mac app gone")

    def ctrl_accept(self, srv):
        conn, (addr, _port) = srv.accept()
        if addr not in ALLOWED_MACS:
            conn.close()
            log(f"refused control connection from {addr}")
            return
        conn.settimeout(2)
        try:
            line = conn.recv(200).decode(errors="ignore").strip()
            token = open(TOKEN_FILE).read().strip()
            if line != f"MJPC {token}":
                conn.close()
                log("control handshake rejected")
                return
            conn.sendall(b"ok\n")
        except OSError:
            conn.close()
            return
        conn.settimeout(0.5)
        self.ctrl_close("replaced")
        self.ctrl, self.last_ctrl = conn, time.monotonic()
        log("mac app connected")
        self.ctrl_send(self.owner_line())

    def owner_line(self):
        return f"OWNER mac {self.mac_y:.4f}" if self.on_mac else "OWNER mint"

    def ctrl_read(self):
        try:
            data = self.ctrl.recv(1024)
        except (BlockingIOError, socket.timeout):
            return
        except OSError:
            data = b""
        if not data:
            self.ctrl_close("closed")
            return
        self.last_ctrl = time.monotonic()
        self.ctrl_buf += data
        if len(self.ctrl_buf) > 2 * CLIP_MAX:
            self.ctrl_close("oversized message")
            return
        while b"\n" in self.ctrl_buf:
            raw, self.ctrl_buf = self.ctrl_buf.split(b"\n", 1)
            parts = raw.decode(errors="ignore").split()
            if not parts:
                continue
            if parts[0] == "EDGE" and len(parts) >= 2:
                if (parts[1] == "1") != self.mac_pinned:
                    log(f"mac pointer {'pinned at' if parts[1] == '1' else 'left'} the right edge; held={sorted(self.held)}")
                self.mac_pinned = parts[1] == "1"
                if len(parts) >= 3:
                    try:
                        self.mac_y = min(1.0, max(0.0, float(parts[2])))
                    except ValueError:
                        pass
                if not self.mac_pinned:
                    self.push = 0
            elif parts[0] == "CLIP" and len(parts) >= 2:
                try:
                    self.mac_clip = base64.b64decode(parts[1], validate=True)[:CLIP_MAX]
                except ValueError:
                    pass
            elif parts[0] == "PROG" and len(parts) >= 2:
                self.to_reader("prog " + parts[1])      # Reader+ position -> highlight on Mint
            elif parts[0] == "GOTO" and len(parts) >= 2:
                if parts[1] == "mac" and not self.on_mac:
                    log("switch button: " + self.go_mac(-1.0, "button on Mac"))
                elif parts[1] == "mint" and self.on_mac:
                    self.go_mint("button on Mac")
            elif parts[0] == "SET" and len(parts) >= 3:
                try:
                    v = int(parts[2])
                except ValueError:
                    v = None
                if v is not None and parts[1] == "push":
                    self.push_counts = min(300, max(20, v))
                elif v is not None and parts[1] == "inset":
                    self.arrive_inset = min(200, max(10, v))
            elif parts[0] == "PAUSE" and len(parts) >= 2:
                self.paused = parts[1] == "1"
                log(f"switching {'paused' if self.paused else 'resumed'}")
            # "HB" just refreshes last_ctrl

    # ---- ownership ---------------------------------------------------------
    def go_mac(self, y_frac=0.5, reason="command"):
        if self.on_mac:
            return "already on mac"
        if self.held:
            return "refused: a key or button is held"
        try:
            token = open(TOKEN_FILE).read().strip()
            s = socket.create_connection((MAC_HOST, MAC_PORT), timeout=2)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.sendall(f"MJP2 {token}\n".encode())
            s.settimeout(2)
            if s.recv(3) != b"ok\n":
                s.close()
                return "refused by mac (token?)"
            s.settimeout(0.5)
        except OSError as e:
            return f"mac unreachable: {e}"
        if not self.dev.set_grab(True):
            self.dev.set_grab(False)
            s.close()
            return "could not grab devices"
        threading.Thread(target=self.push_mint_clip, daemon=True).start()
        self.sock, self.on_mac = s, True
        self.mac_y = y_frac
        self.last_hb = self.mac_since = time.monotonic()
        self.mods.clear(); self.esc_presses.clear(); self.esc_swallowed = False
        self.motion = [0, 0, 0, 0]; self.push = 0; self.mac_pinned = False
        self.ctrl_send(self.owner_line())
        self.last_switch = time.monotonic()
        log(f"-> MAC ({reason}, y={y_frac:.2f})")
        return "on mac"

    def go_mint(self, reason, y_frac=None):
        if not self.on_mac:
            return "already on mint"
        if self.sock:
            try:
                self.sock.sendall(frame("R"))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.dev.set_grab(False)
        self.on_mac = False
        self.push = 0
        self.mac_pinned = False
        self.last_switch = time.monotonic()
        if y_frac is not None:
            w, h = self.x.size()
            self.x.warp(self.arrive_inset, round(y_frac * (h - 1)))   # the only warp: arrive at matching height
        self.ctrl_send(self.owner_line())
        if self.mac_clip and self.mac_clip != self.mac_clip_applied and self.mac_clip != self.mint_clip_sent:
            self.mac_clip_applied = self.mac_clip
            threading.Thread(target=self.mint_clip_set, args=(self.mac_clip,), daemon=True).start()
        log(f"-> MINT ({reason})")
        return "on mint"

    def send(self, data):
        try:
            self.sock.sendall(data)
        except OSError:
            self.go_mint("send failed")

    # ---- events ------------------------------------------------------------
    def edge_push(self, dx, now):
        """Count deliberate push past the shared edge; True when it is enough."""
        if now - self.last_switch < SWITCH_COOLDOWN:
            self.push = 0
            return False
        if now - self.push_start > PUSH_WINDOW:
            self.push, self.push_start = 0, now
        self.push += abs(dx)
        return self.push >= self.push_counts

    def handle_event(self, etype, code, value, now):
        if etype == EV_KEY:
            if value == 1:
                self.held.add(code)
            elif value == 0:
                self.held.discard(code)
        if not self.on_mac and self.mint_gestures(etype, code, value):
            return
        if not self.on_mac:
            # Mint owns: X handles everything; we only watch for a push past the LEFT edge
            if etype == EV_REL and code == REL_X and value < 0 and self.ctrl and not self.paused and not self.held:
                px, py = self.x.pointer()
                if px <= 0:
                    if self.edge_push(value, now):
                        w, h = self.x.size()
                        self.go_mac(py / max(1, h - 1), "left edge")
                else:
                    self.push = 0
            return
        # Mac owns: forward everything
        if etype == EV_KEY:
            if code in (KEY_LEFTCTRL, KEY_RIGHTCTRL, KEY_LEFTALT, KEY_RIGHTALT):
                (self.mods.add if value else self.mods.discard)(code)
            if code == KEY_ESC and value == 1 and self.mods & {KEY_LEFTCTRL, KEY_RIGHTCTRL} \
                    and self.mods & {KEY_LEFTALT, KEY_RIGHTALT}:
                self.esc_presses = [t for t in self.esc_presses if now - t < 1.0] + [now]
                self.esc_swallowed = True
                if len(self.esc_presses) >= 2:
                    self.go_mint("emergency key", 0.5)
                return                              # never forward Ctrl+Alt+Esc
            if code == KEY_ESC and value != 1 and self.esc_swallowed:
                if value == 0:
                    self.esc_swallowed = False
                return
            for f in translate_key(code, value):
                self.send(f)
        elif etype == EV_REL:
            if code == REL_X: self.motion[0] += value
            elif code == REL_Y: self.motion[1] += value
            elif code == REL_WHEEL: self.motion[2] += value
            elif code == REL_HWHEEL: self.motion[3] += value
        elif etype == EV_SYN and code == 0:
            dx, dy, w, h = self.motion
            self.motion = [0, 0, 0, 0]
            if dx or dy:
                self.send(frame("M", 0, 0, dx, dy))
            if w or h:
                self.send(frame("W", 0, 0, w, h))
            # push past the Mac's RIGHT edge -> back to Mint
            if self.on_mac and dx > 0 and self.mac_pinned and not self.paused and not self.held:
                if self.edge_push(dx, now):
                    self.go_mint("right edge", self.mac_y)

    def to_reader(self, msg):
        try:
            self.reader.sendto(msg.encode(), ("127.0.0.1", READER_PORT))
        except OSError:
            pass

    def mint_gestures(self, etype, code, value):
        """Reader+ gestures while Mint owns the keyboard. True = event consumed here."""
        # Control tapped by itself -> stop speech
        if etype == EV_KEY and code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
            if value == 1 and len(self.held) == 1:
                self.ctrl_tap = time.monotonic()
            elif value == 0 and self.ctrl_tap and time.monotonic() - self.ctrl_tap < 0.5:
                self.ctrl_tap = None
                self.to_reader("stop")
        elif etype == EV_KEY and value == 1:
            self.ctrl_tap = None
        # Cmd held: mice are grabbed; motion relayed, clicks become reading gestures
        if etype == EV_KEY and code in (KEY_LEFTMETA, KEY_RIGHTMETA):
            if value == 1 and not self.cmd_down:
                self.cmd_down, self.cmd_used = True, False
                if not (self.held & set(BUTTONS)):      # never grab mid-click (would leave a button stuck)
                    self.dev.grab_mice(True)
            elif value == 0:
                self.cmd_down = False
                self.dev.grab_mice(False)
            return False                       # the key itself still goes to X (Super+scroll zoom keeps working)
        if not self.cmd_down:
            return False
        if etype == EV_KEY and code in (BTN_LEFT, BTN_RIGHT):
            if value == 1:
                if not self.cmd_used:
                    self.cmd_used = True
                    self.x.tap_keycode(OVERLAY_CANCEL_KEYCODE)   # so releasing Cmd doesn't open the menu
                px, py = self.x.pointer()
                self.to_reader(f"{'click' if code == BTN_LEFT else 'rclick'} {px} {py}")
                log(f"reader gesture: {'cmd-click' if code == BTN_LEFT else 'cmd-right-click'} at {px},{py}")
            return True
        if etype == EV_KEY and code in BUTTONS:
            return True                        # other buttons: swallowed while Cmd is held
        if etype == EV_REL:
            if code == REL_X: self.motion[0] += value
            elif code == REL_Y: self.motion[1] += value
            elif code == REL_WHEEL: self.motion[2] += value
            return True
        if etype == EV_SYN and code == 0 and self.dev.mice_grabbed:
            dx, dy, w, _h = self.motion
            self.motion = [0, 0, 0, 0]
            if dx or dy:
                self.x.move_by(dx, dy)
            for _ in range(abs(w)):            # Cmd + scroll still reaches Mint (magnifier zoom)
                self.x.button(4 if w > 0 else 5, True); self.x.button(4 if w > 0 else 5, False)
            return False
        return False

    def speak_accept(self, srv):
        """Speech/OCR lines from mjp_reader.py -> forwarded to the Mac app."""
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        conn.settimeout(1.0)
        data = b""
        try:
            while len(data) < 4 * CLIP_MAX:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        except OSError:
            pass
        conn.close()
        for line in data.decode(errors="ignore").splitlines():
            if line.startswith(("SPEAK ", "OCR ")):
                if self.ctrl:
                    self.ctrl_send(line)

    def read_device(self, fd, now):
        try:
            buf = os.read(fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return
        except OSError:
            for path, f in list(self.dev.fds.items()):
                if f == fd:
                    log(f"device gone: {path}")
                    self.dev.drop(path)
            return
        for i in range(0, len(buf) - EVENT_SIZE + 1, EVENT_SIZE):
            _s, _us, etype, code, value = struct.unpack_from(EVENT_FMT, buf, i)
            self.handle_event(etype, code, value, now)

    # ---- main loop -----------------------------------------------------------
    def run(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cmd.bind(("127.0.0.1", CMD_PORT))
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", CTRL_PORT))
        srv.listen(2)
        try:
            os.unlink(SPEAK_SOCK)
        except OSError:
            pass
        speak = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        speak.bind(SPEAK_SOCK)
        os.chmod(SPEAK_SOCK, 0o600)
        speak.listen(4)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        self.dev.rescan()
        log(f"server started (phase 3), devices={len(self.dev.fds)}, screen={self.x.size()}")
        try:
            while True:
                rlist = [cmd, srv, speak] + list(self.dev.fds.values())
                if self.sock: rlist.append(self.sock)
                if self.ctrl: rlist.append(self.ctrl)
                ready, _, _ = select.select(rlist, [], [], 0.05)
                now = time.monotonic()
                for r in ready:
                    if r is cmd:
                        data, addr = cmd.recvfrom(64)
                        word = data.decode(errors="ignore").strip()
                        reply = (self.go_mac() if word == "mac" else
                                 self.go_mint("command", 0.5) if word == "mint" else
                                 f"{'on mac' if self.on_mac else 'on mint'}; mac app "
                                 f"{'connected' if self.ctrl else 'not connected'}; devices {len(self.dev.fds)}; "
                                 f"pinned={self.mac_pinned} held={sorted(self.held)} push={self.push} paused={self.paused}")
                        cmd.sendto(reply.encode(), addr)
                    elif r is srv:
                        self.ctrl_accept(srv)
                    elif r is speak:
                        self.speak_accept(speak)
                    elif r is self.ctrl:
                        self.ctrl_read()
                    elif r is self.sock:
                        try:
                            data = self.sock.recv(64)
                        except (BlockingIOError, socket.timeout):
                            data = b"h"
                        except OSError:
                            data = b""
                        if not data:
                            self.go_mint("mac receiver closed", 0.5)
                        else:
                            self.last_hb = now
                    else:
                        self.read_device(r, now)
                if self.on_mac:
                    if now - self.last_ping > 0.25:
                        self.last_ping = now
                        self.send(frame("P"))
                    if self.on_mac and now - self.last_hb > HEARTBEAT_TIMEOUT:
                        self.go_mint("receiver heartbeat lost", 0.5)
                    if self.on_mac and TEST_LIMIT and now - self.mac_since > TEST_LIMIT:
                        self.go_mint("test time limit", 0.5)
                if self.ctrl and now - self.last_ctrl > 2.0:
                    self.ctrl_close("app heartbeat lost")
                if now - self.last_scan > 2.0:
                    self.last_scan = now
                    self.dev.rescan()
        finally:
            if self.on_mac:
                self.go_mint("server exit")


if __name__ == "__main__":
    Server().run()
