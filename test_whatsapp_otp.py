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

    def test_extract_otp_accepts_four_and_six_digit_codes(self):
        mod = load_module()

        self.assertEqual(mod.extract_otp("WhatsApp GoPay OTP 1234"), "1234")
        self.assertEqual(mod.extract_otp("WhatsApp GoPay OTP 123456"), "123456")

    def test_extract_otps_returns_all_codes_from_one_line(self):
        mod = load_module()

        self.assertEqual(
            mod.extract_otps("WhatsApp old OTP 123456 new OTP 777888"),
            ["123456", "777888"],
        )

    def test_dumpsys_extracts_new_code_when_line_contains_old_and_new_codes(self):
        mod = load_module()
        text = """
        NotificationRecord(pkg=com.whatsapp)
          android.text=old GoPay OTP 123456, new GoPay OTP 777888
        """

        self.assertEqual(mod.extract_from_dumpsys(text), ["123456", "777888"])

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
        clock = iter([100.0, 101.0, 106.1])
        deduper = mod.OtpDeduper(ttl_seconds=5, max_items=2, now=lambda: next(clock))

        deduper.mark("123456", "+1")
        self.assertTrue(deduper.seen("123456", "+1"))
        self.assertFalse(deduper.seen("123456", "+1"))

    def test_deduper_evicts_oldest_item_when_capacity_is_exceeded(self):
        mod = load_module()
        deduper = mod.OtpDeduper(ttl_seconds=60, max_items=2)

        deduper.mark("123456", "+1")
        deduper.mark("222222", "+1")
        deduper.mark("333333", "+1")

        self.assertFalse(deduper.seen("123456", "+1"))
        self.assertTrue(deduper.seen("222222", "+1"))
        self.assertTrue(deduper.seen("333333", "+1"))

    def test_failed_forward_does_not_mark_otp_duplicate(self):
        mod = load_module()
        cfg = mod.Config(phone="+6281234567890")
        deduper = mod.OtpDeduper()

        with mock.patch.object(mod, "push_otp", return_value=False):
            mod.handle_otp("123456", cfg, deduper)

        with mock.patch.object(mod, "push_otp", return_value=True) as push:
            mod.handle_otp("123456", cfg, deduper)

        self.assertEqual(push.call_count, 1)


if __name__ == "__main__":
    unittest.main()
