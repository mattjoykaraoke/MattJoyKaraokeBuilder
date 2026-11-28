from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_localappdata(tmp_path, monkeypatch) -> None:
    """
    Redirect LOCALAPPDATA to a per-test temp directory so pytest runs never pollute
    the user's real %LOCALAPPDATA%\\MattJoyKaraokeBuilder saved_projects.json or autosaves.
    """
    fake_local = tmp_path / "_pytest_localappdata"
    fake_local.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
