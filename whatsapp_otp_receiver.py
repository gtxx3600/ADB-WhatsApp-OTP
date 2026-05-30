#!/usr/bin/env python3
"""
Small local web panel for receiving WhatsApp OTP reports.

Run:
  python3 whatsapp_otp_receiver.py --host 127.0.0.1 --port 8810

Forwarder:
  python3 whatsapp_otp.py -d emulator-5554 -p +6281234567890 --url http://127.0.0.1:8810/otp
"""

import argparse
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8810
MAX_ITEMS = 200
OTP_RE = re.compile(r"(?<!\d)(\d{4}|\d{6})(?!\d)")
DIGITS_RE = re.compile(r"\D+")


@dataclass
class OtpEntry:
    phone: str
    copy_phone: str
    otp: str
    source: str
    count: int
    first_seen: int
    updated_at: int

    def as_dict(self) -> dict:
        return {
            "phone": self.phone,
            "copy_phone": self.copy_phone,
            "otp": self.otp,
            "source": self.source,
            "count": self.count,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
            "age_seconds": max(0, int(time.time()) - self.updated_at),
            "updated_label": time.strftime("%H:%M:%S", time.localtime(self.updated_at)),
        }


class OtpStore:
    def __init__(self, now: Callable[[], float] = time.time, max_items: int = MAX_ITEMS):
        self.now = now
        self.max_items = max(1, int(max_items))
        self._lock = threading.Lock()
        self._items: OrderedDict[str, OtpEntry] = OrderedDict()
        self._last_update = 0

    def record(self, otp: str, phone: str, source: str = "") -> OtpEntry:
        otp_code = normalize_otp(otp)
        phone_value = normalize_phone(phone)
        if not otp_code:
            raise ValueError("no 4- or 6-digit OTP found")
        if not phone_value:
            raise ValueError("phone is required")

        current = int(self.now())
        with self._lock:
            existing = self._items.pop(phone_value, None)
            if existing:
                entry = OtpEntry(
                    phone=phone_value,
                    copy_phone=copy_phone(phone_value),
                    otp=otp_code,
                    source=source or existing.source,
                    count=existing.count + 1,
                    first_seen=existing.first_seen,
                    updated_at=current,
                )
            else:
                entry = OtpEntry(
                    phone=phone_value,
                    copy_phone=copy_phone(phone_value),
                    otp=otp_code,
                    source=source,
                    count=1,
                    first_seen=current,
                    updated_at=current,
                )
            self._items[phone_value] = entry
            self._items.move_to_end(phone_value, last=False)
            while len(self._items) > self.max_items:
                self._items.popitem(last=True)
            self._last_update = current
            return entry

    def snapshot(self) -> dict:
        with self._lock:
            items = [entry.as_dict() for entry in self._items.values()]
            return {
                "ok": True,
                "count": len(items),
                "updated_at": self._last_update,
                "items": items,
            }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._last_update = int(self.now())


def normalize_phone(phone: str) -> str:
    return str(phone or "").strip().replace(" ", "").replace("-", "")


def copy_phone(phone: str) -> str:
    cleaned = normalize_phone(phone)
    digits = DIGITS_RE.sub("", cleaned)
    if cleaned.startswith("+62"):
        return digits[2:]
    if digits.startswith("62"):
        return digits[2:]
    return digits


def normalize_otp(value: str) -> str:
    match = OTP_RE.search(str(value or ""))
    return match.group(1) if match else ""


def make_handler(store: OtpStore):
    class OtpReceiverHandler(BaseHTTPRequestHandler):
        server_version = "WhatsappOtpReceiver/1.0"

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self._send_html(render_index())
                return
            if self.path.startswith("/api/state"):
                self._send_json(HTTPStatus.OK, store.snapshot())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if self.path == "/otp":
                body = self._read_json()
                raw_otp = str(body.get("otp", "") or body.get("text", "") or "")
                phone = str(body.get("phone", "") or body.get("phone_number", "") or "")
                source = str(body.get("source", "") or body.get("device", "") or "")
                try:
                    entry = store.record(raw_otp, phone, source=source)
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "otp": entry.otp,
                        "phone": entry.phone,
                        "copy_phone": entry.copy_phone,
                    },
                )
                return
            if self.path == "/api/clear":
                store.clear()
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

        def _send_json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}", flush=True)

    return OtpReceiverHandler


def render_index() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>WhatsApp OTP</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #05070b;
      --panel: #111827;
      --panel-2: #172033;
      --text: #f8fafc;
      --muted: #94a3b8;
      --line: #263348;
      --green: #22c55e;
      --cyan: #38bdf8;
      --red: #fb7185;
      --amber: #f59e0b;
    }

    * { box-sizing: border-box; }

    html, body {
      min-height: 100%;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
      overscroll-behavior: contain;
    }

    body {
      width: min(100vw, 420px);
      margin: 0 auto;
      touch-action: manipulation;
    }

    button {
      font: inherit;
      color: inherit;
      border: 0;
      cursor: pointer;
      touch-action: manipulation;
    }

    .app {
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      background: linear-gradient(180deg, #080b12 0%, #05070b 42%, #090d15 100%);
    }

    .header {
      position: sticky;
      top: 0;
      z-index: 10;
      padding: 12px 12px 10px;
      border-bottom: 1px solid var(--line);
      background: rgba(5, 7, 11, 0.94);
      backdrop-filter: blur(12px);
    }

    .title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    h1 {
      margin: 0;
      font-size: 19px;
      line-height: 1.15;
      font-weight: 800;
    }

    .status {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 10px;
      border-radius: 6px;
      background: #0d1f16;
      color: #86efac;
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }

    .sub {
      margin-top: 7px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }

    .clear {
      min-height: 34px;
      padding: 0 11px;
      border-radius: 6px;
      background: #21151a;
      color: #fda4af;
      font-size: 13px;
      font-weight: 700;
    }

    .list {
      flex: 1;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 9px;
    }

    .empty {
      min-height: 54vh;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.5;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }

    .card.newest {
      border-color: rgba(34, 197, 94, 0.75);
      box-shadow: inset 4px 0 0 var(--green);
    }

    .meta {
      min-height: 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 7px 9px;
      color: var(--muted);
      font-size: 12px;
      background: #0b111d;
      border-bottom: 1px solid var(--line);
    }

    .source {
      max-width: 54%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .values {
      padding: 8px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }

    .copy-button {
      width: 100%;
      min-height: 60px;
      padding: 8px 10px;
      border-radius: 7px;
      background: var(--panel-2);
      border: 1px solid #31415a;
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      gap: 10px;
      text-align: left;
      transition: background 160ms ease, border-color 160ms ease, transform 120ms ease;
    }

    .copy-button:hover,
    .copy-button:focus-visible {
      outline: none;
      background: #1b2940;
      border-color: #4b6486;
    }

    .copy-button:active {
      transform: translateY(1px);
    }

    .copy-button.phone { border-left: 4px solid var(--cyan); }
    .copy-button.otp { border-left: 4px solid var(--green); }

    .label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 3px;
    }

    .value {
      display: block;
      color: var(--text);
      font-size: clamp(25px, 9vw, 36px);
      line-height: 1;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }

    .otp .value {
      letter-spacing: 2px;
    }

    .phone .value {
      font-size: clamp(23px, 7vw, 31px);
    }

    .copy-icon {
      width: 34px;
      height: 34px;
      border-radius: 6px;
      display: grid;
      place-items: center;
      background: rgba(255, 255, 255, 0.08);
      color: #cbd5e1;
      font-size: 18px;
      font-weight: 900;
    }

    .toast {
      position: fixed;
      left: 50%;
      bottom: 18px;
      transform: translateX(-50%) translateY(70px);
      min-width: 180px;
      max-width: calc(100vw - 24px);
      padding: 12px 14px;
      border-radius: 8px;
      background: #dcfce7;
      color: #14532d;
      font-weight: 800;
      text-align: center;
      opacity: 0;
      transition: opacity 180ms ease, transform 180ms ease;
      pointer-events: none;
      z-index: 30;
    }

    .toast.show {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }

    @media (max-width: 330px) {
      .header { padding: 12px 10px 10px; }
      .list { padding: 8px; }
      .copy-button { min-height: 58px; padding: 8px 10px; }
      .value { font-size: 25px; }
      .phone .value { font-size: 24px; }
    }

    @media (min-width: 360px) {
      .card { min-height: 168px; }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        transition-duration: 0.001ms !important;
        scroll-behavior: auto !important;
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <header class="header">
      <div class="title-row">
        <h1>WhatsApp OTP</h1>
        <div class="status" id="status">等待中</div>
      </div>
      <div class="sub">
        <span id="summary">0 个手机号</span>
        <button class="clear" type="button" id="clearBtn">清空</button>
      </div>
    </header>

    <section class="list" id="list">
      <div class="empty">等待 whatsapp_otp.py 上报验证码</div>
    </section>
  </main>
  <div class="toast" id="toast">已复制</div>

  <script>
    const listEl = document.getElementById('list');
    const statusEl = document.getElementById('status');
    const summaryEl = document.getElementById('summary');
    const toastEl = document.getElementById('toast');
    const clearBtn = document.getElementById('clearBtn');
    let toastTimer = null;

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[ch]));
    }

    async function copyText(text, label) {
      const value = String(text || '');
      try {
        await navigator.clipboard.writeText(value);
      } catch (err) {
        const area = document.createElement('textarea');
        area.value = value;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
      }
      showToast(label + '已复制');
    }

    function showToast(message) {
      toastEl.textContent = message;
      toastEl.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toastEl.classList.remove('show'), 900);
    }

    function render(items) {
      summaryEl.textContent = `${items.length} 个手机号`;
      statusEl.textContent = items.length ? '已连接' : '等待中';
      if (!items.length) {
        listEl.innerHTML = '<div class="empty">等待 whatsapp_otp.py 上报验证码</div>';
        return;
      }
      listEl.innerHTML = items.map((item, index) => `
        <article class="card ${index === 0 ? 'newest' : ''}">
          <div class="meta">
            <span>${index === 0 ? '最新' : '历史'} · ${escapeHtml(item.updated_label)}</span>
            <span class="source">${escapeHtml(item.source || item.phone)}</span>
          </div>
          <div class="values">
            <button class="copy-button phone" type="button"
              aria-label="复制手机号 ${escapeHtml(item.copy_phone)}"
              data-copy="${escapeHtml(item.copy_phone)}" data-label="手机号">
              <span>
                <span class="label">复制手机号</span>
                <span class="value">${escapeHtml(item.copy_phone)}</span>
              </span>
              <span class="copy-icon">⧉</span>
            </button>
            <button class="copy-button otp" type="button"
              aria-label="复制验证码 ${escapeHtml(item.otp)}"
              data-copy="${escapeHtml(item.otp)}" data-label="验证码">
              <span>
                <span class="label">复制验证码</span>
                <span class="value">${escapeHtml(item.otp)}</span>
              </span>
              <span class="copy-icon">⧉</span>
            </button>
          </div>
        </article>
      `).join('');
    }

    async function refresh() {
      try {
        const response = await fetch('/api/state', { cache: 'no-store' });
        const data = await response.json();
        render(data.items || []);
      } catch (err) {
        statusEl.textContent = '断开';
      }
    }

    listEl.addEventListener('click', (event) => {
      const button = event.target.closest('[data-copy]');
      if (!button) return;
      copyText(button.dataset.copy, button.dataset.label || '');
    });

    clearBtn.addEventListener('click', async () => {
      await fetch('/api/clear', { method: 'POST' });
      await refresh();
      showToast('已清空');
    });

    refresh();
    setInterval(refresh, 900);
  </script>
</body>
</html>
"""


def parse_args(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="Receive WhatsApp OTP reports and show a copy-friendly panel.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    store = OtpStore()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    url = f"http://{args.host}:{args.port}/"
    print(f"[whatsapp-otp-receiver] listening on {url}", flush=True)
    print(f"[whatsapp-otp-receiver] forwarder url: {url}otp", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[whatsapp-otp-receiver] stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
