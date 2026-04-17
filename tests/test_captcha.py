"""Captcha solver selection tests. Network/OCR calls are not exercised — we
only validate the factory's provider-routing and credential enforcement."""

import pytest

from outreach_agent.captcha import (
    AnthropicVisionSolver,
    CaptchaError,
    TwoCaptchaClient,
    build_captcha_solver,
)


def test_twocaptcha_requires_key():
    with pytest.raises(CaptchaError):
        build_captcha_solver("twocaptcha", twocaptcha_api_key=None)


def test_twocaptcha_returns_client():
    solver = build_captcha_solver("twocaptcha", twocaptcha_api_key="abc")
    assert isinstance(solver, TwoCaptchaClient)
    assert solver.name == "twocaptcha"


def test_anthropic_requires_key():
    with pytest.raises(CaptchaError):
        build_captcha_solver("anthropic", anthropic_api_key=None)


def test_anthropic_returns_solver():
    solver = build_captcha_solver("anthropic", anthropic_api_key="sk-ant-x")
    assert isinstance(solver, AnthropicVisionSolver)
    assert solver.name == "anthropic"


def test_unknown_provider_raises():
    with pytest.raises(CaptchaError, match="unknown CAPTCHA_PROVIDER"):
        build_captcha_solver("magic-solver")


def test_default_is_twocaptcha():
    """Empty provider string defaults to twocaptcha (and will then demand a key)."""
    with pytest.raises(CaptchaError, match="TWOCAPTCHA_API_KEY"):
        build_captcha_solver("")


def test_tesseract_requires_pytesseract(monkeypatch):
    """If pytesseract isn't importable, we return a clear error — not a
    mysterious ImportError deep inside the solve_image call."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("pytesseract", "PIL"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(CaptchaError, match="pytesseract \\+ Pillow"):
        build_captcha_solver("tesseract")
