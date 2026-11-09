"""Shared test setup."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_editor_error_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Editor logs go to the test's own folder, never the real logs/ (errors, activity).

    Tests that open a profile without a release fail on purpose; before this fixture
    each test run added those failures to the log Kyle reads on the Settings page.
    """
    monkeypatch.setenv("BBS_UI_ERROR_LOG", str(tmp_path / "logs" / "ui_errors.log"))
    monkeypatch.setenv("BBS_UI_ACTIVITY_LOG", str(tmp_path / "logs" / "ui_activity.log"))
