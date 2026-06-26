#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────
// render-assessment-html.mjs — Generate HTML harness assessment report
// ─────────────────────────────────────────────────────────────
// Usage: node render-assessment-html.mjs [--target /path] [--output report.html]
// ─────────────────────────────────────────────────────────────

import { readFileSync, existsSync, writeFileSync, statSync } from 'node:fs';
import { join, resolve, relative } from 'node:path';
import { spawnSync } from 'node:child_process';

const args = parseArgs(process.argv.slice(2));
const TARGET = resolve(args.target || process.cwd());
const OUTPUT = resolve(args.output || join(TARGET, 'reports', 'harness-assessment.html'));

// Run validation first
const scriptDir = import.meta.dirname || join(process.cwd(), 'scripts', 'harness');
const validatePath = join(scriptDir, 'validate-harness.mjs');
let validateOutput;
try {
  const result = spawnSync(process.execPath, [validatePath, '--target', TARGET, '--json'], {
    encoding: 'utf8',
    timeout: 30000,
  });
  validateOutput = JSON.parse(result.stdout);
} catch {
  validateOutput = {
    date: new Date().toISOString().split('T')[0],
    scores: { instructions: 0, state: 0, verification: 0, scope: 0, lifecycle: 0 },
    weighted_total: 0,
    details: {},
  };
}

// Gather project info
const info = gatherInfo(TARGET);
const scores = validateOutput.scores;
const total = validateOutput.weighted_total;

// Generate HTML
const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harness Assessment — ${escapeHtml(info.name)}</title>
<style>
:root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --muted: #8b949e; --green: #3fb950; --yellow: #d2991d; --red: #f85149; --blue: #58a6ff; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 2rem; line-height: 1.6; }
.container { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.8rem; margin-bottom: 0.3rem; }
h2 { font-size: 1.2rem; margin: 2rem 0 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }
.meta { color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }
.score-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1rem; margin-bottom: 2rem; }
.score-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem; text-align: center; }
.score-card .label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; }
.score-card .value { font-size: 2.5rem; font-weight: 700; }
.score-card .bar { margin-top: 0.5rem; height: 4px; border-radius: 2px; }
.bar-green { background: var(--green); }
.bar-yellow { background: var(--yellow); }
.bar-red { background: var(--red); }
.big-total { text-align: center; margin: 2rem 0; }
.big-total .number { font-size: 4rem; font-weight: 800; }
.big-total .label { font-size: 0.9rem; color: var(--muted); }
.finding { padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
.finding:last-child { border-bottom: none; }
.finding .icon { margin-right: 0.5rem; }
.finding .dim-tag { display: inline-block; font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; margin-right: 0.5rem; background: rgba(88,166,255,0.15); color: var(--blue); }
.finding .ok-tag { color: var(--green); }
.finding .fail-tag { color: var(--red); }
.recommendation { padding: 0.75rem 1rem; background: rgba(88,166,255,0.08); border-left: 3px solid var(--blue); border-radius: 4px; margin-bottom: 0.5rem; }
.recommendation .num { color: var(--blue); font-weight: 700; margin-right: 0.5rem; }
.gate-badge { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; margin-left: 0.5rem; }
.gate-pass { background: rgba(63,185,80,0.15); color: var(--green); }
.gate-fail { background: rgba(248,81,73,0.15); color: var(--red); }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
th { color: var(--muted); font-weight: 500; }
.footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; text-align: center; }
</style>
</head>
<body>
<div class="container">

<h1>🔍 Harness Assessment Report</h1>
<div class="meta">
  <strong>${escapeHtml(info.name)}</strong> — ${validateOutput.date}
  <br>${escapeHtml(TARGET)}
  <span class="gate-badge ${total >= 70 ? 'gate-pass' : 'gate-fail'}">${total >= 85 ? 'RELEASE-READY' : total >= 70 ? 'FEATURE-READY' : 'NEEDS WORK'}</span>
</div>

<div class="score-grid">
${renderScoreCard('Instructions', scores.instructions || 0, 25)}
${renderScoreCard('State', scores.state || 0, 25)}
${renderScoreCard('Verification', scores.verification || 0, 20)}
${renderScoreCard('Scope', scores.scope || 0, 15)}
${renderScoreCard('Lifecycle', scores.lifecycle || 0, 15)}
</div>

<div class="big-total">
  <div class="number" style="color: ${total >= 80 ? 'var(--green)' : total >= 60 ? 'var(--yellow)' : 'var(--red)'}">${total}</div>
  <div class="label">Weighted Total / 100</div>
</div>

<h2>📋 Subsytem Details</h2>
${Object.entries(validateOutput.details || {}).map(([dim, findings]) => `
<div class="card">
  <h3>${dim.charAt(0).toUpperCase() + dim.slice(1)}</h3>
  ${(findings || []).map(f => `
  <div class="finding">
    <span class="icon">${f.ok ? '<span class="ok-tag">✔</span>' : '<span class="fail-tag">✘</span>'}</span>
    <span>${escapeHtml(f.msg)}</span>
  </div>
  `).join('')}
</div>
`).join('')}

<h2>🛠 Priority Recommendations</h2>
${generateRecommendations(validateOutput).map((r, i) => `
<div class="recommendation">
  <span class="num">#${i + 1}</span>
  <span class="dim-tag">${escapeHtml(r.dim)}</span>
  ${escapeHtml(r.msg)}
</div>
`).join('')}

<h2>📊 Project Snapshot</h2>
<div class="card">
<table>
${Object.entries(info).map(([k, v]) => `
<tr><td><strong>${escapeHtml(k)}</strong></td><td>${escapeHtml(String(v))}</td></tr>
`).join('')}
</table>
</div>

<div class="footer">
  Generated by harness-creator · ${new Date().toISOString()}
</div>

</div>
</body>
</html>`;

// Ensure output dir
const outDir = resolve(OUTPUT, '..');
if (!existsSync(outDir)) {
  const { mkdirSync } = await import('node:fs');
  mkdirSync(outDir, { recursive: true });
}
writeFileSync(OUTPUT, html, 'utf8');
console.log(`✅ Report written to ${OUTPUT}`);
console.log(`   Open with: start ${OUTPUT}`);

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
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderScoreCard(label, score, weight) {
  const c = score >= 80 ? 'bar-green' : score >= 60 ? 'bar-yellow' : 'bar-red';
  const colorVal = score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--yellow)' : 'var(--red)';
  return `
  <div class="score-card">
    <div class="label">${label}</div>
    <div class="value" style="color:${colorVal}">${score}</div>
    <div style="font-size:0.7rem;color:var(--muted);margin-top:2px">weight: ${weight}%</div>
    <div class="bar ${c}" style="width:${Math.min(100, score)}%"></div>
  </div>`;
}

function gatherInfo(target) {
  const info = {};
  info['Project Name'] = target.split(/[/\\]/).pop();
  info['Date'] = new Date().toISOString().split('T')[0];
  info['CLAUDE.md'] = existsSync(join(target, 'CLAUDE.md')) ? '✓' : '✗';
  info['AGENTS.md'] = existsSync(join(target, 'AGENTS.md')) ? '✓' : '✗';
  info['feature_list.json'] = existsSync(join(target, 'feature_list.json')) ? '✓' : '✗';
  info['progress.md'] = existsSync(join(target, 'progress.md')) ? '✓' : '✗';
  info['init.sh'] = existsSync(join(target, 'init.sh')) ? '✓' : '✗';
  info['session-handoff.md'] = existsSync(join(target, 'session-handoff.md')) ? '✓' : '✗';
  info['Tests'] = existsSync(join(target, 'tests')) ? '✓' : '✗';
  info['ADRs'] = existsSync(join(target, 'docs', 'adr')) ? '✓' : '✗';
  info['Git'] = existsSync(join(target, '.git')) ? '✓' : '✗';
  try {
    const pkg = JSON.parse(readFileSync(join(target, 'package.json'), 'utf8'));
    info['Node.js'] = pkg.version || 'present';
  } catch { info['Node.js'] = '✗'; }
  return info;
}

function generateRecommendations(data) {
  const failures = [];
  Object.entries(data.details || {}).forEach(([dim, findings]) => {
    (findings || []).filter(f => !f.ok).forEach(f => failures.push({ dim, msg: f.msg }));
  });

  if (failures.length === 0) {
    return [{ dim: 'all', msg: 'All checks passed. Consider running benchmarks for performance baselines.' }];
  }
  return failures.slice(0, 8).map(f => ({
    dim: f.dim,
    msg: f.msg,
  }));
}

// Dynamic import for mkdirSync — use sync version
import { mkdirSync } from 'node:fs';
