"""Regression tests for harness and architecture risk controls."""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from security import create_dev_jwt, decode_jwt_token


class HarnessRiskControlsTest(unittest.TestCase):
    def test_cross_platform_init_entrypoints_exist(self):
        self.assertTrue((ROOT / "scripts" / "harness" / "init_check.py").exists())
        self.assertTrue((ROOT / "init.sh").exists())
        self.assertTrue((ROOT / "init.cmd").exists())
        self.assertTrue((ROOT / "init.ps1").exists())

    def test_key_governance_files_are_ascii_without_bom(self):
        for rel_path in (
            "AGENTS.md",
            "CLAUDE.md",
            "init.sh",
            "init.cmd",
            "init.ps1",
            ".claude/settings.json",
            ".claude/mcp.json",
        ):
            raw = (ROOT / rel_path).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), rel_path)
            raw.decode("ascii")

    def test_order_server_mcp_cannot_bypass_orchestrator_verification(self):
        config = json.loads((ROOT / ".claude" / "mcp.json").read_text(encoding="utf-8"))
        order_env = config["mcpServers"]["order-server"].get("env", {})
        self.assertNotIn("IDENTITY_VERIFICATION", order_env)
        self.assertEqual(order_env.get("AUTH_DEV_SECRET"), "customer-service-dev-secret-min-32-bytes")

    def test_order_server_scrubs_identity_verification_env(self):
        previous = os.environ.get("IDENTITY_VERIFICATION")
        os.environ["IDENTITY_VERIFICATION"] = "should-not-be-forwarded"
        try:
            sys.modules.pop("server", None)
            sys.modules.pop("api_client", None)
            server = importlib.import_module("server")
            self.assertEqual(server.api_client.IDENTITY_VERIFICATION, "")
        finally:
            sys.modules.pop("server", None)
            sys.modules.pop("api_client", None)
            if previous is None:
                os.environ.pop("IDENTITY_VERIFICATION", None)
            else:
                os.environ["IDENTITY_VERIFICATION"] = previous

    def test_default_dev_jwt_secret_is_long_enough_for_hs256(self):
        previous = os.environ.pop("AUTH_DEV_SECRET", None)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                token = create_dev_jwt("agent-1", "order_inquiry")
                actor = decode_jwt_token(token)
            self.assertEqual(actor.role, "order_inquiry")
            warning_names = {warning.category.__name__ for warning in caught}
            self.assertNotIn("InsecureKeyLengthWarning", warning_names)
        finally:
            if previous is not None:
                os.environ["AUTH_DEV_SECRET"] = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)