// Mouse Jumper+ (Mac menu-bar app, phase 4.1)
//
// The keyboard and mouse are plugged into Mint. Mint streams them to the Mac's root
// helper (mjp-hid), which replays them through a virtual USB keyboard/mouse, so macOS
// moves the pointer natively (Zoom, screen readers, acceleration all normal).
//
// This app only does the Mac's part of the border:
//   * tells Mint when the Mac pointer is pinned against the Mac's RIGHT edge
//     (Mint then counts the push and hands control back),
//   * places the pointer at the matching height when control arrives from Mint,
//   * shows who has control, optional sound on each switch, pause switching.
// It needs no special macOS permissions.

import Cocoa
import ServiceManagement
import SwiftUI

/// The Mint PC's address, from ~/Library/Application Support/MouseJumperPlus/mint-host (first line).
let mintHost: String = {
    let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/MouseJumperPlus/mint-host")
    let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    return text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
        .first { !$0.isEmpty && !$0.hasPrefix("#") } ?? "127.0.0.1"
}()
let ctrlPort: UInt16 = 24850

// MARK: - Settings & log

enum Settings {
    static let d = UserDefaults.standard
    static var sound: Bool {
        get { d.object(forKey: "sound") as? Bool ?? true }
        set { d.set(newValue, forKey: "sound") }
    }
    /// Push needed to cross (raw mouse counts): 35 light, 60 normal, 110 firm.
    static var push: Int {
        get { let v = d.integer(forKey: "push"); return v == 0 ? 60 : v }
        set { d.set(newValue, forKey: "push") }
    }
    /// How far inside the edge you arrive (pixels).
    static var inset: Int {
        get { let v = d.integer(forKey: "inset"); return v == 0 ? 40 : v }
        set { d.set(newValue, forKey: "inset") }
    }
    /// Who speaks text found on Mint: "reader" (Reader+) or "voiceover".
    static var speaker: String {
        get { d.string(forKey: "speaker") ?? "reader" }
        set { d.set(newValue, forKey: "speaker") }
    }
    static var paused: Bool {
        get { d.bool(forKey: "paused") }
        set { d.set(newValue, forKey: "paused") }
    }
}

enum Log {
    static let url: URL = {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("MouseJumperPlus.log")
    }()
    static let fmt = ISO8601DateFormatter()
    static let lock = NSLock()
    static func write(_ s: String) {
        lock.lock(); defer { lock.unlock() }
        let line = "[\(fmt.string(from: Date()))] \(s)\n"
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); try? h.close()
        } else {
            try? line.data(using: .utf8)!.write(to: url)
        }
    }
}

func readToken() -> String? {
    let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/MouseJumperPlus/token")
    return (try? String(contentsOf: url, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
}

// MARK: - Screen geometry (CoreGraphics global coordinates, origin top-left)

enum Geometry {
    /// The display whose right side is the Mac's shared edge (right-most display).
    static func edgeDisplay() -> CGRect? {
        var ids = [CGDirectDisplayID](repeating: 0, count: 16); var n: UInt32 = 0
        guard CGGetActiveDisplayList(16, &ids, &n) == .success, n > 0 else { return nil }
        return ids.prefix(Int(n)).map { CGDisplayBounds($0) }.max { $0.maxX < $1.maxX }
    }

    static func pointer() -> CGPoint? { CGEvent(source: nil)?.location }

    /// (pinned to the right edge?, height fraction 0...1 on the edge display)
    static func edgeState() -> (Bool, Double) {
        guard let d = edgeDisplay(), let p = pointer() else { return (false, 0.5) }
        let frac = min(1, max(0, Double((p.y - d.minY) / max(1, d.height - 1))))
        let pinned = p.x >= d.maxX - 1.5 && p.y >= d.minY && p.y < d.maxY
        return (pinned, frac)
    }

    static func placeAtRightEdge(fraction: Double) {
        guard let d = edgeDisplay() else { return }
        let y = d.minY + CGFloat(fraction) * (d.height - 1)
        CGWarpMouseCursorPosition(CGPoint(x: d.maxX - CGFloat(Settings.inset), y: y))   // arrive inside the edge
        CGAssociateMouseAndMouseCursorPosition(1)
    }
}

// MARK: - Speech for Mint (spoken on the Mac, never on Mint)

import Vision

enum Speech {
    /// kind: "read" (full reading), "say" (short echo), "stop"
    static func deliver(kind: String, text: String) {
        if Settings.speaker == "voiceover" {
            guard kind != "stop" else { return }      // VoiceOver: a new announcement interrupts the old one
            let el = NSApp.mainWindow ?? NSApp.windows.first ?? NSApplication.shared
            NSAccessibility.post(element: el, notification: .announcementRequested,
                                 userInfo: [.announcement: String(text.prefix(4000)),
                                            .priority: NSAccessibilityPriorityLevel.high.rawValue])
            return
        }
        let name = Notification.Name("local.shane.ReaderPlus.\(kind == "read" ? "read" : kind == "stop" ? "stop" : "say")")
        DistributedNotificationCenter.default().postNotificationName(name, object: nil, userInfo: ["text": text], deliverImmediately: true)
    }

    /// Words in a picture sent from Mint (Mac's own text recognition, nothing leaves the Mac).
    static func recognize(_ png: Data, done: @escaping (String) -> Void) {
        guard let img = NSImage(data: png), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            DispatchQueue.main.async { done("") }; return
        }
        let req = VNRecognizeTextRequest { r, _ in
            let lines = (r.results as? [VNRecognizedTextObservation] ?? [])
                .sorted { $0.boundingBox.minY > $1.boundingBox.minY }
                .compactMap { $0.topCandidates(1).first?.string }
            DispatchQueue.main.async { done(lines.joined(separator: "\n")) }
        }
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = true
        DispatchQueue.global().async { try? VNImageRequestHandler(cgImage: cg).perform([req]) }
    }
}

// MARK: - Control channel to Mint

final class Control {
    enum Owner: Equatable { case unknown, mint, mac }
    var onOwner: ((Owner) -> Void)?
    var onConnection: ((Bool) -> Void)?
    var onClipboard: ((String) -> Void)?

    private let q = DispatchQueue(label: "mjp.control")        // connect + read loop (never returns)
    private let sq = DispatchQueue(label: "mjp.control.send")  // outgoing messages
    private let sendLock = NSLock()
    private var fd: Int32 = -1
    private var lastEdge = ""
    private var lastEdgeSent = Date.distantPast

    func start() { q.async { self.loop() } }

    private func loop() {
        while true {
            if connect() {
                DispatchQueue.main.async { self.onConnection?(true) }
                readLines()
                DispatchQueue.main.async { self.onConnection?(false); self.onOwner?(.unknown) }
            }
            sendLock.lock(); if fd >= 0 { close(fd); fd = -1 }; sendLock.unlock()
            sleep(2)
        }
    }

    private func connect() -> Bool {
        guard let token = readToken(), token.count >= 16 else {
            Log.write("no token file; cannot connect to Mint"); sleep(8); return false
        }
        fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        var tv = timeval(tv_sec: 3, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var one: Int32 = 1
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        var a = sockaddr_in()
        a.sin_family = sa_family_t(AF_INET)
        a.sin_port = ctrlPort.bigEndian
        inet_pton(AF_INET, mintHost, &a.sin_addr)
        let ok = withUnsafePointer(to: &a) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
            }
        }
        guard ok, sendRaw("MJPC \(token)\n") else { return false }
        var buf = [UInt8](repeating: 0, count: 3)
        guard recv(fd, &buf, 3, 0) == 3, String(bytes: buf, encoding: .ascii) == "ok\n" else {
            Log.write("Mint refused the control connection"); return false
        }
        tv = timeval(tv_sec: 0, tv_usec: 600_000)   // wake regularly to send heartbeats
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        Log.write("connected to Mint")
        lastEdge = ""
        if Settings.paused { _ = sendRaw("PAUSE 1\n") }
        _ = sendRaw("SET push \(Settings.push)\n")
        _ = sendRaw("SET inset \(Settings.inset)\n")
        return true
    }

    @discardableResult
    private func sendRaw(_ s: String) -> Bool {
        sendLock.lock(); defer { sendLock.unlock() }
        guard fd >= 0 else { return false }
        let b = Array(s.utf8)
        return b.withUnsafeBytes { Darwin.send(fd, $0.baseAddress, b.count, 0) } == b.count
    }

    /// Thread-safe sends from the main thread.
    func send(_ s: String) { sq.async { _ = self.sendRaw(s) } }

    private func readLines() {
        var pending = ""
        var lastHB = Date()
        var buf = [UInt8](repeating: 0, count: 1024)
        while true {
            let n = recv(fd, &buf, buf.count, 0)
            if n == 0 { Log.write("Mint closed the control connection"); return }
            if n < 0 && errno != EAGAIN && errno != EWOULDBLOCK { Log.write("control connection lost"); return }
            if n > 0 {
                pending += String(decoding: buf[0..<n], as: UTF8.self)
                while let nl = pending.firstIndex(of: "\n") {
                    let line = String(pending[..<nl]); pending.removeSubrange(...nl)
                    handle(line)
                }
            }
            if Date().timeIntervalSince(lastHB) > 0.5 {
                guard sendRaw("HB\n") else { return }
                lastHB = Date()
            }
        }
    }

    private func handle(_ line: String) {
        let p = line.split(separator: " ")
        if p.first == "SPEAK", p.count >= 2 {
            let kind = String(p[1])
            let text = p.count >= 3 ? (Data(base64Encoded: String(p[2])).flatMap { String(data: $0, encoding: .utf8) } ?? "") : ""
            DispatchQueue.main.async { Speech.deliver(kind: kind, text: text) }
            return
        }
        if p.first == "OCR", p.count >= 3, let png = Data(base64Encoded: String(p[2])) {
            let kind = String(p[1])
            Speech.recognize(png) { text in Speech.deliver(kind: kind, text: text.isEmpty ? "No text found." : text) }
            return
        }
        if p.first == "CLIP", p.count >= 2, let data = Data(base64Encoded: String(p[1])),
           let text = String(data: data, encoding: .utf8) {
            DispatchQueue.main.async { self.onClipboard?(text) }
            return
        }
        guard p.first == "OWNER", p.count >= 2 else { return }
        if p[1] == "mac" {
            let y = p.count >= 3 ? Double(p[2]) ?? 0.5 : 0.5
            DispatchQueue.main.async {
                if y >= 0 { Geometry.placeAtRightEdge(fraction: y) }   // < 0: switched by button, leave pointer
                self.onOwner?(.mac)
            }
        } else {
            DispatchQueue.main.async { self.onOwner?(.mint) }
        }
    }

    /// Called ~60 times a second from the main thread.
    func reportEdge(pinned: Bool, fraction: Double) {
        let msg = pinned ? String(format: "EDGE 1 %.4f\n", fraction) : "EDGE 0\n"
        let changedState = (msg.hasPrefix("EDGE 1")) != lastEdge.hasPrefix("EDGE 1")
        guard msg != lastEdge else { return }
        // pinned-state changes go out at once; height updates at most every 50 ms
        if changedState || Date().timeIntervalSince(lastEdgeSent) > 0.05 {
            lastEdge = msg; lastEdgeSent = Date()
            send(msg)
        }
    }
}

// MARK: - Shared state for the window and the menu

final class AppState: ObservableObject {
    @Published var owner: Control.Owner = .unknown
    @Published var connected = false
    @Published var paused = Settings.paused
    @Published var sound = Settings.sound
    @Published var openAtLogin = SMAppService.mainApp.status == .enabled
    @Published var push = Settings.push
    @Published var inset = Settings.inset
    @Published var receiverRunning = false
    @Published var speaker = Settings.speaker
}

// MARK: - Window (large, high-contrast, screen-reader friendly)

struct BigToggle: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool
    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 20, weight: .semibold))
                Text(detail).font(.system(size: 15)).foregroundStyle(.secondary)
            }
        }
        .toggleStyle(.switch)
        .controlSize(.large)
        .accessibilityHint(detail)
    }
}

struct ChoiceRow: View {
    let title: String
    let detail: String
    let options: [(String, Int)]
    @Binding var value: Int
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 20, weight: .semibold))
            Text(detail).font(.system(size: 15)).foregroundStyle(.secondary)
            Picker(title, selection: $value) {
                ForEach(options, id: \.1) { Text($0.0).font(.system(size: 17)).tag($0.1) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .controlSize(.large)
            .accessibilityLabel(title)
        }
    }
}

struct ChoiceRowString: View {
    let title: String
    let detail: String
    let options: [(String, String)]
    @Binding var value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 20, weight: .semibold))
            Text(detail).font(.system(size: 15)).foregroundStyle(.secondary)
            Picker(title, selection: $value) {
                ForEach(options, id: \.1) { Text($0.0).font(.system(size: 17)).tag($0.1) }
            }
            .pickerStyle(.segmented).labelsHidden().controlSize(.large).accessibilityLabel(title)
        }
    }
}

struct MainView: View {
    @ObservedObject var state: AppState
    let actions: AppActions

    var ownerText: String {
        if !state.connected { return "Not connected to Mint" }
        switch state.owner {
        case .mac: return "On the Mac"
        case .mint: return "On Mint"
        case .unknown: return "Connecting…"
        }
    }
    var ownerColor: Color {
        if !state.connected { return .orange }
        return state.owner == .mac ? .blue : .green
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                // ---- header
                HStack(spacing: 16) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 72, height: 72)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading) {
                        Text("Mouse Jumper+").font(.system(size: 30, weight: .bold))
                        Text("One keyboard and mouse for Mint and the Mac").font(.system(size: 16)).foregroundStyle(.secondary)
                    }
                }

                // ---- big status
                VStack(alignment: .leading, spacing: 8) {
                    Text("Keyboard and mouse are").font(.system(size: 18))
                    Text(ownerText)
                        .font(.system(size: 40, weight: .heavy))
                        .foregroundStyle(ownerColor)
                    if state.paused {
                        Text("Edge switching is paused").font(.system(size: 17, weight: .semibold)).foregroundStyle(.orange)
                    }
                    Text(state.receiverRunning ? "Mac receiver: running" : "Mac receiver: NOT running")
                        .font(.system(size: 15)).foregroundStyle(state.receiverRunning ? Color.secondary : Color.red)
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 14).fill(ownerColor.opacity(0.12)))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(ownerColor, lineWidth: 2))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Keyboard and mouse are \(ownerText)\(state.paused ? ". Edge switching is paused" : "")")

                // ---- switch buttons
                HStack(spacing: 14) {
                    Button { actions.goto("mac") } label: {
                        Label("Use on the Mac", systemImage: "laptopcomputer").font(.system(size: 19, weight: .semibold))
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .disabled(!state.connected || state.owner == .mac)
                    Button { actions.goto("mint") } label: {
                        Label("Use on Mint", systemImage: "desktopcomputer").font(.system(size: 19, weight: .semibold))
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .disabled(!state.connected || state.owner == .mint)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                Text("Or push the pointer past the shared edge: Mint's left side ↔ the Dell's right side. Emergency: Control + Option + Escape twice returns everything to Mint.")
                    .font(.system(size: 15)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                Divider()

                // ---- settings
                Text("Settings").font(.system(size: 24, weight: .bold)).accessibilityAddTraits(.isHeader)
                BigToggle(title: "Pause edge switching", detail: "Pushing past the edge does nothing. Buttons and the emergency key still work.",
                          isOn: Binding(get: { state.paused }, set: { actions.setPaused($0) }))
                BigToggle(title: "Sound when switching", detail: "\"Tink\" when control goes to the Mac, \"Pop\" when it comes back to Mint.",
                          isOn: Binding(get: { state.sound }, set: { actions.setSound($0) }))
                BigToggle(title: "Open at login", detail: "Start Mouse Jumper+ automatically when you log in to the Mac.",
                          isOn: Binding(get: { state.openAtLogin }, set: { actions.setLogin($0) }))
                ChoiceRowString(title: "Read Mint's screen with", detail: "Text found on Mint is spoken here on the Mac, never on Mint.",
                          options: [("Reader+", "reader"), ("VoiceOver", "voiceover")],
                          value: Binding(get: { state.speaker }, set: { actions.setSpeaker($0) }))
                ChoiceRow(title: "Push needed to cross", detail: "How hard you push past the edge before it switches.",
                          options: [("Light", 35), ("Normal", 60), ("Firm", 110)],
                          value: Binding(get: { state.push }, set: { actions.setPush($0) }))
                ChoiceRow(title: "Arrive this far from the edge", detail: "Further in means less chance of bouncing straight back.",
                          options: [("Close", 20), ("Normal", 40), ("Far", 90)],
                          value: Binding(get: { state.inset }, set: { actions.setInset($0) }))

                Divider()
                HStack {
                    Button("Show log") { actions.showLog() }.controlSize(.large)
                    Spacer()
                    Button("Quit Mouse Jumper+") { NSApp.terminate(nil) }.controlSize(.large)
                }
                .font(.system(size: 16))
            }
            .padding(28)
        }
        .frame(minWidth: 560, minHeight: 640)
    }
}

// MARK: - Actions (shared by window and menu)

final class AppActions {
    let state: AppState
    let control: Control
    init(state: AppState, control: Control) { self.state = state; self.control = control }

    func goto(_ where_: String) { control.send("GOTO \(where_)\n") }
    func setPaused(_ v: Bool) { Settings.paused = v; state.paused = v; control.send(v ? "PAUSE 1\n" : "PAUSE 0\n") }
    func setSound(_ v: Bool) { Settings.sound = v; state.sound = v }
    func setPush(_ v: Int) { Settings.push = v; state.push = v; control.send("SET push \(v)\n") }
    func setInset(_ v: Int) { Settings.inset = v; state.inset = v; control.send("SET inset \(v)\n") }
    func setLogin(_ v: Bool) {
        do { if v { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() } }
        catch { Log.write("open-at-login change failed: \(error)") }
        state.openAtLogin = SMAppService.mainApp.status == .enabled
    }
    func showLog() { NSWorkspace.shared.open(Log.url) }
    func setSpeaker(_ v: String) { Settings.speaker = v; state.speaker = v; control.send("SET speaker \(v)\n") }
}

// MARK: - App

final class App: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var item: NSStatusItem!
    let control = Control()
    let state = AppState()
    lazy var actions = AppActions(state: state, control: control)
    var window: NSWindow?
    var lastPasteboardCount = NSPasteboard.general.changeCount

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.font = NSFont.boldSystemFont(ofSize: 13)
        control.onConnection = { [weak self] c in self?.state.connected = c; self?.refreshMenu() }
        control.onOwner = { [weak self] o in self?.ownerChanged(o) }
        control.onClipboard = { [weak self] text in
            let pb = NSPasteboard.general
            if pb.string(forType: .string) != text { pb.clearContents(); pb.setString(text, forType: .string) }
            self?.lastPasteboardCount = pb.changeCount          // don't echo it back to Mint
        }
        control.start()

        // Reader+ reading progress for Mint readings -> Mint draws the highlight and scrolls
        DistributedNotificationCenter.default().addObserver(forName: Notification.Name("local.shane.ReaderPlus.progress"),
                                                            object: nil, queue: .main) { [weak self] n in
            guard let loc = n.userInfo?["location"] as? Int else { return }
            self?.control.send("PROG \(loc)\n")
        }

        // Mac pointer pinned at the right edge -> tell Mint (60 times a second while the Mac has control)
        Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            guard let self, self.state.connected, self.state.owner == .mac else { return }
            let (pinned, frac) = Geometry.edgeState()
            self.control.reportEdge(pinned: pinned, fraction: frac)
        }
        // Text copied on the Mac goes to Mint
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self, self.state.connected else { return }
            let pb = NSPasteboard.general
            guard pb.changeCount != self.lastPasteboardCount else { return }
            self.lastPasteboardCount = pb.changeCount
            if let text = pb.string(forType: .string), let data = text.data(using: .utf8), data.count <= 512 * 1024 {
                self.control.send("CLIP \(data.base64EncodedString())\n")
            }
        }
        // Is the root receiver (mjp-hid) up?
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.checkReceiver() }
        checkReceiver()

        if CommandLine.arguments.contains("--enable-login"), SMAppService.mainApp.status != .enabled {
            actions.setLogin(true)
        }
        refreshMenu()
        Log.write("Mouse Jumper+ (window build) launched")

        // Show the window when opened by hand, not when started at login.
        let ev = NSAppleEventManager.shared().currentAppleEvent
        let atLogin = ev?.eventID == kAEOpenApplication &&
            ev?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        if !atLogin && !CommandLine.arguments.contains("--no-window") { showWindow() }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showWindow(); return true
    }

    func checkReceiver() {
        DispatchQueue.global().async {
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            var a = sockaddr_in(); a.sin_family = sa_family_t(AF_INET); a.sin_port = UInt16(24851).bigEndian
            inet_pton(AF_INET, "127.0.0.1", &a.sin_addr)
            let ok = withUnsafePointer(to: &a) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 } }
            close(fd)
            DispatchQueue.main.async { self.state.receiverRunning = ok }
        }
    }

    @objc func showWindow() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 760),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            w.title = "Mouse Jumper+"
            w.contentViewController = NSHostingController(rootView: MainView(state: state, actions: actions))
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.center()
            window = w
        }
        NSApp.setActivationPolicy(.regular)          // Dock icon + Command-Tab while the window is open
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)        // back to menu bar only
    }

    func ownerChanged(_ o: Control.Owner) {
        let previous = state.owner
        state.owner = o
        if Settings.sound && previous != .unknown && o != .unknown && o != previous {
            NSSound(named: o == .mac ? "Tink" : "Pop")?.play()
        }
        if o != previous { Log.write("control is now on \(o)") }
        refreshMenu()
    }

    var statusText: String {
        if !state.connected { return "Not connected to Mint" }
        if state.paused { return "Switching paused" }
        switch state.owner {
        case .mac: return "Keyboard and mouse: on the Mac"
        case .mint: return "Keyboard and mouse: on Mint"
        case .unknown: return "Connected to Mint"
        }
    }

    func refreshMenu() {
        item.button?.title = !state.connected ? "MJ+ ○" : state.owner == .mac ? "MJ+ Mac" : "MJ+ Mint"
        item.button?.setAccessibilityLabel("Mouse Jumper Plus. \(statusText)")
        item.button?.toolTip = statusText
        let m = NSMenu()
        m.addItem(withTitle: statusText, action: nil, keyEquivalent: "")
        m.addItem(.separator())
        let open = NSMenuItem(title: "Open Mouse Jumper+ Window…", action: #selector(showWindow), keyEquivalent: ",")
        open.target = self; m.addItem(open)
        m.addItem(.separator())
        m.addItem(withTitle: "Back to Mint any time: Control+Option+Escape twice", action: nil, keyEquivalent: "")
        m.addItem(withTitle: "Quit Mouse Jumper+", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = m
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
