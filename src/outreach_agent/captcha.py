"""2Captcha client for the MC image captcha.

Average solve time per spec: 2-5 seconds. If MC switches to reCAPTCHA this
needs to be extended.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

log = logging.getLogger(__name__)

_BASE = "https://2captcha.com"


class CaptchaError(RuntimeError):
    pass


class TwoCaptchaClient:
    def __init__(self, api_key: str, timeout: float = 60.0):
        if not api_key:
            raise ValueError("TwoCaptcha requires an API key")
        self.api_key = api_key
        self.timeout = timeout

    def solve_image(self, image_bytes: bytes) -> str:
        """Submit a PNG/JPEG captcha and poll until solved."""
        b64 = base64.b64encode(image_bytes).decode("ascii")

        with httpx.Client(timeout=self.timeout) as client:
            submit = client.post(
                f"{_BASE}/in.php",
                data={"key": self.api_key, "method": "base64", "body": b64, "json": 1},
            ).json()
            if submit.get("status") != 1:
                raise CaptchaError(f"submit failed: {submit!r}")
            captcha_id = submit["request"]

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                time.sleep(3)
                poll = client.get(
                    f"{_BASE}/res.php",
                    params={"key": self.api_key, "action": "get", "id": captcha_id, "json": 1},
                ).json()
                if poll.get("status") == 1:
                    return poll["request"]
                if poll.get("request") != "CAPCHA_NOT_READY":
                    raise CaptchaError(f"poll failed: {poll!r}")

        raise CaptchaError("2Captcha timed out")
