#!/usr/bin/env python3
"""
Forward WhatsApp OTP notifications from one Android emulator/device to the GoPay orchestrator.

Examples:
  python3 whatsapp_otp.py --device emulator-5554 --phone +6281234567890 --mode poll
  python3 whatsapp_otp.py --device 127.0.0.1:7555 --phone 81234567890 --mode logcat \
    --url http://127.0.0.1:8800/otp --auth "Bearer my-secret-token"
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


DEFAULT_OTP_URL = "http://127.0.0.1:8800/otp"
DEFAULT_ADB = "adb"
DEFAULT_MODE = "poll"
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_DEDUPE_TTL = 180
DEFAULT_DEDUPE_MAX = 100

OTP_REGEX = re.compile(r"(?<!\d)(\d{6})(?!\d)")
WHATSAPP_MARKERS = ("com.whatsapp", "whatsapp")
GOPAY_MARKERS = ("gopay", "go pay", "otp", "kode", "code", "verification", "verifikasi")


@dataclass
class Config:
    otp_url: str = DEFAULT_OTP_URL
    auth: str = ""
    phone: str = ""
    device: str = ""
    mode: str = DEFAULT_MODE
    adb: str = DEFAULT_ADB
    poll_interval: float = DEFAULT_POLL_INTERVAL
    dedupe_ttl: int = DEFAULT_DEDUPE_TTL
    dedupe_max: int = DEFAULT_DEDUPE_MAX
    strict_filter: bool = False
    timeout: float = 5.0


class OtpDeduper:
    def __init__(
        self,
        ttl_seconds: int = DEFAULT_DEDUPE_TTL,
        max_items: int = DEFAULT_DEDUPE_MAX,
        now: Callable[[], float] = time.time,
    ):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_items = max(1, int(max_items))
        self.now = now
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()

    def seen(self, otp: str, phone: str = "") -> bool:
        current = self.now()
        self._prune(current)
        key = (otp, phone)
        if key in self._seen:
            self._seen.move_to_end(key)
            self._seen[key] = current
            return True
        self._seen[key] = current
        while len(self._seen) > self.max_items:
            self._seen.popitem(last=False)
        return False

    def _prune(self, current: float) -> None:
        expired = [
            key for key, seen_at in self._seen.items()
            if current - seen_at > self.ttl_seconds
        ]
        for key in expired:
            self._seen.pop(key, None)


def adb_cmd(adb: str, device: str, args: Iterable[str]) -> list[str]:
    cmd = [adb]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(args)
    return cmd


def log(message: str) -> None:
    print(message, flush=True)


def extract_otp(line: str, strict_filter: bool = False) -> Optional[str]:
    lowered = line.lower()
    if strict_filter and not any(marker in lowered for marker in GOPAY_MARKERS):
        return None
    match = OTP_REGEX.search(line)
    return match.group(1) if match else None


def is_whatsapp_line(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in WHATSAPP_MARKERS)


def push_otp(otp: str, cfg: Config) -> bool:
    data = {"otp": otp}
    if cfg.phone:
        data["phone"] = cfg.phone
    if cfg.device:
        data["source"] = cfg.device
        data["device"] = cfg.device

    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.auth:
        headers["Authorization"] = cfg.auth

    req = urllib.request.Request(cfg.otp_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = raw
            log(f"  -> forwarded ok: {body}")
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        log(f"  -> forwarded failed: HTTP {exc.code} {detail}")
    except Exception as exc:
        log(f"  -> forwarded failed: {exc}")
    return False


def handle_otp(otp: str, cfg: Config, deduper: OtpDeduper) -> None:
    if deduper.seen(otp, cfg.phone):
        log(f"[{time.strftime('%H:%M:%S')}] duplicate OTP skipped: {otp} phone={mask_phone(cfg.phone)}")
        return
    log(f"[{time.strftime('%H:%M:%S')}] captured OTP: {otp} phone={mask_phone(cfg.phone)}")
    push_otp(otp, cfg)


def mask_phone(phone: str) -> str:
    if not phone:
        return "*"
    if len(phone) <= 4:
        return "*" * len(phone)
    return f"***{phone[-4:]}"


def run_logcat(cfg: Config) -> None:
    log("[whatsapp-otp] mode=logcat")
    log(f"[whatsapp-otp] device={cfg.device or '(default)'} phone={mask_phone(cfg.phone)} target={cfg.otp_url}")
    log("[whatsapp-otp] press Ctrl+C to stop")

    cmd = adb_cmd(
        cfg.adb,
        cfg.device,
        ["logcat", "-s", "NotificationService:I", "StatusBarNotification:I"],
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    deduper = OtpDeduper(cfg.dedupe_ttl, cfg.dedupe_max)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if not is_whatsapp_line(line):
                continue
            otp = extract_otp(line, strict_filter=cfg.strict_filter)
            if otp:
                handle_otp(otp, cfg, deduper)
    finally:
        if proc.poll() is None:
            proc.terminate()
        stderr = ""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read().strip()
            except Exception:
                stderr = ""
        if stderr:
            log(f"[whatsapp-otp] adb logcat stderr: {stderr}")


def run_poll(cfg: Config) -> None:
    log(f"[whatsapp-otp] mode=poll interval={cfg.poll_interval:g}s")
    log(f"[whatsapp-otp] device={cfg.device or '(default)'} phone={mask_phone(cfg.phone)} target={cfg.otp_url}")
    log("[whatsapp-otp] press Ctrl+C to stop")

    deduper = OtpDeduper(cfg.dedupe_ttl, cfg.dedupe_max)
    cmd = adb_cmd(cfg.adb, cfg.device, ["shell", "dumpsys", "notification", "--noredact"])

    while True:
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=8)
            for otp in extract_from_dumpsys(out, strict_filter=cfg.strict_filter):
                handle_otp(otp, cfg, deduper)
        except subprocess.CalledProcessError as exc:
            log(f"[{time.strftime('%H:%M:%S')}] adb failed: {exc.output.strip()}")
        except subprocess.TimeoutExpired:
            log(f"[{time.strftime('%H:%M:%S')}] adb dumpsys timeout")
        except Exception as exc:
            log(f"[{time.strftime('%H:%M:%S')}] error: {exc}")
        time.sleep(cfg.poll_interval)


def extract_from_dumpsys(text: str, strict_filter: bool = False) -> list[str]:
    otps: list[str] = []
    in_whatsapp_block = False

    for line in text.splitlines():
        lowered = line.lower()
        if "pkg=" in lowered:
            in_whatsapp_block = "com.whatsapp" in lowered
        elif "notificationrecord" in lowered and "com.whatsapp" not in lowered:
            in_whatsapp_block = False

        if not in_whatsapp_block and not is_whatsapp_line(line):
            continue

        otp = extract_otp(line, strict_filter=strict_filter)
        if otp:
            otps.append(otp)

    return otps


def parse_args(argv: Optional[list[str]] = None) -> Config:
    parser = argparse.ArgumentParser(
        description="Forward WhatsApp 6-digit OTP notifications from one ADB device to orchestrator /otp.",
    )
    parser.add_argument("--device", "-d", default="", help="ADB serial, e.g. emulator-5554 or 127.0.0.1:7555")
    parser.add_argument("--phone", "-p", required=True, help="WhatsApp phone number to include in /otp payload")
    parser.add_argument("--mode", "-m", choices=("poll", "logcat"), default=DEFAULT_MODE, help="ADB capture mode")
    parser.add_argument("--url", default=DEFAULT_OTP_URL, help="orchestrator OTP URL")
    parser.add_argument("--auth", default="", help='Authorization header value, e.g. "Bearer token"')
    parser.add_argument("--adb", default=DEFAULT_ADB, help="ADB executable path")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="poll mode interval seconds")
    parser.add_argument("--dedupe-ttl", type=int, default=DEFAULT_DEDUPE_TTL, help="seconds before the same OTP can be sent again")
    parser.add_argument("--dedupe-max", type=int, default=DEFAULT_DEDUPE_MAX, help="maximum OTP keys kept in memory")
    parser.add_argument(
        "--strict-filter",
        action="store_true",
        help="only forward lines that also contain GoPay/OTP-related text",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP forward timeout seconds")

    args = parser.parse_args(argv)
    return Config(
        otp_url=args.url,
        auth=args.auth,
        phone=args.phone.strip(),
        device=args.device.strip(),
        mode=args.mode,
        adb=args.adb,
        poll_interval=max(0.5, args.poll_interval),
        dedupe_ttl=args.dedupe_ttl,
        dedupe_max=args.dedupe_max,
        strict_filter=args.strict_filter,
        timeout=args.timeout,
    )


def main(argv: Optional[list[str]] = None) -> int:
    cfg = parse_args(argv)
    try:
        if cfg.mode == "logcat":
            run_logcat(cfg)
        else:
            run_poll(cfg)
    except KeyboardInterrupt:
        log("\n[whatsapp-otp] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
