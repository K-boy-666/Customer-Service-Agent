#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────
// create-harness.mjs — Scaffold a minimal harness for a project
// ─────────────────────────────────────────────────────────────
// Usage: node create-harness.mjs --target /path/to/project [options]
//
// Options:
//   --agent-file CLAUDE.md     Instruction file name (default: CLAUDE.md)
//   --package-manager npm|pnpm|yarn|bun  (auto-detected if omitted)
//   --commands "cmd1,cmd2"     Custom verification commands
//   --force                    Overwrite existing files
//   --dry-run                  Show what would be created, don't write
// ─────────────────────────────────────────────────────────────

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { execSync } from 'node:child_process';

const args = parseArgs(process.argv.slice(2));

const TARGET = resolve(args.target || process.cwd());
const AGENT_FILE = args['agent-file'] || 'CLAUDE.md';
const FORCE = args.force || false;
const DRY_RUN = args['dry-run'] || false;
const PKG_MGR = args['package-manager'] || detectPackageManager(TARGET);
const CUSTOM_CMDS = args.commands ? args.commands.split(',').map(s => s.trim()) : [];

console.log(`\n🔧 Harness Creator — Scaffolding harness for ${TARGET}\n`);

// Collect project info
const info = collectInfo(TARGET);
printInfo(info);

if (DRY_RUN) {
  console.log('📋 DRY RUN — would create these files:\n');
  listArtifacts(TARGET, AGENT_FILE);
  process.exit(0);
}

// Check existing files
const conflicts = findConflicts(TARGET, AGENT_FILE);
if (conflicts.length > 0 && !FORCE) {
  console.error('❌ These files already exist. Use --force to overwrite:\n');
  conflicts.forEach(f => console.error(`   ${f}`));
  console.error('');
  process.exit(1);
}

// Create artifacts
const created = [];
try {
  created.push(...createInstructionFile(TARGET, AGENT_FILE, info));
  created.push(...createFeatureList(TARGET, info));
  created.push(...createProgressMd(TARGET, info));
  created.push(...createInitSh(TARGET, info, PKG_MGR, CUSTOM_CMDS));
  created.push(...createSessionHandoff(TARGET, info));
} catch (err) {
  console.error(`❌ Failed: ${err.message}`);
  process.exit(1);
}

console.log('✅ Harness scaffolded successfully!\n');
created.forEach(f => console.log(`   📄 ${f}`));
console.log(`\nNext: replace placeholder entries in feature_list.json with your project's real features.\n`);

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        result[key] = next;
        i++;
      } else {
        result[key] = true;
      }
    }
  }
  return result;
}

function detectPackageManager(dir) {
  const checks = [
    ['bun.lockb', 'bun'],
    ['pnpm-lock.yaml', 'pnpm'],
    ['yarn.lock', 'yarn'],
    ['package-lock.json', 'npm'],
  ];
  for (const [file, mgr] of checks) {
    if (existsSync(join(dir, file))) return mgr;
  }
  // Check for pyproject.toml / uv.lock for Python projects
  if (existsSync(join(dir, 'uv.lock'))) return 'uv';
  if (existsSync(join(dir, 'pyproject.toml'))) return 'uv';
  return 'npm'; // fallback
}

function collectInfo(dir) {
  const info = {
    name: dir.split(/[/\\]/).pop(),
    hasGit: existsSync(join(dir, '.git')),
    hasClaudeMd: existsSync(join(dir, 'CLAUDE.md')),
    hasAgentsMd: existsSync(join(dir, 'AGENTS.md')),
    hasTests: existsSync(join(dir, 'tests')) || existsSync(join(dir, 'test')),
    hasSrc: existsSync(join(dir, 'src')),
    hasDocs: existsSync(join(dir, 'docs')),
    hasPyProject: existsSync(join(dir, 'pyproject.toml')),
    hasPackageJson: existsSync(join(dir, 'package.json')),
  };

  if (info.hasGit) {
    try {
      info.gitRemote = execSync('git remote get-url origin', { cwd: dir, encoding: 'utf8' }).trim();
    } catch { info.gitRemote = null; }
    try {
      info.branch = execSync('git branch --show-current', { cwd: dir, encoding: 'utf8' }).trim();
    } catch { info.branch = 'main'; }
  }

  const stack = [];
  if (info.hasPyProject) stack.push('Python');
  if (info.hasPackageJson) stack.push('Node.js');
  info.stack = stack.length ? stack.join(' + ') : 'unknown';

  return info;
}

function printInfo(info) {
  console.log(`   Project: ${info.name}`);
  console.log(`   Stack: ${info.stack}`);
  console.log(`   Git: ${info.hasGit ? `✓ (${info.branch})` : '✗'}`);
  console.log(`   Tests: ${info.hasTests ? '✓' : '✗'}`);
  console.log(`   Package mgr: ${PKG_MGR}\n`);
}

function listArtifacts(target, agentFile) {
  const artifacts = [
    agentFile,
    'feature_list.json',
    'progress.md',
    'init.sh',
    'session-handoff.md',
  ];
  artifacts.forEach(f => console.log(`   ${join(target, f)}`));
}

function findConflicts(target, agentFile) {
  const artifacts = [
    agentFile,
    'feature_list.json',
    'progress.md',
    'init.sh',
    'session-handoff.md',
  ];
  return artifacts.filter(f => existsSync(join(target, f)));
}

function write(target, filename, content, overwrite) {
  const path = join(target, filename);
  if (existsSync(path) && !overwrite) return null;
  writeFileSync(path, content, 'utf8');
  return path;
}

function createInstructionFile(target, agentFile, info) {
  const content = info.hasClaudeMd || info.hasAgentsMd
    ? null // Don't overwrite existing instruction files unless --force
    : `# ${info.name}

## Quick Start

\`\`\`bash
bash init.sh --check-only
\`\`\`

## Working Rules

1. Run \`bash init.sh --check-only\` at session start.
2. Read \`progress.md\` for active feature and blockers.
3. Check \`feature_list.json\` for dependencies before starting work.
4. Append to \`session-handoff.md\` before ending a session.

## Harness

- **Features**: \`feature_list.json\`
- **Progress**: \`progress.md\`
- **Verification**: \`bash init.sh\`
- **Handoff**: \`session-handoff.md\`
`;

  if (!content) return [];
  const path = write(target, agentFile, content, FORCE);
  return path ? [path] : [];
}

function createFeatureList(target, info) {
  const content = JSON.stringify({
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "version": "1.0",
    "features": {
      "example-feature-1": {
        "title": "Example feature — replace me",
        "status": "planned",
        "description": "Describe what this feature does.",
        "depends_on": [],
        "done_criteria": [
          "Criterion 1 — what must be true for this feature to be done",
          "Criterion 2"
        ],
        "verified_by": "tests/test_example.py"
      }
    },
    "planned": [],
    "completed_order": []
  }, null, 2) + '\n';

  const path = write(target, 'feature_list.json', content, FORCE);
  return path ? [path] : [];
}

function createProgressMd(target, info) {
  const content = `# Progress — ${info.name}

> Last updated: ${new Date().toISOString().split('T')[0]}
> Active branch: \`${info.branch || 'main'}\`

## Current feature

None active. See \`feature_list.json\` for the full inventory.

## Recent completions

| Date | Feature | Commit | Evidence |
|------|---------|--------|----------|
| — | — | — | — |

## Active blockers

_None._

## Verification state

\`\`\`
$ bash init.sh
... (run init.sh to populate)
\`\`\`

## Notes

- Replace this template content with your project's real progress.
`;

  const path = write(target, 'progress.md', content, FORCE);
  return path ? [path] : [];
}

function createInitSh(target, info, pkgMgr, customCmds) {
  const pkgInstall = pkgMgr === 'uv'
    ? 'uv sync --quiet'
    : pkgMgr === 'bun' ? 'bun install --silent'
    : pkgMgr === 'pnpm' ? 'pnpm install --silent'
    : pkgMgr === 'yarn' ? 'yarn install --silent'
    : 'npm install --silent';

  const testCmd = info.hasPyProject
    ? 'uv run pytest tests/ -q'
    : info.hasPackageJson
    ? `${pkgMgr} test`
    : 'echo "No tests configured — add verification commands"';

  const extraChecks = customCmds.map(cmd => `echo "[Extra] ${cmd}" && ${cmd} && echo "  ✔ ${cmd}"`).join('\n');

  const content = `#!/usr/bin/env bash
set -euo pipefail
IFS=$'\\n\\t'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=false
for arg in "$@"; do case "$arg" in --check-only) CHECK_ONLY=true ;; esac; done

GREEN='\\033[0;32m' RED='\\033[0;31m' YELLOW='\\033[1;33m' NC='\\033[0m'
PASS="${GREEN}✔${NC}" FAIL="${RED}✘${NC}" WARN="${YELLOW}⚠${NC}"
failures=0

ok()   { echo -e "  $PASS $1"; }
fail() { echo -e "  $FAIL $1"; failures=$((failures + 1)); }

echo "══════════════════════════════════════"
echo "  ${info.name} — Verification"
echo "══════════════════════════════════════"

echo "[1] Dependencies"
if [ "$CHECK_ONLY" = true ]; then
  ok "check-only mode, skipping install"
else
  ${pkgInstall} && ok "dependencies installed" || fail "install failed"
fi

echo "[2] Tests"
${testCmd} && ok "tests passed" || fail "tests failed"
${extraChecks}

echo ""
if [ "$failures" -eq 0 ]; then
  echo -e "  ${GREEN}Ready${NC}"
else
  echo -e "  ${RED}$failures failure(s)${NC}"
fi
exit $failures
`;

  const path = write(target, 'init.sh', content, FORCE);
  if (path) {
    try { execSync(`chmod +x "${path}"`, { stdio: 'ignore' }); } catch {}
  }
  return path ? [path] : [];
}

function createSessionHandoff(target, info) {
  const content = `# Session Handoff — ${info.name}

> **Purpose**: Make the next session restartable without replaying the entire conversation.
> **When to update**: At the end of every session where work was done.
> **When to read**: At the start of every session — check the most recent block.

---

## Session: ${new Date().toISOString().split('T')[0]} (harness bootstrapping)

**Branch**: \`${info.branch || 'main'}\`
**Active feature**: Harness scaffold
**Outcome**: Completed. Harness files created by harness-creator.

**What was done**:
- Scaffolded AGENTS.md/CLAUDE.md, feature_list.json, progress.md, init.sh, session-handoff.md

**Files touched**:
- feature_list.json — created
- progress.md — created
- init.sh — created
- session-handoff.md — created

**State snapshot**:
- Replace with current feature state

**Open risks / follow-ups**:
- Replace placeholder features in feature_list.json
- Run init.sh to verify the environment

---

## Session: (template — copy for next session)

**Branch**: \`${info.branch || 'main'}\`
**Active feature**:
**Outcome**:

**What was done**:
-

**Files touched this session**:
-

**State snapshot**:
-

**Key decisions made**:
-

**Open risks / follow-ups**:
-
`;

  const path = write(target, 'session-handoff.md', content, FORCE);
  return path ? [path] : [];
}
