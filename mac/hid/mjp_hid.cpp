// mjp-hid — Mouse Jumper+ receiver for the Mac (runs as root, LaunchDaemon).
//
// Accepts ONE connection from the Mint server (allowed IPs + shared token), reads
// fixed 8-byte input frames and replays them through the Karabiner virtual keyboard
// and mouse, which macOS treats as real hardware (Zoom, Control+scroll, Secure Input,
// screen readers all behave natively). Sends a 1-byte heartbeat every 250 ms.
// On disconnect or any error: releases every key and button.
//
// Frame (little-endian, 8 bytes):  u8 type | u8 page | u16 code | i16 v1 | i16 v2
//   'K' key       page 0x07 keyboard / 0x0C consumer, code = HID usage, v1 = 1 down / 0 up
//   'B' button    code = 1..5, v1 = 1 down / 0 up
//   'M' motion    v1 = dx, v2 = dy   (raw device counts)
//   'W' wheel     v1 = vertical (+ up), v2 = horizontal (+ right)
//   'R' reset     release everything
// Key contents are never logged.

#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pqrs/karabiner/driverkit/virtual_hid_device_driver.hpp>
#include <pqrs/karabiner/driverkit/virtual_hid_device_service.hpp>
#include <pqrs/osx/autorelease_pool.hpp>
#include <set>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace drv = pqrs::karabiner::driverkit::virtual_hid_device_driver;
namespace svc = pqrs::karabiner::driverkit::virtual_hid_device_service;
using namespace std::chrono_literals;

static constexpr int kPort = 24851;
static const char* kTokenPath = "/Library/Application Support/MouseJumperPlus/token";
static const char* kAllowedPath = "/Library/Application Support/MouseJumperPlus/allowed-hosts";

// Addresses the Mint server may connect from: one per line in kAllowedPath, plus localhost.
static std::vector<std::string> allowed_hosts() {
  std::vector<std::string> out = {"127.0.0.1"};
  std::ifstream f(kAllowedPath);
  std::string line;
  while (std::getline(f, line)) {
    line.erase(0, line.find_first_not_of(" \t\r"));
    line.erase(line.find_last_not_of(" \t\r") + 1);
    if (!line.empty() && line[0] != '#') out.push_back(line);
  }
  return out;
}

static std::atomic<bool> g_quit{false}, g_kb_ready{false}, g_pt_ready{false};
static std::mutex g_mutex;   // guards client + state below
static std::unique_ptr<svc::client> g_client;

static void logline(const std::string& s) {
  auto t = std::time(nullptr);
  char buf[32];
  std::strftime(buf, sizeof buf, "%F %T", std::localtime(&t));
  std::cout << buf << " " << s << std::endl;
}

// ---------------------------------------------------------------- HID state
struct State {
  uint8_t modifiers = 0;
  std::set<uint16_t> keys, consumer;
  std::set<uint8_t> buttons;
} g_state;

static uint8_t modifier_bit(uint16_t usage) {   // 0xE0..0xE7 -> driver modifier bits
  static const drv::hid_report::modifier m[8] = {
      drv::hid_report::modifier::left_control, drv::hid_report::modifier::left_shift,
      drv::hid_report::modifier::left_option, drv::hid_report::modifier::left_command,
      drv::hid_report::modifier::right_control, drv::hid_report::modifier::right_shift,
      drv::hid_report::modifier::right_option, drv::hid_report::modifier::right_command};
  return (usage >= 0xE0 && usage <= 0xE7) ? static_cast<uint8_t>(m[usage - 0xE0]) : 0;
}

static void post_keyboard() {
  drv::hid_report::keyboard_input r;
  for (int b = 0; b < 8; ++b)
    if (g_state.modifiers & (1 << b)) r.modifiers.insert(static_cast<drv::hid_report::modifier>(1 << b));
  for (auto k : g_state.keys) r.keys.insert(k);
  g_client->async_post_report(r);
}

static void post_consumer() {
  drv::hid_report::consumer_input r;
  for (auto k : g_state.consumer) r.keys.insert(k);
  g_client->async_post_report(r);
}

static void post_pointing(int dx, int dy, int v, int h) {
  // 8-bit fields: split large values into several reports
  do {
    drv::hid_report::pointing_input r;
    for (auto b : g_state.buttons) r.buttons.insert(b);
    int sx = std::clamp(dx, -127, 127), sy = std::clamp(dy, -127, 127);
    int sv = std::clamp(v, -127, 127), sh = std::clamp(h, -127, 127);
    r.x = static_cast<uint8_t>(static_cast<int8_t>(sx));
    r.y = static_cast<uint8_t>(static_cast<int8_t>(sy));
    r.vertical_wheel = static_cast<uint8_t>(static_cast<int8_t>(sv));
    r.horizontal_wheel = static_cast<uint8_t>(static_cast<int8_t>(sh));
    g_client->async_post_report(r);
    dx -= sx; dy -= sy; v -= sv; h -= sh;
  } while (dx || dy || v || h);
}

static void release_all() {
  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_client) return;
  g_state = State{};
  post_keyboard();
  post_consumer();
  post_pointing(0, 0, 0, 0);
}

static void handle_frame(const uint8_t* f) {
  uint8_t type = f[0], page = f[1];
  uint16_t code; int16_t v1, v2;
  std::memcpy(&code, f + 2, 2); std::memcpy(&v1, f + 4, 2); std::memcpy(&v2, f + 6, 2);

  std::lock_guard<std::mutex> lock(g_mutex);
  if (!g_client || !g_kb_ready || !g_pt_ready) return;
  switch (type) {
    case 'K':
      if (page == 0x07) {
        if (uint8_t bit = modifier_bit(code)) {
          v1 ? (g_state.modifiers |= bit) : (g_state.modifiers &= ~bit);
        } else if (code >= 0x04 && code <= 0xA4) {
          if (v1) { if (g_state.keys.size() < 30) g_state.keys.insert(code); }
          else g_state.keys.erase(code);
        } else return;
        post_keyboard();
      } else if (page == 0x0C && code > 0 && code < 0x400) {
        v1 ? (void)g_state.consumer.insert(code) : (void)g_state.consumer.erase(code);
        post_consumer();
      }
      break;
    case 'B':
      if (code >= 1 && code <= 5) {
        v1 ? (void)g_state.buttons.insert(static_cast<uint8_t>(code)) : (void)g_state.buttons.erase(static_cast<uint8_t>(code));
        post_pointing(0, 0, 0, 0);
      }
      break;
    case 'M': post_pointing(v1, v2, 0, 0); break;
    case 'W': post_pointing(0, 0, v1, v2); break;
    case 'P': break;   // keep-alive
    case 'R':
      g_state = State{};
      post_keyboard(); post_consumer(); post_pointing(0, 0, 0, 0);
      break;
    default: break;   // unknown frame: ignore
  }
}

// ---------------------------------------------------------------- network
static std::string read_token() {
  std::ifstream f(kTokenPath);
  std::string t;
  std::getline(f, t);
  return t;
}

static bool read_exact(int fd, uint8_t* buf, size_t n) {
  size_t got = 0;
  while (got < n) {
    ssize_t r = recv(fd, buf + got, n - got, 0);
    if (r <= 0) return false;
    got += static_cast<size_t>(r);
  }
  return true;
}

static void serve_client(int fd, const std::string& peer) {
  // handshake: "MJP2 <token>\n" within 3 s
  timeval tv{3, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
  std::string line;
  char c;
  while (line.size() < 200 && recv(fd, &c, 1, 0) == 1 && c != '\n') line += c;
  const std::string token = read_token();
  if (token.size() < 16 || line != "MJP2 " + token) {
    logline("rejected " + peer + " (bad handshake)");
    return;
  }
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
  timeval idle{2, 0};   // Mint sends 'P' every 250 ms; silence = Mint gone
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &idle, sizeof idle);
  send(fd, "ok\n", 3, 0);
  logline("connected: " + peer);

  std::atomic<bool> alive{true};
  std::thread hb([&] {
    while (alive && !g_quit) {
      if (send(fd, "h", 1, MSG_NOSIGNAL) != 1) { alive = false; break; }
      std::this_thread::sleep_for(250ms);
    }
  });
  uint8_t frame[8];
  while (alive && !g_quit && read_exact(fd, frame, sizeof frame)) handle_frame(frame);
  alive = false;
  shutdown(fd, SHUT_RDWR);
  hb.join();
  release_all();
  logline("disconnected: " + peer + " (all keys released)");
}

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  std::signal(SIGTERM, [](int) { g_quit = true; });
  std::signal(SIGINT, [](int) { g_quit = true; });
  pqrs::dispatcher::extra::initialize_shared_dispatcher(
      pqrs::osx::autorelease_pool::execute_with_autorelease_pool);

  g_client = std::make_unique<svc::client>();
  g_client->connected.connect([] {
    logline("driver service connected");
    svc::virtual_hid_keyboard_parameters p;
    p.set_country_code(pqrs::hid::country_code::us);
    g_client->async_virtual_hid_keyboard_initialize(p);
    g_client->async_virtual_hid_pointing_initialize();
  });
  g_client->connect_failed.connect([](auto&&) { g_kb_ready = g_pt_ready = false; });
  g_client->closed.connect([] { g_kb_ready = g_pt_ready = false; logline("driver service closed"); });
  g_client->driver_version_mismatched.connect([](auto&& m) { if (m) logline("DRIVER VERSION MISMATCH"); });
  g_client->virtual_hid_keyboard_ready.connect([](auto&& r) { if (r != g_kb_ready) logline(std::string("keyboard ready=") + (r ? "1" : "0")); g_kb_ready = r; });
  g_client->virtual_hid_pointing_ready.connect([](auto&& r) { if (r != g_pt_ready) logline(std::string("pointing ready=") + (r ? "1" : "0")); g_pt_ready = r; });
  g_client->async_start();

  int srv = socket(AF_INET, SOCK_STREAM, 0);
  int one = 1;
  setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(kPort);
  a.sin_addr.s_addr = htonl(INADDR_ANY);
  if (bind(srv, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || listen(srv, 2) != 0) {
    logline("cannot listen on port 24851");
    return 1;
  }
  logline("mjp-hid listening on 24851");

  while (!g_quit) {
    sockaddr_in p{};
    socklen_t pl = sizeof p;
    int fd = accept(srv, reinterpret_cast<sockaddr*>(&p), &pl);
    if (fd < 0) continue;
    char ip[INET_ADDRSTRLEN] = {0};
    inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip);
    std::string peer(ip);
    bool ok = false;
    for (auto& allowed : allowed_hosts()) ok |= (peer == allowed);
    if (ok) serve_client(fd, peer);
    else logline("refused connection from " + peer);
    close(fd);
  }
  release_all();
  {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_client = nullptr;
  }
  pqrs::dispatcher::extra::terminate_shared_dispatcher();
  return 0;
}
