import importlib.util
import json
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("whatsapp_otp.py")


def load_module():
    spec = importlib.util.spec_from_file_location("whatsapp_otp", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WhatsappOtpTests(unittest.TestCase):
    def test_adb_cmd_includes_device_serial(self):
        mod = load_module()

        self.assertEqual(
            mod.adb_cmd("adb", "emulator-5554", ["shell", "echo", "ok"]),
            ["adb", "-s", "emulator-5554", "shell", "echo", "ok"],
        )

    def test_adb_cmd_supports_host_port_device_serial(self):
        mod = load_module()

        self.assertEqual(
            mod.adb_cmd("adb", "127.0.0.1:16384", ["shell", "echo", "ok"]),
            ["adb", "-s", "127.0.0.1:16384", "shell", "echo", "ok"],
        )

    def test_push_otp_posts_phone_with_otp(self):
        mod = load_module()
        cfg = mod.Config(
            otp_url="http://127.0.0.1:8800/otp",
            auth="Bearer test-token",
            phone="+6281234567890",
            device="emulator-5554",
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true}'

        with mock.patch.object(mod.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
            self.assertTrue(mod.push_otp("123456", cfg))

        request = urlopen.call_args.args[0]
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"otp": "123456", "phone": "+6281234567890", "source": "emulator-5554", "device": "emulator-5554"},
        )
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_deduper_tracks_otp_and_phone_with_ttl_and_capacity(self):
        mod = load_module()
        clock = iter([100.0, 100.0, 101.0, 106.1, 107.0, 108.0, 109.0])
        deduper = mod.OtpDeduper(ttl_seconds=5, max_items=2, now=lambda: next(clock))

        self.assertFalse(deduper.seen("123456", "+1"))
        self.assertTrue(deduper.seen("123456", "+1"))
        self.assertFalse(deduper.seen("123456", "+2"))
        self.assertFalse(deduper.seen("123456", "+1"))

        self.assertFalse(deduper.seen("222222", "+1"))
        self.assertFalse(deduper.seen("333333", "+1"))
        self.assertFalse(deduper.seen("123456", "+2"))


if __name__ == "__main__":
    unittest.main()
