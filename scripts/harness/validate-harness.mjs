#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────
// validate-harness.mjs — Audit harness subsystems and score them
// ─────────────────────────────────────────────────────────────
// Usage: node validate-harness.mjs [--target /path] [--score] [--json]
// ─────────────────────────────────────────────────────────────

import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const args = parseArgs(process.argv.slice(2));
const TARGET = resolve(args.target || process.cwd());
const SCORE_MODE = args.score || false;
const JSON_OUTPUT = args.json || false;

const DIMENSIONS = {
  instructions: { weight: 25, name: 'Instructions' },
  state: { weight: 25, name: 'State' },
  verification: { weight: 20, name: 'Verification' },
  scope: { weight: 15, name: 'Scope' },
  lifecycle: { weight: 15, name: 'Lifecycle' },
};

const results = {};
const details = {};

// ─────────────── 1. Instructions ───────────────
function auditInstructions() {
  const hasClaude = existsSync(join(TARGET, 'CLAUDE.md'));
  const hasAgents = existsSync(join(TARGET, 'AGENTS.md'));
  let score = 0;
  const findings = [];

  if (hasClaude || hasAgents) {
    score += 40;
    findings.push({ ok: true, msg: 'Instruction file exists' });
    const file = hasClaude ? 'CLAUDE.md' : 'AGENTS.md';
    try {
      const content = readFileSync(join(TARGET, file), 'utf8');
      const len = content.length;
      if (len > 500) {
        score += 20;
        findings.push({ ok: true, msg: `${file} has substantive content (${len} chars)` });
      } else {
        score += 5;
        findings.push({ ok: false, msg: `${file} is minimal (${len} chars) — expand with working rules and conventions` });
      }
      if (content.includes('## ') || content.includes('### ')) {
        score += 15;
        findings.push({ ok: true, msg: `${file} has section structure` });
      } else {
        findings.push({ ok: false, msg: `${file} lacks section headers — add structure` });
      }
      if (/test|verify|init|check/i.test(content)) {
        score += 15;
        findings.push({ ok: true, msg: 'Verification commands referenced in instructions' });
      } else {
        findings.push({ ok: false, msg: 'No verification commands found in instructions' });
      }
      if (content.includes('session-handoff') || content.includes('handoff')) {
        score += 10;
        findings.push({ ok: true, msg: 'Session lifecycle referenced in instructions' });
      } else {
        findings.push({ ok: false, msg: 'No session lifecycle reference in instructions' });
      }
    } catch { /* should not happen */ }
  } else {
    findings.push({ ok: false, msg: 'No instruction file (CLAUDE.md or AGENTS.md)' });
  }

  return { score: Math.min(100, score), findings };
}

// ─────────────── 2. State ───────────────
function auditState() {
  const hasFeatureList = existsSync(join(TARGET, 'feature_list.json'));
  const hasProgress = existsSync(join(TARGET, 'progress.md'));
  let score = 0;
  const findings = [];

  if (hasFeatureList) {
    score += 40;
    findings.push({ ok: true, msg: 'feature_list.json exists' });
    try {
      const raw = readFileSync(join(TARGET, 'feature_list.json'), 'utf8');
      const data = JSON.parse(raw);
      const featureCount = Object.keys(data.features || {}).length;
      const plannedCount = (data.planned || []).length;
      const hasSchema = raw.includes('$schema');
      const hasDoneCriteria = raw.includes('done_criteria');

      if (hasSchema) {
        score += 10;
        findings.push({ ok: true, msg: 'feature_list.json has JSON Schema' });
      } else {
        findings.push({ ok: false, msg: 'feature_list.json missing $schema field' });
      }
      if (hasDoneCriteria) {
        score += 15;
        findings.push({ ok: true, msg: 'Features have done_criteria defined' });
      } else {
        findings.push({ ok: false, msg: 'No done_criteria in feature_list.json' });
      }
      if (featureCount + plannedCount > 0) {
        score += 10;
        findings.push({ ok: true, msg: `${featureCount} features + ${plannedCount} planned` });
      } else {
        findings.push({ ok: false, msg: 'feature_list.json is empty' });
      }
    } catch (e) {
      score -= 20;
      findings.push({ ok: false, msg: `feature_list.json parse error: ${e.message}` });
    }
  } else {
    findings.push({ ok: false, msg: 'feature_list.json missing' });
  }

  if (hasProgress) {
    score += 25;
    findings.push({ ok: true, msg: 'progress.md exists' });
    try {
      const content = readFileSync(join(TARGET, 'progress.md'), 'utf8');
      if (content.length > 200) {
        findings.push({ ok: true, msg: 'progress.md has substantive content' });
      } else {
        findings.push({ ok: false, msg: 'progress.md is minimal — populate with real progress' });
      }
    } catch {}
  } else {
    findings.push({ ok: false, msg: 'progress.md missing' });
  }

  return { score: Math.min(100, Math.max(0, score)), findings };
}

// ─────────────── 3. Verification ───────────────
function auditVerification() {
  const hasInitSh = existsSync(join(TARGET, 'init.sh'));
  const hasInitCmd = existsSync(join(TARGET, 'init.cmd'));
  const hasTests = existsSync(join(TARGET, 'tests'));
  const hasPytest = existsSync(join(TARGET, 'pyproject.toml')) || existsSync(join(TARGET, 'pytest.ini'));
  const hasPackageTest = existsSync(join(TARGET, 'package.json'));
  let score = 0;
  const findings = [];

  if (hasInitSh) {
    score += 35;
    findings.push({ ok: true, msg: 'init.sh exists' });
    try {
      const stat = statSync(join(TARGET, 'init.sh'));
      if ((stat.mode & 0o111) || process.platform === 'win32' || hasInitCmd) {
        findings.push({ ok: true, msg: hasInitCmd ? 'init.cmd provides a Windows verification entry point' : 'init.sh is executable' });
      } else {
        findings.push({ ok: false, msg: 'init.sh is not executable - run chmod +x init.sh' });
      }
    } catch {}
  } else {
    findings.push({ ok: false, msg: 'No init.sh — create a verification entry point' });
  }

  if (hasTests) {
    score += 30;
    const testFiles = readdirSync(join(TARGET, 'tests')).filter(f => f.startsWith('test_'));
    findings.push({ ok: true, msg: `tests/ directory with ${testFiles.length} test files` });
  } else {
    findings.push({ ok: false, msg: 'No tests/ directory' });
  }

  // Check if test runner is configured
  if (hasPytest || hasPackageTest) {
    score += 15;
    findings.push({ ok: true, msg: 'Test runner configured' });
  }

  // Check for lint config
  const hasLint = existsSync(join(TARGET, '.eslintrc.js')) || existsSync(join(TARGET, '.eslintrc.json'))
    || existsSync(join(TARGET, 'eslint.config.mjs')) || existsSync(join(TARGET, 'pyproject.toml'));
  if (hasLint) {
    score += 10;
    findings.push({ ok: true, msg: 'Lint configuration found' });
  }

  // Check if tests can actually run
  if (hasTests && (hasPytest || hasPackageTest)) {
    score += 10;
    findings.push({ ok: true, msg: 'Test runner matches test directory' });
  } else if (hasTests && !hasPytest && !hasPackageTest) {
    findings.push({ ok: false, msg: 'Tests directory exists but no test runner found' });
  }

  return { score: Math.min(100, score), findings };
}

// ─────────────── 4. Scope ───────────────
function auditScope() {
  let score = 0;
  const findings = [];
  const hasFeatureList = existsSync(join(TARGET, 'feature_list.json'));

  if (hasFeatureList) {
    try {
      const data = JSON.parse(readFileSync(join(TARGET, 'feature_list.json'), 'utf8'));
      const features = data.features || {};
      const hasDeps = Object.values(features).some(f => f.depends_on && f.depends_on.length > 0);
      const hasDoneCriteria = Object.values(features).some(f => f.done_criteria && f.done_criteria.length > 0);
      const hasStatuses = Object.values(features).every(f => f.status);

      if (hasDeps) {
        score += 35;
        findings.push({ ok: true, msg: 'Features have dependency declarations' });
      } else {
        score += 10;
        findings.push({ ok: false, msg: 'No feature dependencies declared — add depends_on to prevent overreach' });
      }

      if (hasDoneCriteria) {
        score += 35;
        findings.push({ ok: true, msg: 'Features have done_criteria defined' });
      } else {
        findings.push({ ok: false, msg: 'No done_criteria — agents cannot verify completion' });
      }

      if (hasStatuses) {
        score += 20;
        findings.push({ ok: true, msg: 'All features have explicit status' });
      } else {
        findings.push({ ok: false, msg: 'Some features missing status field' });
      }
    } catch {
      findings.push({ ok: false, msg: 'Cannot parse feature_list.json for scope audit' });
    }
  } else {
    findings.push({ ok: false, msg: 'feature_list.json missing — cannot audit scope boundaries' });
  }

  // Check for ADRs or design docs
  const hasAdr = existsSync(join(TARGET, 'docs', 'adr'));
  if (hasAdr) {
    score += 10;
    findings.push({ ok: true, msg: 'ADR directory found — architectural scope tracked' });
  }

  return { score: Math.min(100, score), findings };
}

// ─────────────── 5. Lifecycle ───────────────
function auditLifecycle() {
  let score = 0;
  const findings = [];
  const hasHandoff = existsSync(join(TARGET, 'session-handoff.md'));
  const hasMemory = existsSync(join(TARGET, 'memory')) || existsSync(join(TARGET, '.claude', 'agent-memory'));
  const hasHooks = existsSync(join(TARGET, '.claude', 'settings.json'));

  if (hasHandoff) {
    score += 40;
    findings.push({ ok: true, msg: 'session-handoff.md exists' });
    try {
      const content = readFileSync(join(TARGET, 'session-handoff.md'), 'utf8');
      if (content.includes('## Session:')) {
        score += 10;
        findings.push({ ok: true, msg: 'Has at least one session handoff block' });
      }
    } catch {}
  } else {
    findings.push({ ok: false, msg: 'No session-handoff.md — sessions cannot resume reliably' });
  }

  if (hasMemory) {
    score += 25;
    findings.push({ ok: true, msg: 'Memory directory exists — cross-session persistence configured' });
  } else {
    findings.push({ ok: false, msg: 'No memory directory — agent loses context between sessions' });
  }

  if (hasHooks) {
    score += 15;
    findings.push({ ok: true, msg: 'Hook configuration found (settings.json)' });
  } else {
    findings.push({ ok: false, msg: 'No hooks configured — no tool safety or lifecycle automation' });
  }

  // Check for progress tracking
  const hasProgress = existsSync(join(TARGET, 'progress.md'));
  if (hasProgress) {
    score += 10;
    findings.push({ ok: true, msg: 'progress.md enables task tracking across sessions' });
  }

  return { score: Math.min(100, score), findings };
}

// ─────────────── Main ───────────────

Object.entries(DIMENSIONS).forEach(([key, dim]) => {
  const auditFn = {
    instructions: auditInstructions,
    state: auditState,
    verification: auditVerification,
    scope: auditScope,
    lifecycle: auditLifecycle,
  }[key];
  const result = auditFn();
  results[key] = result.score;
  details[key] = result.findings;
});

const weighted = Object.entries(DIMENSIONS).reduce((sum, [key, dim]) => {
  return sum + (results[key] * dim.weight / 100);
}, 0);

if (JSON_OUTPUT) {
  console.log(JSON.stringify({
    date: new Date().toISOString().split('T')[0],
    target: TARGET,
    scores: results,
    weighted_total: Math.round(weighted * 10) / 10,
    details,
  }, null, 2));
  process.exit(0);
}

console.log('\n═══════════════════════════════════════════════');
console.log('  Harness Audit Report');
console.log('═══════════════════════════════════════════════\n');

// Determine color based on score
function color(score) {
  if (score >= 80) return '\x1b[0;32m'; // green
  if (score >= 60) return '\x1b[1;33m'; // yellow
  return '\x1b[0;31m'; // red
}
const NC = '\x1b[0m';
function bar(score) {
  const filled = Math.round(score / 10);
  return '█'.repeat(filled) + '░'.repeat(10 - filled);
}

Object.entries(DIMENSIONS).forEach(([key, dim]) => {
  const s = results[key];
  const c = color(s);
  console.log(`  ${dim.name.padEnd(20)} ${c}${bar(s)} ${s}/100${NC}`);
});

console.log('');
console.log(`  Weighted Total: ${Math.round(weighted * 10) / 10}/100`);
console.log('');

// Lowest scoring area
const lowest = Object.entries(results).sort((a, b) => a[1] - b[1])[0];
console.log(`  🔍 Lowest: ${DIMENSIONS[lowest[0]].name} (${lowest[1]}/100)`);
console.log('');

// Priority improvements
console.log('  Priority improvements:');
const allFindings = Object.entries(details).flatMap(([dim, f]) =>
  f.filter(x => !x.ok).map(x => ({ dim, ...x }))
);
allFindings.slice(0, 5).forEach((f, i) => {
  console.log(`  ${i + 1}. [${DIMENSIONS[f.dim].name}] ${f.msg}`);
});

if (allFindings.length === 0) {
  console.log('  ✓ All checks passed. Harness is production-grade.');
}

console.log('\n═══════════════════════════════════════════════\n');

if (SCORE_MODE) {
  console.log(JSON.stringify({
    date: new Date().toISOString().split('T')[0],
    scores: results,
    weighted_total: Math.round(weighted * 10) / 10,
    passed: weighted >= 70,
    gate: weighted >= 85 ? 'release' : weighted >= 70 ? 'feature' : 'fail',
    findings: allFindings.slice(0, 5),
  }, null, 2));
}

process.exit(weighted >= 70 ? 0 : 1);

// ─────────────── Helpers ───────────────

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
