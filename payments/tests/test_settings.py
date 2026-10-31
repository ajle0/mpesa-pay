"""Startup checks in config/settings.py. Each case imports settings in a fresh Python process."""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parents[2]
SANDBOX_ENV = {
    "DEBUG": "1",
    "MPESA_MODE": "sandbox",
    "MPESA_CONSUMER_KEY": "key",
    "MPESA_CONSUMER_SECRET": "secret",
    "MPESA_PASSKEY": "passkey",
    "MPESA_CALLBACK_SECRET": "a-long-random-callback-secret",
}


def load_settings(**overrides):
    env = {**os.environ, **SANDBOX_ENV, **overrides, "DJANGO_SETTINGS_MODULE": "config.settings"}
    code = "import django; django.setup(); from django.conf import settings; print(settings.MPESA_CALLBACK_BASE_URL)"
    return subprocess.run([sys.executable, "-c", code], cwd=BASE_DIR, env=env, capture_output=True, text=True)


class CallbackUrlCheckTests(SimpleTestCase):
    def test_clean_url_is_accepted_and_tidied(self):
        result = load_settings(MPESA_CALLBACK_BASE_URL="  https://demo.ngrok-free.dev/ ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "https://demo.ngrok-free.dev")

    def test_malformed_urls_stop_startup(self):
        for bad in [
            "https://demo.trycloudflare.com# Long random string hidden in the callback path.",
            "http://demo.ngrok-free.dev",
            "https://demo.ngrok-free.dev/callbacks",
        ]:
            result = load_settings(MPESA_CALLBACK_BASE_URL=bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("MPESA_CALLBACK_BASE_URL must be just an https address", result.stderr, bad)
