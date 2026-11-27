"""Test defaults shared by HTTP and browser servers, including Windows suite runs."""
import os

os.environ.setdefault("PHARM_TEST_OFFLINE_UPDATE", "1")

# Existing HTTP tests model the cashier's local browser. LAN tests specify their own peer.
from starlette.testclient import TestClient

test_client_defaults = TestClient.__init__.__defaults__
if test_client_defaults and test_client_defaults[-1] == ("testclient", 50000):
    TestClient.__init__.__defaults__ = (*test_client_defaults[:-1], ("127.0.0.1", 50000))
