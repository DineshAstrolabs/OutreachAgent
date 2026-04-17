"""Lets users run `python -m outreach_agent ...` as a fallback when the
console-script shim is broken (e.g. after a partial editable install)."""

from .cli import main

if __name__ == "__main__":
    main()
