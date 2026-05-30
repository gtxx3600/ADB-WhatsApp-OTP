# WhatsApp OTP Tools

Local tools for collecting 4- or 6-digit WhatsApp OTP notifications from Android emulators and showing them in a small copy-friendly web panel.

## Files

- `whatsapp_otp.py`: ADB forwarder. Run one process per emulator/device.
- `whatsapp_otp_receiver.py`: local web receiver and floating-style OTP panel.
- `test_whatsapp_otp.py`, `test_whatsapp_otp_receiver.py`: standard-library unit tests.

No third-party Python packages are required.

## Start The Receiver

```bash
cd whatsapp-otp-tools
python3 whatsapp_otp_receiver.py --host 127.0.0.1 --port 8810
```

Open:

```text
http://127.0.0.1:8810/
```

The panel keeps the newest OTP per phone number. It is visually optimized for 2-3 emulator cards in a narrow phone-shaped window, but it does not hard-limit the number of phones.

Clicking the phone copies the local Indonesian number without `+62` or `62`. For example, `+6281234567890` copies as `81234567890`.

## Start Forwarders

Run one forwarder per emulator/device:

```bash
python3 whatsapp_otp.py \
  --device emulator-5554 \
  --phone +6281111111111 \
  --mode poll \
  --url http://127.0.0.1:8810/otp

python3 whatsapp_otp.py \
  --device emulator-5556 \
  --phone +6282222222222 \
  --mode poll \
  --url http://127.0.0.1:8810/otp

python3 whatsapp_otp.py \
  --device 127.0.0.1:16384 \
  --phone +6283333333333 \
  --mode poll \
  --url http://127.0.0.1:8810/otp
```

Useful options:

```text
--device / -d        ADB serial, e.g. emulator-5554 or 127.0.0.1:16384
--phone / -p         WhatsApp phone number included in reports
--mode / -m          poll or logcat
--url                receiver or orchestrator /otp URL
--auth               Authorization header value, e.g. "Bearer token"
--strict-filter      only forward lines containing GoPay/OTP-related text
--dedupe-ttl         seconds before the same OTP can be sent again
```

`poll` is the recommended mode for most emulators because `logcat` notification text is not always available.

## Test

```bash
python3 -m unittest test_whatsapp_otp.py test_whatsapp_otp_receiver.py
python3 -m py_compile whatsapp_otp.py whatsapp_otp_receiver.py test_whatsapp_otp.py test_whatsapp_otp_receiver.py
```
