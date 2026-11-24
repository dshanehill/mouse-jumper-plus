#!/usr/bin/env python3
"""Mouse Jumper+ reader agent for Mint (Reader+ for Mint).

Finds text on Mint through AT-SPI (the Linux accessibility layer) and hands it to the
Mouse Jumper+ server, which sends it to the Mac. Nothing is spoken on Mint: Reader+
(or VoiceOver) speaks it on the Mac.

Gestures come from mjp_server (it sees the raw keyboard/mouse), over localhost UDP 24860:
  click X Y    Cmd-click: read from here (heading -> its section, paragraph -> to the end)
  rclick X Y   Cmd-right-click: read the whole window from the top
  stop         Control tapped by itself
  prog N       Reader+ has reached character N of the current reading (-1 = finished):
               the paragraph being read gets a yellow outline and is scrolled into view.

Output: lines on the unix socket $XDG_RUNTIME_DIR/mjp-speak.sock
  SPEAK read|say|stop <base64 utf-8>     OCR read <base64 png>
"""
import base64
import os
import socket
import sys
import threading
import time

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cairo  # noqa: E402
import pyatspi  # noqa: E402

CMD_PORT = 24860
SPEAK_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "mjp-speak.sock")
LOG = os.path.expanduser("~/.local/state/mouse-jumper/reader.log")
MAX_TEXT = 60_000
OBJ = "￼"            # embedded-object character used by AT-SPI for links etc.

DOC_ROLES = {pyatspi.ROLE_DOCUMENT_WEB, pyatspi.ROLE_DOCUMENT_FRAME, pyatspi.ROLE_DOCUMENT_TEXT,
             pyatspi.ROLE_DOCUMENT_EMAIL, pyatspi.ROLE_DOCUMENT_PRESENTATION, pyatspi.ROLE_DOCUMENT_SPREADSHEET}
BLOCK_ROLES = {pyatspi.ROLE_PARAGRAPH, pyatspi.ROLE_HEADING, pyatspi.ROLE_LIST_ITEM, pyatspi.ROLE_STATIC,
               pyatspi.ROLE_LABEL, pyatspi.ROLE_TEXT, pyatspi.ROLE_SECTION, pyatspi.ROLE_BLOCK_QUOTE,
               pyatspi.ROLE_TABLE_CELL, pyatspi.ROLE_CAPTION, pyatspi.ROLE_ARTICLE, pyatspi.ROLE_FOOTNOTE}
CONTROL_ROLES = {pyatspi.ROLE_PUSH_BUTTON, pyatspi.ROLE_TOGGLE_BUTTON, pyatspi.ROLE_CHECK_BOX,
                 pyatspi.ROLE_RADIO_BUTTON, pyatspi.ROLE_MENU_ITEM, pyatspi.ROLE_MENU, pyatspi.ROLE_LINK,
                 pyatspi.ROLE_COMBO_BOX, pyatspi.ROLE_PAGE_TAB, pyatspi.ROLE_LIST_ITEM, pyatspi.ROLE_ENTRY,
                 pyatspi.ROLE_PASSWORD_TEXT, pyatspi.ROLE_SPIN_BUTTON, pyatspi.ROLE_SLIDER,
                 pyatspi.ROLE_TABLE_CELL, pyatspi.ROLE_ICON, pyatspi.ROLE_CHECK_MENU_ITEM,
                 pyatspi.ROLE_RADIO_MENU_ITEM, pyatspi.ROLE_TOOL_BAR}


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 500_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- output to the server
_out_lock = threading.Lock()


def emit(kind, payload, verb="SPEAK"):
    data = payload if isinstance(payload, bytes) else payload.encode("utf-8", "replace")
    line = f"{verb} {kind} {base64.b64encode(data).decode()}\n".encode()
    with _out_lock:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(SPEAK_SOCK)
            s.sendall(line)
            s.close()
        except OSError as e:
            log(f"could not reach server: {e}")


# ---------------------------------------------------------------- AT-SPI helpers
def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def role_name(acc):
    return safe(acc.getLocalizedRoleName, "") or ""


def text_of(acc):
    """Full text of an accessible, with embedded objects (links) filled in."""
    t = safe(lambda: acc.queryText(), None)
    if t is None:
        return ""
    s = safe(lambda: t.getText(0, -1), "") or ""
    if OBJ in s:
        parts, i = [], 0
        for ch in s:
            if ch == OBJ:
                child = safe(lambda: acc.getChildAtIndex(i), None)
                i += 1
                parts.append(text_of(child) or (safe(lambda: child.name, "") if child else ""))
            else:
                parts.append(ch)
        s = "".join(parts)
    return " ".join(s.split())


def active_window():
    desktop = pyatspi.Registry.getDesktop(0)
    for app in desktop:
        if app is None:
            continue
        for win in app:
            if win is not None and safe(lambda: win.getState().contains(pyatspi.STATE_ACTIVE), False):
                return win
    return None


def deepest_at(x, y):
    """Most specific accessible under screen point (x, y), searching the active window first."""
    candidates = []
    win = active_window()
    if win is not None:
        candidates.append(win)
    desktop = pyatspi.Registry.getDesktop(0)
    for app in desktop:
        for w in (app or []):
            if w is not None and w not in candidates:
                candidates.append(w)
    for top in candidates:
        comp = safe(lambda: top.queryComponent(), None)
        if comp is None or not safe(lambda: comp.contains(x, y, pyatspi.DESKTOP_COORDS), False):
            continue
        node = top
        for _ in range(60):
            c = safe(lambda: node.queryComponent(), None)
            child = safe(lambda: c.getAccessibleAtPoint(x, y, pyatspi.DESKTOP_COORDS), None) if c else None
            if child is None or child == node:
                break
            node = child
        return node, top
    return None, None


def ancestors(acc):
    out, node = [], acc
    for _ in range(80):
        node = safe(lambda: node.parent, None)
        if node is None:
            break
        out.append(node)
    return out


def walk(acc, depth=0):
    """Depth-first list of text blocks (accessible, text) under acc."""
    if acc is None or depth > 200:
        return []
    role = safe(acc.getRole, None)
    if role in BLOCK_ROLES or role in (pyatspi.ROLE_LINK,) and depth == 0:
        t = text_of(acc)
        if t:
            return [(acc, t)]
    out = []
    for i in range(safe(lambda: acc.childCount, 0) or 0):
        child = safe(lambda: acc.getChildAtIndex(i), None)
        out.extend(walk(child, depth + 1))
    if not out:
        t = text_of(acc)
        if t and role not in DOC_ROLES:
            out.append((acc, t))
    return out


def heading_level(acc):
    attrs = safe(lambda: dict(a.split(":", 1) for a in acc.getAttributes() if ":" in a), {}) or {}
    try:
        return int(attrs.get("level", "2"))
    except ValueError:
        return 2


# ---------------------------------------------------------------- reading
def read_at(x, y):
    hit, top = deepest_at(x, y)
    if hit is None:
        return ocr_around(x, y)
    role = safe(hit.getRole, None)
    chain = [hit] + ancestors(hit)

    # Controls: "Save, button" (plus value / state where useful)
    if role in CONTROL_ROLES and role not in (pyatspi.ROLE_TABLE_CELL, pyatspi.ROLE_LIST_ITEM):
        name = safe(lambda: hit.name, "") or text_of(hit)
        if name:
            emit("say", f"{name}, {role_name(hit)}")
            return

    # Documents: heading -> its section; paragraph -> from here to the end
    doc = next((a for a in chain if safe(a.getRole, None) in DOC_ROLES), None)
    block = next((a for a in chain if safe(a.getRole, None) in BLOCK_ROLES), None)
    if doc is not None and block is not None:
        blocks = walk(doc)
        start = next((i for i, (a, _t) in enumerate(blocks) if a == block or a in chain), None)
        if start is not None:
            read_blocks(blocks[start:])           # from here to the end of the page
            return

    # Anything else with text
    t = text_of(block or hit) or safe(lambda: hit.name, "")
    if t:
        emit("read" if len(t) > 80 else "say", t)
        return
    ocr_around(x, y)


def read_window(x, y):
    hit, top = deepest_at(x, y)
    win = top or active_window()
    if win is None:
        return ocr_around(x, y, whole=True)
    blocks = walk(win)
    if sum(len(t) for _a, t in blocks) < 20:
        return ocr_window(win)
    read_blocks(blocks)


def read_blocks(blocks):
    """Send blocks as one reading; remember where each starts so the highlight can follow."""
    global READING
    parts, spans, pos = [], [], 0
    for acc, t in blocks:
        if pos > MAX_TEXT:
            break
        if parts:
            pos += 2                                   # the "\n\n" between blocks
        spans.append((pos, pos + len(t), acc))
        parts.append(t)
        pos += len(t)
    READING = {"spans": spans, "current": -1}
    emit("read", "\n\n".join(parts))
    log(f"reading {len(spans)} blocks, {pos} characters")


# ---------------------------------------------------------------- highlight that follows the reading
READING = {"spans": [], "current": -1}


class Highlight:
    """Click-through yellow outline drawn above everything on Mint."""
    PAD = 6

    def __init__(self):
        self.win = None

    def _make(self):
        w = Gtk.Window(type=Gtk.WindowType.POPUP)
        screen = w.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            w.set_visual(visual)
        w.set_app_paintable(True)
        w.set_keep_above(True)
        w.set_accept_focus(False)
        w.connect("draw", self._draw)
        w.realize()
        w.input_shape_combine_region(cairo.Region())    # clicks pass straight through
        self.win = w

    def _draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        a = w.get_allocation()
        cr.set_line_width(5)
        cr.set_source_rgba(1.0, 0.85, 0.0, 0.95)        # Reader+ yellow
        cr.rectangle(3, 3, a.width - 6, a.height - 6)
        cr.stroke()
        return True

    def show(self, x, y, w, h):
        if self.win is None:
            self._make()
        p = self.PAD
        self.win.move(int(x - p), int(y - p))
        self.win.resize(max(12, int(w + 2 * p)), max(12, int(h + 2 * p)))
        self.win.show_all()
        self.win.queue_draw()

    def hide(self):
        if self.win is not None:
            self.win.hide()


HIGHLIGHT = Highlight()


def follow(location):
    if location < 0:
        HIGHLIGHT.hide()
        READING["current"] = -1
        return
    spans = READING["spans"]
    idx = next((i for i, (a, b, _acc) in enumerate(spans) if a <= location < b + 2), None)
    if idx is None or idx == READING["current"]:
        return
    READING["current"] = idx
    acc = spans[idx][2]
    comp = safe(lambda: acc.queryComponent(), None)
    if comp is None:
        return
    safe(lambda: comp.scrollTo(pyatspi.SCROLL_ANYWHERE))      # bring the paragraph on screen

    def place():
        if READING["current"] != idx:
            return False
        e = safe(lambda: comp.getExtents(pyatspi.DESKTOP_COORDS), None)
        if e is not None and e.width > 0 and e.height > 0:
            HIGHLIGHT.show(e.x, e.y, e.width, e.height)
        else:
            HIGHLIGHT.hide()
        return False
    GLib.timeout_add(120, place)                      # after the page has scrolled


# ---------------------------------------------------------------- OCR fallback (recognised on the Mac)
def grab_png(x, y, w, h):
    root = Gdk.get_default_root_window()
    sw, sh = root.get_width(), root.get_height()
    x, y = max(0, int(x)), max(0, int(y))
    w, h = min(int(w), sw - x), min(int(h), sh - y)
    if w < 8 or h < 8:
        return None
    pb = Gdk.pixbuf_get_from_window(root, x, y, w, h)
    if pb is None:
        return None
    ok, buf = pb.save_to_bufferv("png", [], [])
    return bytes(buf) if ok else None


def ocr_around(x, y, whole=False):
    READING.update(spans=[], current=-1); HIGHLIGHT.hide()
    png = grab_png(0, 0, 10_000, 10_000) if whole else grab_png(x - 450, y - 160, 900, 320)
    if png:
        emit("read", png, verb="OCR")
    else:
        emit("say", "Nothing to read here.")


def ocr_window(win):
    comp = safe(lambda: win.queryComponent(), None)
    ext = safe(lambda: comp.getExtents(pyatspi.DESKTOP_COORDS), None) if comp else None
    png = grab_png(ext.x, ext.y, ext.width, ext.height) if ext else None
    if png:
        emit("read", png, verb="OCR")


# ---------------------------------------------------------------- focus echo
_last_focus = [""]


def on_focus(event):
    try:
        if not event.detail1:
            return
        acc = event.source
        name = safe(lambda: acc.name, "") or ""
        role = role_name(acc)
        if safe(acc.getRole, None) in (pyatspi.ROLE_PASSWORD_TEXT,):
            name = name or "password"
        msg = f"{name}, {role}" if name else role
        if msg and msg != _last_focus[0]:
            _last_focus[0] = msg
            emit("say", msg)
    except Exception as e:
        log(f"focus event error: {e}")


# ---------------------------------------------------------------- commands from mjp_server
def on_command(sock, _cond):
    try:
        data, _addr = sock.recvfrom(256)
    except OSError:
        return True
    parts = data.decode(errors="ignore").split()
    if not parts:
        return True
    try:
        if parts[0] != "prog":
            log(f"command: {parts[0]}")
        if parts[0] == "click" and len(parts) == 3:
            read_at(int(parts[1]), int(parts[2]))
        elif parts[0] == "rclick" and len(parts) == 3:
            read_window(int(parts[1]), int(parts[2]))
        elif parts[0] == "prog" and len(parts) == 2:
            follow(int(parts[1]))
        elif parts[0] == "stop":
            HIGHLIGHT.hide()
            emit("stop", "")
    except Exception as e:
        log(f"command {parts[0]} failed: {e}")
        emit("say", "Sorry, that could not be read.")
    return True


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", CMD_PORT))
    GLib.io_add_watch(sock.fileno(), GLib.IO_IN, lambda fd, c: on_command(sock, c))
    Gtk.init([])
    log("reader agent started")
    pyatspi.Registry.start()          # runs the GLib main loop


if __name__ == "__main__":
    sys.exit(main())
