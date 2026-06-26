#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────
// run-benchmark.mjs — Structural benchmark for harness readiness
// ─────────────────────────────────────────────────────────────
// Usage: node run-benchmark.mjs [--target /path] [--html report.html] [--ci]
//
// Benchmarks run against the project structure, not runtime perf.
// For runtime benchmarks, integrate with your test suite.
// ─────────────────────────────────────────────────────────────

import { readFileSync, existsSync, statSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const args = parseArgs(process.argv.slice(2));
const TARGET = resolve(args.target || process.cwd());
const CI_MODE = args.ci || false;
const HTML_OUTPUT = args.html || null;

const startTime = Date.now();

// ─────────────── Benchmark Categories ───────────────

function benchFileCount() {
  const countFiles = (dir, exts) => {
    if (!existsSync(dir)) return 0;
    let count = 0;
    try {
      const entries = readdirSync(dir, { recursive: true });
      for (const entry of entries) {
        if (exts.some(ext => entry.endsWith(ext))) count++;
      }
    } catch { return 0; }
    return count;
  };

  const pyFiles = countFiles(TARGET, ['.py']);
  const mdFiles = countFiles(TARGET, ['.md']);
  const jsonFiles = countFiles(join(TARGET, '.claude'), ['.json']);

  return {
    python_files: pyFiles,
    markdown_files: mdFiles,
    config_files: jsonFiles,
    total_tracked: pyFiles + mdFiles,
  };
}

function benchAgentFleet() {
  const agentDir = join(TARGET, '.claude', 'agents');
  if (!existsSync(agentDir)) return { agent_count: 0, agents: [] };

  const agents = readdirSync(agentDir).filter(f => f.endsWith('.md'));
  const agentsWithTools = [];
  for (const agent of agents) {
    try {
      const content = readFileSync(join(agentDir, agent), 'utf8');
      const toolMatch = content.match(/tools:\s*(.+)/);
      const tools = toolMatch ? toolMatch[1].split(',').map(s => s.trim()).filter(Boolean) : [];
      const modelMatch = content.match(/model:\s*(\S+)/);
      const model = modelMatch ? modelMatch[1] : 'unknown';
      agentsWithTools.push({ name: agent.replace('.md', ''), toolCount: tools.length, model });
    } catch { agentsWithTools.push({ name: agent.replace('.md', ''), toolCount: 0, model: 'unknown' }); }
  }

  return {
    agent_count: agents.length,
    total_tools_assigned: agentsWithTools.reduce((sum, a) => sum + a.toolCount, 0),
    avg_tools_per_agent: agents.length ? Math.round(agentsWithTools.reduce((sum, a) => sum + a.toolCount, 0) / agents.length) : 0,
    agents: agentsWithTools,
  };
}

function benchTestCoverage() {
  const testDir = join(TARGET, 'tests');
  if (!existsSync(testDir)) return { test_files: 0, total_lines: 0, approximate_coverage_pct: 0 };

  const testFiles = readdirSync(testDir).filter(f => f.endsWith('.py'));
  let totalLines = 0;
  for (const f of testFiles) {
    try {
      totalLines += readFileSync(join(testDir, f), 'utf8').split('\n').length;
    } catch {}
  }

  // Estimate source lines
  let srcLines = 0;
  const srcFiles = readdirSync(TARGET).filter(f => f.endsWith('.py') && !f.startsWith('test_'));
  for (const f of srcFiles) {
    try {
      srcLines += readFileSync(join(TARGET, f), 'utf8').split('\n').length;
    } catch {}
  }

  return {
    test_files: testFiles.length,
    test_lines: totalLines,
    source_lines: srcLines,
    test_to_source_ratio: srcLines ? (totalLines / srcLines).toFixed(2) : 0,
  };
}

function benchMcpServers() {
  const mcpPath = join(TARGET, '.claude', 'mcp.json');
  if (!existsSync(mcpPath)) return { mcp_servers: 0, total_tools: 0 };

  try {
    const config = JSON.parse(readFileSync(mcpPath, 'utf8'));
    const servers = config.mcpServers || {};
    const names = Object.keys(servers);
    return {
      mcp_servers: names.length,
      server_names: names,
    };
  } catch {
    return { mcp_servers: 0, server_names: [] };
  }
}

function benchDocs() {
  const adrDir = join(TARGET, 'docs', 'adr');
  const adrCount = existsSync(adrDir) ? readdirSync(adrDir).filter(f => f.endsWith('.md')).length : 0;
  const contextMd = existsSync(join(TARGET, 'CONTEXT.md')) ? 1 : 0;
  const agentDocs = existsSync(join(TARGET, 'docs', 'agents')) ? readdirSync(join(TARGET, 'docs', 'agents')).length : 0;

  return { adr_count: adrCount, context_md: contextMd, agent_docs: agentDocs };
}

function benchHarnessArtifacts() {
  const files = ['CLAUDE.md', 'AGENTS.md', 'feature_list.json', 'progress.md', 'init.sh', 'session-handoff.md'];
  const present = files.filter(f => existsSync(join(TARGET, f)));
  return {
    harness_files_present: present.length,
    harness_files_total: files.length,
    harness_ratio: present.length / files.length,
    missing: files.filter(f => !present.includes(f)),
  };
}

function benchGitHealth() {
  try {
    const log = spawnSync('git', ['log', '--oneline', '-20'], { cwd: TARGET, encoding: 'utf8', timeout: 10000 });
    const commits = log.stdout.trim().split('\n').filter(Boolean).length;
    const status = spawnSync('git', ['status', '--porcelain'], { cwd: TARGET, encoding: 'utf8', timeout: 10000 });
    const dirty = status.stdout.trim().split('\n').filter(Boolean).length;
    return {
      recent_commits: commits,
      dirty_files: dirty,
      clean: dirty === 0,
    };
  } catch {
    return { recent_commits: 0, dirty_files: 0, clean: true };
  }
}

// ─────────────── Run All ───────────────

const results = {
  meta: {
    project: TARGET.split(/[/\\]/).pop(),
    date: new Date().toISOString().split('T')[0],
    duration_ms: 0,
  },
  file_counts: benchFileCount(),
  agent_fleet: benchAgentFleet(),
  test_coverage: benchTestCoverage(),
  mcp_servers: benchMcpServers(),
  docs: benchDocs(),
  harness_artifacts: benchHarnessArtifacts(),
  git_health: benchGitHealth(),
};

results.meta.duration_ms = Date.now() - startTime;

// ─────────────── Interpret ───────────────

function interpret(r) {
  const checks = [];

  // Harness completeness
  if (r.harness_artifacts.harness_ratio >= 0.83) checks.push({ ok: true, msg: 'Harness artifacts complete (5/6+)' });
  else checks.push({ ok: false, msg: `Only ${r.harness_artifacts.harness_files_present}/${r.harness_artifacts.harness_files_total} harness files` });

  // Agent fleet
  if (r.agent_fleet.agent_count >= 3) checks.push({ ok: true, msg: `Agent fleet operational (${r.agent_fleet.agent_count} agents)` });
  else if (r.agent_fleet.agent_count > 0) checks.push({ ok: false, msg: `Minimal agent fleet (${r.agent_fleet.agent_count} agents)` });
  else checks.push({ ok: false, msg: 'No agents defined' });

  // Tests
  if (r.test_coverage.test_files >= 3) checks.push({ ok: true, msg: `Test suite adequate (${r.test_coverage.test_files} files)` });
  else if (r.test_coverage.test_files > 0) checks.push({ ok: false, msg: `Test suite minimal (${r.test_coverage.test_files} files)` });
  else checks.push({ ok: false, msg: 'No test files found' });

  // Docs
  if (r.docs.adr_count >= 3) checks.push({ ok: true, msg: 'ADR documentation solid' });
  else if (r.docs.adr_count > 0) checks.push({ ok: false, msg: `Only ${r.docs.adr_count} ADRs` });
  else checks.push({ ok: false, msg: 'No ADRs — architectural decisions untracked' });

  // MCP
  if (r.mcp_servers.mcp_servers >= 1) checks.push({ ok: true, msg: `MCP servers configured (${r.mcp_servers.mcp_servers})` });
  else checks.push({ ok: false, msg: 'No MCP servers configured' });

  // Git
  if (r.git_health.clean) checks.push({ ok: true, msg: 'Working tree clean' });
  else checks.push({ ok: false, msg: `${r.git_health.dirty_files} dirty files — commit or stash` });

  return checks;
}

const checks = interpret(results);
const passed = checks.filter(c => c.ok).length;
const total = checks.length;
const score = Math.round((passed / total) * 100);

if (CI_MODE) {
  console.log(JSON.stringify({ ...results, interpretation: { checks, score, passed, total } }, null, 2));
  process.exit(score >= 70 ? 0 : 1);
}

// ─────────────── Console Output ───────────────

console.log('\n╔══════════════════════════════════════════════╗');
console.log('║  📊 Harness Benchmark Report                ║');
console.log('╚══════════════════════════════════════════════╝\n');
console.log(`  Project: ${results.meta.project}`);
console.log(`  Duration: ${results.meta.duration_ms}ms\n`);

console.log('  ── Structure ──');
console.log(`  Python files:    ${results.file_counts.python_files}`);
console.log(`  Markdown files:  ${results.file_counts.markdown_files}`);
console.log(`  Config files:    ${results.file_counts.config_files}`);

console.log('\n  ── Agent Fleet ──');
console.log(`  Agents:          ${results.agent_fleet.agent_count}`);
console.log(`  Avg tools/agent: ${results.agent_fleet.avg_tools_per_agent}`);
results.agent_fleet.agents.forEach(a => {
  console.log(`    ${a.name.padEnd(30)} ${a.model.padEnd(8)} ${a.toolCount} tools`);
});

console.log('\n  ── Test Coverage ──');
console.log(`  Test files:      ${results.test_coverage.test_files}`);
console.log(`  Test lines:      ${results.test_coverage.test_lines}`);
console.log(`  Source lines:    ${results.test_coverage.source_lines}`);
console.log(`  Test/Src ratio:  ${results.test_coverage.test_to_source_ratio}`);

console.log('\n  ── MCP ──');
console.log(`  Servers:         ${results.mcp_servers.mcp_servers}`);
if (results.mcp_servers.server_names) {
  results.mcp_servers.server_names.forEach(n => console.log(`    - ${n}`));
}

console.log('\n  ── Docs ──');
console.log(`  ADRs:            ${results.docs.adr_count}`);
console.log(`  CONTEXT.md:      ${results.docs.context_md ? '✓' : '✗'}`);
console.log(`  Agent docs:      ${results.docs.agent_docs}`);

console.log('\n  ── Harness ──');
console.log(`  Files:           ${results.harness_artifacts.harness_files_present}/${results.harness_artifacts.harness_files_total}`);
if (results.harness_artifacts.missing.length) {
  results.harness_artifacts.missing.forEach(f => console.log(`    ✗ ${f}`));
}

console.log('\n  ── Git ──');
console.log(`  Recent commits:  ${results.git_health.recent_commits}`);
console.log(`  Dirty files:     ${results.git_health.dirty_files}`);
console.log(`  Clean:           ${results.git_health.clean ? '✓' : '✗'}`);

console.log('\n  ── Interpretation ──');
checks.forEach(c => {
  const icon = c.ok ? '✓' : '✗';
  console.log(`  ${icon} ${c.msg}`);
});
console.log(`\n  Score: ${score}/100 (${passed}/${total} checks passed)\n`);

// ─────────────── HTML Output ───────────────

if (HTML_OUTPUT) {
  const html = generateHtml(results, checks, score);
  const { mkdirSync } = await import('node:fs');
  const outDir = resolve(HTML_OUTPUT, '..');
  if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
  writeFileSync(HTML_OUTPUT, html, 'utf8');
  console.log(`  📄 HTML report: ${HTML_OUTPUT}\n`);
}

// ─────────────── Helpers ───────────────

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) { result[key] = next; i++; }
      else { result[key] = true; }
    }
  }
  return result;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function generateHtml(r, checks, score) {
  const s = (ok) => ok ? 'color:#3fb950' : 'color:#f85149';
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Benchmark — ${escapeHtml(r.meta.project)}</title>
<style>
:root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --muted: #8b949e; --green: #3fb950; --yellow: #d2991d; --red: #f85149; --blue: #58a6ff; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 2rem; line-height: 1.6; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin-bottom: 0.3rem; }
h2 { font-size: 1.1rem; margin: 1.5rem 0 0.75rem; padding-bottom: 0.3rem; border-bottom: 1px solid var(--border); }
.meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 1.2rem; margin-bottom: 0.75rem; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.4rem 0.5rem; text-align: left; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
th { color: var(--muted); }
.big-score { text-align: center; font-size: 3rem; font-weight: 800; margin: 1rem 0; }
.footer { margin-top: 2rem; padding-top: 0.75rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.75rem; text-align: center; }
.check { padding: 0.3rem 0; }
</style>
</head>
<body>
<div class="container">
<h1>📊 Harness Benchmark</h1>
<div class="meta">${escapeHtml(r.meta.project)} — ${r.meta.date} — ${r.meta.duration_ms}ms</div>

<div class="big-score" style="color:${score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--yellow)' : 'var(--red)'}">${score}/100</div>

<h2>Structure</h2>
<div class="card"><table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Python files</td><td>${r.file_counts.python_files}</td></tr>
<tr><td>Markdown files</td><td>${r.file_counts.markdown_files}</td></tr>
<tr><td>Config files</td><td>${r.file_counts.config_files}</td></tr>
</table></div>

<h2>Agent Fleet</h2>
<div class="card"><table>
<tr><th>Agent</th><th>Model</th><th>Tools</th></tr>
${r.agent_fleet.agents.map(a => `<tr><td>${escapeHtml(a.name)}</td><td>${a.model}</td><td>${a.toolCount}</td></tr>`).join('')}
</table></div>

<h2>Test Coverage</h2>
<div class="card"><table>
<tr><td>Test files</td><td>${r.test_coverage.test_files}</td></tr>
<tr><td>Test lines</td><td>${r.test_coverage.test_lines}</td></tr>
<tr><td>Source lines</td><td>${r.test_coverage.source_lines}</td></tr>
<tr><td>Ratio</td><td>${r.test_coverage.test_to_source_ratio}</td></tr>
</table></div>

<h2>Checks</h2>
<div class="card">
${checks.map(c => `<div class="check" style="${s(c.ok)}">${c.ok ? '✓' : '✗'} ${escapeHtml(c.msg)}</div>`).join('')}
</div>

<div class="footer">Generated by run-benchmark.mjs · ${new Date().toISOString()}</div>
</div>
</body>
</html>`;
}

// Dynamic import helper
import { mkdirSync } from 'node:fs';
