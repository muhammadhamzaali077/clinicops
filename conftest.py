"""Present so that a bare `pytest` puts the repo root on sys.path.

Without this file, pytest's default import mode inserts only `tests/`, and the test
imports (`from src.api.main import app`, `from api.index import app`) fail with
ModuleNotFoundError. `python -m pytest` happens to work because it adds the current
directory itself — this file makes the plain `pytest -q` in CI behave the same way.

Deliberately empty otherwise: no fixtures belong here yet.
"""
