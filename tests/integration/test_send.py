"""Integration test: ProgressServerTransport against a real Speckle server.

Sends a small object tree and asserts it round-trips, and that our progress
counter advanced. Requires env vars:

    SPECKLE_TOKEN     an API token with write scope
    SPECKLE_PROJECT   a project (stream) id to write objects into
    SPECKLE_SERVER    server url (default https://speckle.tgcs.com.au)
"""

import importlib.util
import os
import pathlib

import pytest

pytestmark = pytest.mark.integration

TOKEN = os.environ.get("SPECKLE_TOKEN")
PROJECT = os.environ.get("SPECKLE_PROJECT")
SERVER = os.environ.get("SPECKLE_SERVER", "https://speckle.tgcs.com.au")

skip_no_server = pytest.mark.skipif(
    not (TOKEN and PROJECT),
    reason="set SPECKLE_TOKEN and SPECKLE_PROJECT to run integration tests",
)

_MOD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "bpy_speckle"
    / "connector"
    / "operations"
    / "progress_transport.py"
)
_spec = importlib.util.spec_from_file_location("progress_transport", _MOD_PATH)
_pt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pt)
ProgressServerTransport = _pt.ProgressServerTransport


class _FakeWM:
    def __init__(self):
        self.updates = []

    def progress_update(self, pct):
        self.updates.append(pct)


@skip_no_server
def test_send_round_trips_and_reports_progress():
    from specklepy.core.api import operations
    from specklepy.core.api.credentials import Account
    from specklepy.objects.base import Base

    account = Account.from_token(TOKEN, SERVER)

    root = Base()
    root["name"] = "pytest-integration-root"
    root["@children"] = [Base() for _ in range(3)]  # detached -> separate objects

    wm = _FakeWM()
    transport = ProgressServerTransport(
        stream_id=PROJECT, account=account, wm=wm, total=4
    )

    root_id = operations.send(root, [transport])

    assert isinstance(root_id, str) and root_id
    # root + 3 detached children were serialized through our transport
    assert transport.progress_count >= 4
    assert wm.updates, "progress_update should have been called during send"

    # round-trip: the objects really landed on the server
    receive_transport = ProgressServerTransport(stream_id=PROJECT, account=account)
    received = operations.receive(root_id, receive_transport)
    assert received["name"] == "pytest-integration-root"
    assert len(received["@children"]) == 3
