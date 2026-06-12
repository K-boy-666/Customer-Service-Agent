---
name: mcp-server-connection-fix-20260611
description: MCP server 连接问题根因分析和修复记录：sql.js WASM 中文路径、数据库初始化静默崩溃、.mcp.json Windows 兼容性
metadata:
  type: project
---

## 2026-06-11 — MCP Server 连接问题诊断与修复

**问题**: customer-service MCP server 无法被 Claude Code 连接，`track_order` 等工具不可用。

**根因分析**:
1. **sql.js WASM 加载失败（大概率根因）**: `sql.js` 内部使用 Emscripten 的 URL 解析来定位 `sql-wasm.wasm`。当路径包含中文字符（`客服智能体2.0`）时，Emscripten 的 `import.meta.url` 解析可能产生编码问题导致 WASM 文件定位失败。
2. **数据库初始化静默崩溃**: `dist/index.js` 顶层 `await initSchema()` / `await seedData()` 如果失败，整个模块加载就崩了——`main()` 永远不会执行，stderr 上也没有任何错误输出。Claude Code 看到的就是"MCP server 启动后立即退出"。
3. **`.mcp.json` 缺少 cwd/env**: Windows 下 `node` 进程可能需要显式的工作目录和环境变量。

**修复内容（4 个文件共 5 处修改）**:

| 文件 | 修改 |
|------|------|
| `mcp-server/dist/db.js` | `initSqlJs()` 调用时传入 `locateFile` 显式指定 WASM 路径，绕过 Emscripten URL 解析 |
| `mcp-server/src/db.ts` | 同上，保持源文件同步 |
| `mcp-server/dist/index.js` | 顶层 `initSchema()`/`seedData()` 加 try-catch，失败时输出 stderr 日志而非静默退出 |
| `mcp-server/src/index.ts` | 同上 |
| `.mcp.json` | 添加 `cwd` 和 `env` 字段 |

**Why**: 中文字符路径是 Windows 真实环境的常见问题，sql.js 的 Emscripten WASM 加载对这个场景敏感。静默崩溃问题导致任何错误都无法被观测到。

**How to apply**: 重启 Claude Code 会话后 `.mcp.json` 生效。如果仍连不上，运行 `node mcp-server/dist/diagnose.js` 获取精确错误信息。

**验证方法**:
```
node mcp-server/dist/diagnose.js
```
— 应输出所有 ✅ 通过。

**验证 MCP tools**:
在 Claude Code 重启后，确认工具列表中出现 `track_order`、`query_customer`、`ask_knowledge_base`、`process_after_sale`、`diagnose_product`、`issue_credit`、`manage_ticket`。
