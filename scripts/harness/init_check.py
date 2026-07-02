#!/usr/bin/env python
"""Cross-platform project initialization and verification checks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if name == "node":
        bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
        if bundled.exists():
            return str(bundled)
    return None


def project_python() -> str:
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


class Reporter:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def ok(self, message: str) -> None:
        print(f"  [OK] {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"  [WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"  [FAIL] {message}")


def run_command(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def stage_python(reporter: Reporter) -> str:
    print("[1/6] Python version")
    version = sys.version_info
    executable = Path(sys.executable)
    if version.major == 3 and version.minor >= 10:
        reporter.ok(f"Python {version.major}.{version.minor}.{version.micro} ({executable})")
    else:
        reporter.fail(f"Python {version.major}.{version.minor}.{version.micro}; need >= 3.10")
    return sys.executable


def stage_dependencies(reporter: Reporter, check_only: bool) -> None:
    print("[2/6] Dependencies")
    uv = find_executable("uv")
    venv = ROOT / ".venv"
    lockfile = ROOT / "uv.lock"
    if uv:
        if lockfile.exists() and venv.exists():
            reporter.ok("uv, uv.lock, and .venv detected")
            return
        if check_only:
            reporter.warn("uv sync is needed, but check-only mode will not modify the environment")
            return
        result = run_command(["uv", "sync", "--quiet"], timeout=240)
        if result.returncode == 0:
            reporter.ok("uv sync complete")
        else:
            reporter.fail("uv sync failed")
            print(result.stdout)
        return

    if venv.exists():
        reporter.warn("uv not found, but .venv exists")
    else:
        reporter.fail("uv not found and .venv is missing")


def stage_database(reporter: Reporter, check_only: bool) -> None:
    print("[3/6] Database")
    db_path = ROOT / "data" / "orders.db"
    if db_path.exists():
        reporter.ok("data/orders.db exists")
    elif check_only:
        reporter.warn("data/orders.db missing; run a full init to seed it")
    else:
        result = run_command([project_python(), "src/seed_data.py"], timeout=120)
        if result.returncode == 0:
            reporter.ok("seed_data.py complete")
        else:
            reporter.fail("seed_data.py failed")
            print(result.stdout)

    alembic = [project_python(), "-m", "alembic", "upgrade", "head"]
    if check_only:
        reporter.ok("migration command available for full init")
        return
    result = run_command(alembic, timeout=120)
    if result.returncode == 0:
        reporter.ok("alembic upgrade head")
    else:
        reporter.warn("alembic upgrade did not complete; inspect output if schema changed")
        print(result.stdout)


def stage_rest_api(reporter: Reporter) -> None:
    print("[4/6] REST API health")
    try:
        with urlopen("http://localhost:8000/api/health", timeout=3) as response:
            if response.status == 200:
                reporter.ok("REST API localhost:8000 responded 200")
            else:
                reporter.warn(f"REST API returned HTTP {response.status}")
    except URLError:
        reporter.warn("REST API localhost:8000 is not reachable; start with: uvicorn order_api:app --reload --port 8000")
    except Exception as exc:
        reporter.warn(f"REST API health check skipped: {exc}")


def stage_mcp(reporter: Reporter) -> None:
    print("[5/6] MCP server smoke")
    config_path = ROOT / ".claude" / "mcp.json"
    if not config_path.exists():
        reporter.fail("Missing .claude/mcp.json")
        return

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        reporter.fail(f"Cannot parse .claude/mcp.json: {exc}")
        return

    servers = config.get("mcpServers", {})
    expected = {"customer-service", "order-server"}
    missing = sorted(expected - set(servers))
    if missing:
        reporter.fail(f"Missing MCP servers: {', '.join(missing)}")
    else:
        reporter.ok("MCP config includes customer-service and order-server")

    for server_name, rel_path in {
        "customer-service": "src/server_customer.py",
        "order-server": "src/server.py",
    }.items():
        if (ROOT / rel_path).exists():
            reporter.ok(f"{server_name} entry file present: {rel_path}")
        else:
            reporter.fail(f"{server_name} entry file missing: {rel_path}")

    order_env = servers.get("order-server", {}).get("env", {})
    if "IDENTITY_VERIFICATION" in order_env:
        reporter.fail("order-server must not carry IDENTITY_VERIFICATION; protected customer data must go through the orchestrator")
    else:
        reporter.ok("order-server has no identity verification token")

    node = find_executable("node")
    if node:
        reporter.ok(f"Node.js available for harness validators: {node}")
    else:
        reporter.warn("Node.js not found; harness validators cannot run")


def stage_tests(reporter: Reporter, skip_tests: bool) -> None:
    print("[6/6] Test suite")
    if skip_tests:
        reporter.warn("Tests skipped (--skip-tests)")
        return
    if not (ROOT / "tests").exists():
        reporter.warn("No tests/ directory found")
        return
    command = [project_python(), "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider", "-n", "auto", "--dist=loadscope"]
    result = run_command(command, timeout=240)
    print(result.stdout)
    if result.returncode == 0:
        reporter.ok("All tests passed")
    else:
        reporter.fail("Some tests failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Customer Service Agent 2.0 workspace health.")
    parser.add_argument("--check-only", action="store_true", help="Verify without seeding, syncing, or migrating.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest.")
    args = parser.parse_args()

    os.chdir(ROOT)
    reporter = Reporter()
    print("")
    print("Customer Service Agent 2.0 - Init & Verification")
    print("")
    stage_python(reporter)
    stage_dependencies(reporter, args.check_only)
    stage_database(reporter, args.check_only)
    stage_rest_api(reporter)
    stage_mcp(reporter)
    stage_tests(reporter, args.skip_tests)
    print("")
    if reporter.failures == 0 and reporter.warnings == 0:
        print("All checks passed; project is ready.")
    elif reporter.failures == 0:
        print(f"{reporter.warnings} warning(s); project is usable but needs attention.")
    else:
        print(f"{reporter.failures} failure(s), {reporter.warnings} warning(s); fix failures before working.")
    return reporter.failures


if __name__ == "__main__":
    raise SystemExit(main())
