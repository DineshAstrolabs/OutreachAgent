"""Data source adapters.

Each source implements an interface in `base.py`. Pipeline composes them via
dependency injection, so tests and dry-runs can use fixture-backed stubs
without touching the network.
"""
