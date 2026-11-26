"""Test defaults shared by HTTP and browser servers, including Windows suite runs."""
import os

os.environ.setdefault("PHARM_TEST_OFFLINE_UPDATE", "1")
