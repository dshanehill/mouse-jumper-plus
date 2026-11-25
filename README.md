<p align="center"><img src="icon.png" alt="Mouse Jumper+ icon: a cartoon computer mouse with a white cane and a magnifying glass, jumping from one computer screen to another" width="200"></p>

# Mouse Jumper+: One Keyboard, Two Computers, and a Bridge for Your Screen Reader

*A low-vision user needed to share a keyboard and mouse between a Mac and a Linux PC without losing a screen magnifier or screen reader. No existing app could do it, so a new one was built.*

---

Picture a desk with a MacBook on one side and a small Linux Mint computer on the other. Slide the mouse off the edge of one screen and the pointer appears on the other. Point at a paragraph on the Linux screen, and its text is handed straight to the Mac, ready for a Mac screen reader to read aloud.

That's Mouse Jumper+. It lets a Mac and a Linux Mint PC share one keyboard and mouse, and it has an accessibility layer that carries text from Linux to the Mac, so one screen reader can cover both computers.

## The problem with existing apps

Sharing one keyboard and mouse between two computers isn't a new idea, and several apps already do it. For most people they work fine. For people with low vision, they fall short in a few key ways:

1. **They break screen magnifiers.** Most of these apps fake keystrokes and mouse movements in software. Magnifiers such as macOS Zoom often respond badly to that fake input, so zoom shortcuts stop working or the view jumps around.
2. **They disturb the other screen.** The pointer on the computer you just left can drift, dragging that computer's magnifier along with it.
3. **They ignore screen readers.** They move the pointer and nothing more. Your screen reader stays stuck on one computer.

Linux has its own screen reader, Orca, but many users find it lackluster. Switching between two different screen readers, one per computer, is tiring and slow.

Mouse Jumper+ was created by a low-vision user who tried the existing apps, found these gaps, and built an app that closes them.

## How the accessibility layer works

Underneath the keyboard and mouse sharing is an accessibility layer that connects the two computers. Mouse Jumper+ doesn't speak anything itself. Its job is to find the text on the Linux screen and deliver it to the Mac, where a screen reader does the talking. It does four things.

### 1. It sends Linux text to your Mac screen reader
Mouse Jumper+ finds the text on the Mint screen through the Linux accessibility system and sends it to the Mac. A Mac screen reader, such as **[Reader+](https://github.com/dshanehill/reader-plus)**, then reads it aloud. Reader+ is a separate app and isn't included with Mouse Jumper+.

- **Cmd-click a heading** to send that whole section.
- **Cmd-click a paragraph** to send it from there to the end.
- **Cmd-right-click** to send the whole window from the top.
- **Tap Control** to stop the reading.
- If the words are part of a picture, it sends an image of them so they can be read with text recognition.

### 2. It shows what's being read
When paired with Reader+, Mouse Jumper+ follows along on the Linux screen:

- The paragraph being read is outlined in yellow.
- The outlined paragraph scrolls into view, so you can follow along by eye.

### 3. It keeps magnifiers working
- On the Mac, Mouse Jumper+ shows up as a real keyboard and mouse. macOS Zoom and its shortcuts behave exactly as they do with a keyboard plugged in directly.
- While you're working on the Mac, the Mint pointer and magnifier stay still. When you come back, everything is where you left it.

### 4. It always gives you a way back
- Press **Control + Alt + Escape twice** to return control to the Mint PC immediately.
- If the connection drops or the Mac stops answering, control returns to Mint on its own.
- No one is ever left without a working keyboard.

## Built to run an AI workstation

Mouse Jumper+ wasn't built in isolation. It's the piece that makes a larger setup work.

Its creator took an older mini PC that was sitting unused and turned it into a dedicated AI hub running Linux Mint. Claude and other AI tools now run projects and workflows on that machine. The setup has three clear benefits:

- **The Mac stays fast.** AI tools running in the background use a lot of memory. Moving them to their own machine keeps the Mac's memory free.
- **The AI runs all day.** The mini PC can keep projects and workflows running without tying up the main computer.
- **An old computer gets a new job.** Hardware that was collecting dust becomes a useful workstation.

A second computer only helps if you can use it comfortably, and Mouse Jumper+ is what makes that possible. A typical moment looks like this:

1. Work on the Mac as usual.
2. Slide the pointer over to the Linux screen to check on an AI project.
3. Cmd-click the results to send them to the Mac, where Reader+ reads them aloud.
4. Slide back to the Mac and keep working.

That's one keyboard, one mouse, one magnifier and one screen reader across both machines.

## Who it's for

- People with low vision who use a Mac and a Linux Mint PC side by side
- Anyone running an AI workstation, home server or second computer next to a Mac
- Mac screen reader users who want to work on Linux without learning Orca

## What you need

- A Mac running macOS
- A PC running Linux Mint (Cinnamon), with the keyboard and mouse plugged into it
- Both computers on the same network, or connected through Tailscale
- To hear Linux text read aloud: a Mac screen reader such as [Reader+](https://github.com/dshanehill/reader-plus), installed separately

## Companion app: Audio Abduction+

**[Audio Abduction+](https://github.com/dshanehill/audio-abduction-plus)** does for sound what Mouse Jumper+ does for the keyboard and mouse. One click on its tray icon in Linux Mint sends all of the Linux PC's audio to the Mac, where it plays through the Mac's speakers or headphones.

It's especially handy if you wear Bluetooth headphones with your Mac: they stay paired to the Mac, and you skip the Bluetooth audio problems Linux is known for. Audio Abduction+ is a separate app and isn't included with Mouse Jumper+.

## What's next

Mouse Jumper+ is being actively tuned to work with **Lucent**, a screen magnifier app from the same creator.

## Get started

Setup steps for both computers are in [INSTALL.md](INSTALL.md). Mouse Jumper+ is free and open source under the [MIT License](LICENSE).
