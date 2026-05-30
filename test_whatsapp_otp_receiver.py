import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("whatsapp_otp_receiver.py")


def load_module():
    spec = importlib.util.spec_from_file_location("whatsapp_otp_receiver", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReceiverTests(unittest.TestCase):
    def test_strip_indonesia_country_code_for_copy(self):
        mod = load_module()

        self.assertEqual(mod.copy_phone("+6281234567890"), "81234567890")
        self.assertEqual(mod.copy_phone("6281234567890"), "81234567890")
        self.assertEqual(mod.copy_phone("081234567890"), "081234567890")

    def test_store_keeps_latest_otp_per_phone_and_recent_first(self):
        mod = load_module()
        store = mod.OtpStore(now=lambda: 1000)

        store.record("111111", "+6281111111111", source="emu-a")
        store.record("222222", "+6282222222222", source="emu-b")
        store.record("333333", "+6281111111111", source="emu-a")

        phones = [item["phone"] for item in store.snapshot()["items"]]
        self.assertEqual(phones, ["+6281111111111", "+6282222222222"])
        self.assertEqual(store.snapshot()["items"][0]["otp"], "333333")
        self.assertEqual(store.snapshot()["items"][0]["copy_phone"], "81111111111")

    def test_store_accepts_four_digit_otp(self):
        mod = load_module()
        store = mod.OtpStore(now=lambda: 1000)

        store.record("code: 1234", "+6281111111111", source="emu-a")

        self.assertEqual(store.snapshot()["items"][0]["otp"], "1234")

    def test_store_keeps_more_than_three_phones(self):
        mod = load_module()
        store = mod.OtpStore(now=lambda: 1000)

        for i in range(4):
            store.record(f"11111{i}", f"+62800000000{i}", source=f"emu-{i}")

        snapshot = store.snapshot()
        self.assertEqual(snapshot["count"], 4)
        self.assertEqual(snapshot["items"][0]["phone"], "+628000000003")
        self.assertIn("+628000000000", [item["phone"] for item in snapshot["items"]])

    def test_post_otp_endpoint_accepts_whatsapp_report(self):
        mod = load_module()
        store = mod.OtpStore(now=lambda: 1000)
        handler_cls = mod.make_handler(store)

        response = run_request(
            handler_cls,
            method="POST",
            path="/otp",
            body={"otp": "123456", "phone": "+6281234567890", "source": "emu"},
        )

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["json"]["ok"], True)
        self.assertEqual(response["json"]["copy_phone"], "81234567890")
        self.assertEqual(store.snapshot()["items"][0]["otp"], "123456")

    def test_index_contains_copy_buttons_and_polling_api(self):
        mod = load_module()

        html = mod.render_index()

        self.assertIn("copyText", html)
        self.assertIn("/api/state", html)
        self.assertIn("复制手机号", html)
        self.assertIn("复制验证码", html)


def run_request(handler_cls, method, path, body=None):
    request = DummyRequest(method=method, path=path, body=body)
    def fake_setup(self):
        self.rfile = self.request
        self.wfile = self.request

    with mock.patch.object(handler_cls, "setup", fake_setup):
        with mock.patch.object(handler_cls, "finish", lambda self: None):
            handler = handler_cls(request, ("127.0.0.1", 12345), None)
    return request.response()


class DummyRequest:
    def __init__(self, method, path, body=None):
        if body is None:
            body_bytes = b""
        else:
            body_bytes = json.dumps(body).encode("utf-8")
        headers = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            f"Content-Type: application/json\r\n"
            f"\r\n"
        ).encode("ascii")
        self.input_bytes = headers + body_bytes
        self._read_offset = 0
        self.output = bytearray()

    def makefile(self, *args, **kwargs):
        return self

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.input_bytes) - self._read_offset
        data = self.input_bytes[self._read_offset:self._read_offset + size]
        self._read_offset += len(data)
        return data

    def readline(self, size=-1):
        if self._read_offset >= len(self.input_bytes):
            return b""
        newline_at = self.input_bytes.find(b"\n", self._read_offset)
        if newline_at == -1:
            end = len(self.input_bytes)
        else:
            end = newline_at + 1
        if size is not None and size >= 0:
            end = min(end, self._read_offset + size)
        data = self.input_bytes[self._read_offset:end]
        self._read_offset = end
        return data

    def write(self, data):
        self.output.extend(data)
        return len(data)

    def flush(self):
        return None

    def response(self):
        raw = bytes(self.output)
        header_blob, _, body = raw.partition(b"\r\n\r\n")
        status_line = header_blob.splitlines()[0].decode("iso-8859-1")
        status = int(status_line.split()[1])
        parsed = None
        if body:
            parsed = json.loads(body.decode("utf-8"))
        return {"status": status, "body": body, "json": parsed, "raw": raw}


if __name__ == "__main__":
    unittest.main()
