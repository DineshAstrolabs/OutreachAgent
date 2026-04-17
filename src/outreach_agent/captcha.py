"""Pluggable image-captcha solvers for the MC CR lookup.

MC's captcha is a short alphanumeric image. We support three providers, in
descending order of cost and accuracy:

  twocaptcha  — paid human-solver service, ~2-5s per solve, ~95%+ accuracy.
  anthropic   — Claude vision via the existing Anthropic API key. No extra
                system dependency. High accuracy on short alphanumeric
                captchas. Reuses ANTHROPIC_API_KEY so no new cost center.
  tesseract   — pure open-source OCR (Tesseract via pytesseract). Requires
                the `tesseract` system binary. 50-80% accuracy on simple
                captchas; retry loop helps. No API keys at all.

Select with CAPTCHA_PROVIDER env var; default is twocaptcha.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class CaptchaError(RuntimeError):
    pass


class CaptchaSolver(Protocol):
    """Any solver: bytes in, decoded text out."""

    name: str

    def solve_image(self, image_bytes: bytes) -> str: ...


# ---------------------------------------------------------------------------
# 2Captcha — paid service, original implementation.
# ---------------------------------------------------------------------------


_TWOCAPTCHA_BASE = "https://2captcha.com"


class TwoCaptchaClient:
    """Paid 2Captcha human-solver API."""

    name = "twocaptcha"

    def __init__(self, api_key: str, timeout: float = 60.0):
        if not api_key:
            raise ValueError("TwoCaptcha requires an API key")
        self.api_key = api_key
        self.timeout = timeout

    def solve_image(self, image_bytes: bytes) -> str:
        log.info("[2captcha] submitting image (%d bytes)", len(image_bytes))
        b64 = base64.b64encode(image_bytes).decode("ascii")

        with httpx.Client(timeout=self.timeout) as client:
            submit = client.post(
                f"{_TWOCAPTCHA_BASE}/in.php",
                data={"key": self.api_key, "method": "base64", "body": b64, "json": 1},
            ).json()
            if submit.get("status") != 1:
                raise CaptchaError(f"submit failed: {submit!r}")
            captcha_id = submit["request"]
            log.info("[2captcha] submitted, id=%s, polling…", captcha_id)

            deadline = time.time() + self.timeout
            polls = 0
            while time.time() < deadline:
                time.sleep(3)
                polls += 1
                poll = client.get(
                    f"{_TWOCAPTCHA_BASE}/res.php",
                    params={
                        "key": self.api_key,
                        "action": "get",
                        "id": captcha_id,
                        "json": 1,
                    },
                ).json()
                if poll.get("status") == 1:
                    log.info("[2captcha] solved after %d polls", polls)
                    return poll["request"]
                if poll.get("request") != "CAPCHA_NOT_READY":
                    raise CaptchaError(f"poll failed: {poll!r}")

        raise CaptchaError("2Captcha timed out")


# ---------------------------------------------------------------------------
# Tesseract — fully open-source OCR. Needs the `tesseract` system binary.
# ---------------------------------------------------------------------------


# Common MC-style captchas are short alphanumeric with no case variation.
# Constraining the allowlist sharply improves Tesseract's accuracy.
_TESS_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_TESS_CONFIG = f"--psm 7 -c tessedit_char_whitelist={_TESS_ALLOWLIST}"


class TesseractSolver:
    """Open-source OCR. Requires system `tesseract` binary + pytesseract.

    Install:
        macOS:   brew install tesseract && pip install pytesseract pillow
        Debian:  apt-get install tesseract-ocr && pip install pytesseract pillow
    """

    name = "tesseract"

    def __init__(self):
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise CaptchaError(
                "tesseract provider requires pytesseract + Pillow. "
                "Install with: pip install pytesseract pillow, and the "
                "tesseract system binary (brew install tesseract / "
                "apt-get install tesseract-ocr)."
            ) from exc

    def solve_image(self, image_bytes: bytes) -> str:
        log.info("[tesseract] solving image (%d bytes)", len(image_bytes))
        import pytesseract
        from PIL import Image, ImageFilter, ImageOps

        try:
            img = Image.open(io.BytesIO(image_bytes))
        except Exception as exc:
            raise CaptchaError(f"failed to decode captcha image: {exc}") from exc

        log.info("[tesseract] preprocess: grayscale → autocontrast → threshold(140) → median(3)")
        # Preprocess: grayscale → autocontrast → threshold → denoise. These
        # transforms measurably improve Tesseract accuracy on typical MC-style
        # short-alphanumeric captchas.
        img = img.convert("L")
        img = ImageOps.autocontrast(img)
        img = img.point(lambda p: 255 if p > 140 else 0)
        img = img.filter(ImageFilter.MedianFilter(size=3))

        text = pytesseract.image_to_string(img, config=_TESS_CONFIG)
        cleaned = "".join(c for c in text.strip() if c.isalnum())
        log.info("[tesseract] raw=%r cleaned=%r", text.strip(), cleaned)
        if not cleaned:
            raise CaptchaError("tesseract returned empty OCR result")
        return cleaned


# ---------------------------------------------------------------------------
# Anthropic vision — reuses ANTHROPIC_API_KEY. No extra system deps.
# ---------------------------------------------------------------------------


_VISION_PROMPT = (
    "Read the alphanumeric text shown in this captcha image. Output ONLY the "
    "characters you see, with no spaces, punctuation, or explanation. Do not "
    "add quotation marks. Preserve case exactly as rendered."
)


class AnthropicVisionSolver:
    """Solve an image captcha with Claude vision.

    Uses the model's vision capability — pass the image as a base64 block in
    the user turn. No thinking needed (this is a trivial visual read). Best
    suited for the user's own captcha on a public registry; not a general-
    purpose captcha-bypass tool.
    """

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-4-7"):
        if not api_key:
            raise ValueError("AnthropicVisionSolver requires an API key")
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def solve_image(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        media_type = _sniff_media_type(image_bytes)
        log.info(
            "[anthropic-vision] POST /v1/messages model=%s media_type=%s bytes=%d",
            self.model, media_type, len(image_bytes),
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=32,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
        )

        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        raw = "".join(text_parts).strip()
        cleaned = "".join(c for c in raw if c.isalnum())
        log.info("[anthropic-vision] raw=%r cleaned=%r", raw, cleaned)
        if not cleaned:
            raise CaptchaError("anthropic vision returned empty result")
        return cleaned


def _sniff_media_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # Default — Claude will return a clear error if actually malformed.
    return "image/png"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def build_captcha_solver(
    provider: str,
    twocaptcha_api_key: str | None = None,
    anthropic_api_key: str | None = None,
) -> CaptchaSolver:
    """Pick a solver by provider name.

    Raises a clear error if the chosen provider is missing its credentials
    or system dependencies. Defaults to twocaptcha on empty input.
    """
    provider = (provider or "twocaptcha").strip().lower()
    log.info("[captcha] building solver for provider=%r", provider)

    if provider == "twocaptcha":
        if not twocaptcha_api_key:
            raise CaptchaError(
                "CAPTCHA_PROVIDER=twocaptcha requires TWOCAPTCHA_API_KEY"
            )
        return TwoCaptchaClient(twocaptcha_api_key)

    if provider == "tesseract":
        return TesseractSolver()

    if provider == "anthropic":
        if not anthropic_api_key:
            raise CaptchaError(
                "CAPTCHA_PROVIDER=anthropic requires ANTHROPIC_API_KEY"
            )
        return AnthropicVisionSolver(anthropic_api_key)

    raise CaptchaError(
        f"unknown CAPTCHA_PROVIDER={provider!r}; "
        f"valid: twocaptcha, tesseract, anthropic"
    )
