"""Regression tests for harness and architecture risk controls."""

from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_fast_agent_workflow_rules_are_documented(self):
        required = (
            "Fast Agent Workflow",
            "scoop\\shims",
            "Get-ChildItem -Recurse",
            ".venv",
            ".git",
            ".pytest_cache",
            "__pycache__",
            "UTF-8 file input",
            "Unicode escapes",
            "same Python process",
        )
        for rel_path in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / rel_path).read_text(encoding="utf-8")
            for needle in required:
                self.assertIn(needle, text, f"{needle!r} missing from {rel_path}")

    def test_customer_server_avoids_cold_start_work_at_import_time(self):
        source = (ROOT / "src" / "server_customer.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        top_level_imports = [alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names]
        self.assertNotIn("analytics_service", top_level_imports)

        top_level_names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        top_level_names.add(target.id)
        self.assertNotIn("FAQ", top_level_names)
        self.assertNotIn("FAQ_RETRIEVER", top_level_names)
        self.assertNotIn("FAQ_CATEGORIES", top_level_names)

        runtime_source = (ROOT / "src" / "orchestrator_runtime.py").read_text(encoding="utf-8")
        runtime_tree = ast.parse(runtime_source)
        runtime_imports = [
            alias.name for node in runtime_tree.body if isinstance(node, ast.Import) for alias in node.names
        ]
        self.assertNotIn("analytics_service", runtime_imports)

    def test_local_customer_tools_lazy_loads_faq_retriever(self):
        import orchestrator_runtime

        retriever = Mock()
        retriever.search.return_value = [{"id": "faq-001"}]
        with patch.object(orchestrator_runtime, "get_faq_retriever", return_value=retriever) as factory:
            tools = orchestrator_runtime.LocalCustomerServiceTools()
            factory.assert_not_called()

            self.assertEqual(tools.search_faq("return policy"), [{"id": "faq-001"}])
            factory.assert_called_once()
            retriever.search.assert_called_once_with("return policy", limit=3)

    def test_mcp_config_does_not_embed_static_credentials(self):
        config = json.loads((ROOT / ".claude" / "mcp.json").read_text(encoding="utf-8"))
        for name, server in config["mcpServers"].items():
            env = server.get("env", {})
            self.assertNotIn("IDENTITY_VERIFICATION", env, name)
            self.assertNotIn("API_KEY", env, name)
            self.assertNotIn("AUTH_DEV_SECRET", env, name)
            self.assertNotIn("OTP_PROVIDER", env, name)

        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for needle in (
            "DATABASE_URL",
            "OIDC_ISSUER",
            "OIDC_JWKS_URL",
            "OTP_PROVIDER",
            "REPORT_TIMEZONE",
            "FAQ_RAG_BACKEND",
        ):
            self.assertIn(needle, example)

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
