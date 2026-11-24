# Installing Mouse Jumper+

Mouse Jumper+ has two halves: a server on the Linux Mint PC, which the keyboard and mouse plug into, and a receiver plus a menu bar app on the Mac.

**Layout:** the Mac sits to the **left** of the Mint PC. Push past Mint's left edge to go to the Mac, and past the Mac's right edge to come back.

**Safety:** if anything goes wrong, press **Control + Alt + Escape twice** within one second. Control returns to Mint.

## What's in this repository

| Folder | Contents |
|---|---|
| `linux-mint/` | `mjp_server.py` (keyboard and mouse server), `mjp_reader.py` (sends screen text to the Mac), `mjp_clip.py` (clipboard), `mjp-ctl`, `mjp-server-loop` and the autostart entry |
| `mac/Sources/` | The menu bar app (Swift) |
| `mac/hid/` | `mjp-hid`, the receiver that turns network input into a virtual keyboard and mouse (C++) |
| `mac/launchd/` | Startup files for the receiver and the Karabiner driver service |

## 1. Make a shared token

Both computers use the same secret token so that only your own Mint PC can send input to your Mac. On the Mac, run:

```bash
openssl rand -hex 24
```

Keep the result handy for the steps below.

## 2. Linux Mint

1. Copy the files in `linux-mint/` to `~/.local/bin/` and make them executable:

   ```bash
   mkdir -p ~/.local/bin && cp linux-mint/mjp* ~/.local/bin/ && chmod +x ~/.local/bin/mjp*
   ```

2. Let your user read the keyboard and mouse directly, then log out and back in:

   ```bash
   sudo usermod -aG input "$USER"
   ```

3. Save the token, and the Mac's address on the first line of `mac-host`. That's its Tailscale or local network address. You can add other addresses the Mac may connect from on the lines after it.

   ```bash
   mkdir -p ~/.config/mouse-jumper && chmod 700 ~/.config/mouse-jumper
   ```

   ```bash
   echo "YOUR-TOKEN" > ~/.config/mouse-jumper/token && chmod 600 ~/.config/mouse-jumper/token
   ```

   ```bash
   echo "MAC-ADDRESS" > ~/.config/mouse-jumper/mac-host
   ```

4. Start it at login:

   ```bash
   mkdir -p ~/.config/autostart && cp linux-mint/mouse-jumper-plus.desktop ~/.config/autostart/
   ```

The Mint side needs Python 3 and the GTK and AT-SPI libraries, which come with Linux Mint Cinnamon. Check on it with `mjp-ctl status`, and find the log at `~/.local/state/mouse-jumper/server.log`.

## 3. Mac

### The Karabiner virtual keyboard and mouse driver

Install **Karabiner-DriverKit-VirtualHIDDevice** from [its releases page](https://github.com/pqrs-org/Karabiner-DriverKit-VirtualHIDDevice/releases), then turn it on:

```bash
sudo "/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager" activate
```

Approve the driver in **System Settings > General > Login Items & Extensions** if macOS asks.

### The receiver (`mjp-hid`)

1. Build it. This downloads the Karabiner headers it needs:

   ```bash
   mac/hid/build.sh
   ```

2. Install it with the token and the Mint PC's address. The address lets only your Mint PC connect:

   ```bash
   sudo mkdir -p "/Library/Application Support/MouseJumperPlus" && sudo cp mac/hid/mjp-hid "/Library/Application Support/MouseJumperPlus/"
   ```

   ```bash
   echo "YOUR-TOKEN" | sudo tee "/Library/Application Support/MouseJumperPlus/token" >/dev/null && sudo chmod 600 "/Library/Application Support/MouseJumperPlus/token"
   ```

   ```bash
   echo "MINT-ADDRESS" | sudo tee "/Library/Application Support/MouseJumperPlus/allowed-hosts" >/dev/null
   ```

3. Start the receiver and the driver service, now and at every startup:

   ```bash
   sudo cp mac/launchd/*.plist /Library/LaunchDaemons/ && sudo launchctl bootstrap system /Library/LaunchDaemons/com.mousejumperplus.vhid-daemon.plist && sudo launchctl bootstrap system /Library/LaunchDaemons/com.mousejumperplus.hid.plist
   ```

The receiver's log is at `/var/log/mjp-hid.log`.

### The menu bar app

1. Save the token and the Mint PC's address for your user:

   ```bash
   mkdir -p ~/"Library/Application Support/MouseJumperPlus" && echo "YOUR-TOKEN" > ~/"Library/Application Support/MouseJumperPlus/token" && chmod 600 ~/"Library/Application Support/MouseJumperPlus/token"
   ```

   ```bash
   echo "MINT-ADDRESS" > ~/"Library/Application Support/MouseJumperPlus/mint-host"
   ```

2. Build and install **Mouse Jumper+.app** in Applications. This needs the Xcode command line tools.

   ```bash
   mac/build.sh
   ```

   To sign it with your own certificate, set `SIGN_ID` before running the script. Without it, the app is signed just for this Mac.

3. Open **Mouse Jumper+** from Applications. It lives in the menu bar.

## Hearing Linux text read aloud

Mouse Jumper+ sends text from Mint to the Mac, but it doesn't speak. To hear it, install [Reader+](https://github.com/dshanehill/reader-plus) on the Mac. Then on Mint:

- **Cmd-click** a heading or paragraph to read from there.
- **Cmd-right-click** to read the whole window.
- **Tap Control** to stop.

## Uninstalling

**Mint:** remove `~/.config/autostart/mouse-jumper-plus.desktop` and the `mjp*` files in `~/.local/bin`, then log out.

**Mac:**

```bash
sudo launchctl bootout system/com.mousejumperplus.hid && sudo launchctl bootout system/com.mousejumperplus.vhid-daemon && sudo rm /Library/LaunchDaemons/com.mousejumperplus.*.plist && sudo rm -rf "/Library/Application Support/MouseJumperPlus" "/Applications/Mouse Jumper+.app"
```
