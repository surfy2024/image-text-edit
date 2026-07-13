import pytest


@pytest.fixture(autouse=True)
def isolated_edit_chart_text_state(tmp_path, monkeypatch):
    """Keep locks and install secrets in a per-test application state directory."""
    state = tmp_path / "app-state"
    monkeypatch.setenv("EDIT_CHART_TEXT_STATE_DIR", str(state))
    return state
