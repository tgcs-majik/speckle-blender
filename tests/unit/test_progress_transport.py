"""Unit tests for ProgressServerTransport.

The transport module imports only ``specklepy`` (no ``bpy``), so it is loaded
directly from its file. The network-touching ``ServerTransport`` base is patched
out, so these tests need no Blender and no server.
"""

import importlib.util
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_MOD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "bpy_speckle"
    / "connector"
    / "operations"
    / "progress_transport.py"
)
_spec = importlib.util.spec_from_file_location("progress_transport", _MOD_PATH)
progress_transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(progress_transport)

ProgressServerTransport = progress_transport.ProgressServerTransport
_Base = ProgressServerTransport.__mro__[1]  # specklepy ServerTransport


@pytest.fixture
def make_transport():
    """Build a transport with the network-touching base __init__ neutralised."""

    def _make(wm=None, total=0):
        with patch.object(_Base, "__init__", return_value=None):
            return ProgressServerTransport(wm=wm, total=total)

    return _make


def test_counter_increments_and_base_still_called(make_transport):
    t = make_transport(total=3)
    with patch.object(_Base, "save_object", return_value=None) as base_save:
        for i in range(3):
            t.save_object(f"id{i}", "{}")
    assert t.progress_count == 3
    # the real save must still run — progress must not replace it
    assert base_save.call_count == 3


def test_percent_reported_and_clamped_to_99(make_transport):
    wm = MagicMock()
    t = make_transport(wm=wm, total=4)
    with patch.object(_Base, "save_object", return_value=None):
        for i in range(4):
            t.save_object(f"id{i}", "{}")
    reported = [c.args[0] for c in wm.progress_update.call_args_list]
    assert reported == [25, 50, 75, 99]  # 100% clamped to 99 until finalized
    assert all(0 <= p <= 99 for p in reported)


def test_zero_total_does_not_divide_by_zero(make_transport):
    wm = MagicMock()
    t = make_transport(wm=wm, total=0)  # internally clamped to 1
    with patch.object(_Base, "save_object", return_value=None):
        t.save_object("x", "{}")
    assert t.progress_count == 1


def test_progress_error_never_breaks_a_save(make_transport):
    wm = MagicMock()
    wm.progress_update.side_effect = RuntimeError("no window in background")
    t = make_transport(wm=wm, total=2)
    with patch.object(_Base, "save_object", return_value=None):
        t.save_object("a", "{}")  # must not raise
    assert t.progress_count == 1


def test_no_window_manager_is_fine(make_transport):
    t = make_transport(wm=None, total=2)
    with patch.object(_Base, "save_object", return_value=None):
        t.save_object("a", "{}")
    assert t.progress_count == 1


def test_console_line_every_1000(make_transport, capsys):
    t = make_transport(total=2500)
    with patch.object(_Base, "save_object", return_value=None):
        for i in range(2500):
            t.save_object(f"id{i}", "{}")
    out = capsys.readouterr().out
    assert "Uploading objects: 1000/2500" in out
    assert "Uploading objects: 2000/2500" in out
