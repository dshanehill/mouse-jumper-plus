#!/usr/bin/env python3
"""Mouse Jumper+ clipboard helper for Mint (text only).

  mjp_clip.py get   -> prints the clipboard text (nothing if empty / not text)
  mjp_clip.py set   <- reads text from stdin and puts it on the clipboard

After "set", the text is handed to Cinnamon's clipboard manager (store()), so it
stays available after this helper exits.
"""
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

MAX_BYTES = 512 * 1024


def main():
    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    if len(sys.argv) > 1 and sys.argv[1] == "get":
        text = clip.wait_for_text()
        if text:
            data = text.encode("utf-8")[:MAX_BYTES]
            sys.stdout.buffer.write(data)
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "set":
        text = sys.stdin.buffer.read(MAX_BYTES).decode("utf-8", "replace")
        clip.set_text(text, -1)
        clip.store()
        GLib.timeout_add(400, Gtk.main_quit)    # give the clipboard manager time to copy it
        Gtk.main()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
