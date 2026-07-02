<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>客服智能体 2.0 — 代码库架构理解</title>
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #58a6ff;
    --accent2: #f78166;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --purple: #bc8cff;
    --cyan: #39d2c0;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, "Noto Sans CJK SC", "Segoe UI", Helvetica, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    font-size: 15px;
  }
  .container { max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }

  /* Hero */
  .hero {
    text-align: center;
    padding: 3rem 1rem 2rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2rem;
  }
  .hero h1 {
    font-size: 2.2rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--purple));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 0.5rem;
  }
  .hero .subtitle { color: var(--text-dim); font-size: 1.1rem; }
  .hero .meta {
    display: flex;
    justify-content: center;
    gap: 1.5rem;
    margin-top: 1rem;
    flex-wrap: wrap;
  }
  .hero .meta span {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 0.3rem 0.9rem;
    font-size: 0.85rem;
    color: var(--text-dim);
  }
  .hero .meta span strong { color: var(--accent); }

  /* TOC */
  .toc {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 2.5rem;
  }
  .toc h2 { font-size: 1.1rem; margin-bottom: 0.75rem; color: var(--accent); }
  .toc ul { list-style: none; columns: 2; gap: 1rem; }
  @media (max-width: 640px) { .toc ul { columns: 1; } }
  .toc li { margin-bottom: 0.3rem; }
  .toc a { color: var(--text-dim); text-decoration: none; font-size: 0.9rem; transition: color 0.2s; }
  .toc a:hover { color: var(--accent); }

  /* Section */
  section {
    margin-bottom: 3rem;
    scroll-margin-top: 1rem;
  }
  h2.section-title {
    font-size: 1.6rem;
    font-weight: 700;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  h2.section-title .num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px; height: 32px;
    background: var(--accent);
    color: var(--bg);
    border-radius: 8px;
    font-size: 0.9rem;
    font-weight: 700;
  }
  h3 { font-size: 1.2rem; margin: 1.5rem 0 0.75rem; color: var(--text); }
  h4 { font-size: 1rem; margin: 1rem 0 0.5rem; color: var(--accent); }

  p { margin-bottom: 0.75rem; }

  /* Code blocks */
  pre {
    background: #010409;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    overflow-x: auto;
    margin: 1rem 0;
    font-size: 0.85rem;
    line-height: 1.5;
  }
  code {
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
    color: var(--text);
  }
  p code, li code, td code {
    background: rgba(88,166,255,0.1);
    border: 1px solid rgba(88,166,255,0.2);
    border-radius: 4px;
    padding: 0.1rem 0.35rem;
    font-size: 0.85em;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.9rem;
  }
  th, td {
    text-align: left;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--border);
  }
  th { background: var(--card); color: var(--accent); font-weight: 600; }
  tr:nth-child(even) td { background: rgba(22,27,34,0.5); }

  /* Cards */
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 1rem;
    margin: 1rem 0;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
  }
  .card h4 { margin-top: 0; }
  .card .tag {
    display: inline-block;
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border-radius: 12px;
    margin-right: 0.3rem;
  }
  .tag-l0 { background: rgba(63,185,80,0.15); color: var(--green); }
  .tag-l1 { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .tag-l2 { background: rgba(248,81,73,0.15); color: var(--red); }

  /* Callouts */
  .callout {
    background: var(--card);
    border-left: 4px solid var(--accent);
    border-radius: 0 8px 8px 0;
    padding: 1rem 1.25rem;
    margin: 1rem 0;
  }
  .callout-warn { border-left-color: var(--yellow); }
  .callout-info { border-left-color: var(--cyan); }
  .callout p:last-child { margin-bottom: 0; }

  /* Flow diagram */
  .flow {
    background: #010409;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.5rem;
    margin: 1rem 0;
    font-family: monospace;
    font-size: 0.82rem;
    line-height: 1.6;
    white-space: pre;
    overflow-x: auto;
  }

  ul, ol { margin: 0.5rem 0 1rem 1.5rem; }
  li { margin-bottom: 0.3rem; }

  /* Badge */
  .badge {
    display: inline-block;
    font-size: 0.75rem;
    padding: 0.15rem 0.6rem;
    border-radius: 10px;
    font-weight: 600;
  }
  .badge-done { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge-adr { background: rgba(188,140,255,0.15); color: var(--purple); }

  .stat-row {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    margin: 1rem 0;
  }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.5rem;
    flex: 1;
    min-width: 140px;
    text-align: center;
  }
  .stat .num { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
  .stat .label { font-size: 0.8rem; color: var(--text-dim); margin-top: 0.25rem; }

  footer {
    text-align: center;
    padding: 2rem 0;
    border-top: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 0.85rem;
  }
</style>
</head>
<body>

<div class="container">

<!-- ===== HERO ===== -->
<div class="hero">
  <h1>客服智能体 2.0</h1>
  <p class="subtitle">代码库架构理解文档</p>
  <div class="meta">
    <span>Python <strong>3.10+</strong></span>
    <span>FastMCP + FastAPI</span>
    <span>SQLAlchemy ORM</span>
    <span>SQLite / MySQL</span>
    <span>84 tests · <strong>76%</strong> cov</span>
  </div>
</div>

<!-- ===== TOC ===== -->
<div class="toc">
  <h2>目录</h2>
  <ul>
    <li><a href="#s1">1. 项目概览与技术栈</a></li>
    <li><a href="#s2">2. 架构总览</a></li>
    <li><a href="#s3">3. 模块结构（22 个源文件）</a></li>
    <li><a href="#s4">4. 核心路由流：Orchestrator → Dispatcher → Sub-agent</a></li>
    <li><a href="#s5">5. 接口缝隙与适配器模式</a></li>
    <li><a href="#s6">6. 数据流与安全模型</a></li>
    <li><a href="#s7">7. 数据库与迁移</a></li>
    <li><a href="#s8">8. 基础设施层</a></li>
    <li><a href="#s9">9. 测试策略</a></li>
    <li><a href="#s10">10. 架构决策记录（ADR-0001~0010）</a></li>
    <li><a href="#s11">11. 关键设计模式</a></li>
    <li><a href="#s12">12. 优化演进路线（P1–P5）</a></li>
  </ul>
</div>

<!-- ===== 1. 项目概览 ===== -->
<section id="s1">
<h2 class="section-title"><span class="num">1</span>项目概览与技术栈</h2>

<p><strong>客服智能体 2.0</strong> 是一个基于多 Agent 架构的智能客服系统，采用 <strong>单一 Orchestrator 入口</strong>（ADR-0001）的路由模型。客户请求通过 Orchestrator 编排，由内部意图引擎分析后分发到 6 个子 Agent 处理，最终整合结果返回客户。</p>

<div class="stat-row">
  <div class="stat"><div class="num">22</div><div class="label">源文件 (src/)</div></div>
  <div class="stat"><div class="num">29</div><div class="label">REST 路由</div></div>
  <div class="stat"><div class="num">17</div><div class="label">MCP 工具</div></div>
  <div class="stat"><div class="num">14</div><div class="label">ORM 模型</div></div>
  <div class="stat"><div class="num">10</div><div class="label">ADR 决策</div></div>
  <div class="stat"><div class="num">84</div><div class="label">测试用例</div></div>
</div>

<h3>技术栈</h3>
<table>
  <tr><th>层</th><th>技术</th><th>说明</th></tr>
  <tr><td>Agent 传输</td><td>FastMCP (stdio)</td><td>两个 MCP 服务器：customer-service（对外）+ order-server（内部只读）</td></tr>
  <tr><td>REST API</td><td>FastAPI + Uvicorn</td><td>29 个路由，含限流装饰器、Prometheus 指标</td></tr>
  <tr><td>ORM</td><td>SQLAlchemy</td><td>14 个模型，Alembic 迁移</td></tr>
  <tr><td>数据库</td><td>SQLite (dev) / MySQL 8.0+ (prod)</td><td>方言适配器隔离差异</td></tr>
  <tr><td>日志</td><td>structlog 26.x</td><td>ProcessorFormatter 桥接 stdlib logging，JSON/console 双模式</td></tr>
  <tr><td>限流</td><td>slowapi</td><td>4 层分级限流，env-driven 配置</td></tr>
  <tr><td>指标</td><td>prometheus_client</td><td>标准 exposition format，多进程模式支持</td></tr>
  <tr><td>测试</td><td>pytest + pytest-xdist</td><td>并行加速（loadscope 分发），覆盖率 76%</td></tr>
  <tr><td>CI/CD</td><td>GitHub Actions + GHCR</td><td>lint + audit + 测试矩阵 + 迁移烟雾测试 + 镜像构建</td></tr>
</table>
</section>

<!-- ===== 2. 架构总览 ===== -->
<section id="s2">
<h2 class="section-title"><span class="num">2</span>架构总览</h2>

<div class="flow">客户请求
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  customer-service-orchestrator  (唯一客户入口, ADR-0001)  │
│                                                          │
│  问候 → 情绪分级 → 意图分析 → 分发 → 结果整合 → 收尾    │
│                                          │               │
│                 ┌────────────────────────┘               │
│                 ▼                                        │
│  ┌──────────────────────────┐                            │
│  │  dispatcher (内部引擎)   │                            │
│  │  RuleBasedIntentDispatcher│                           │
│  │  HybridIntentDispatcher  │ ← 未来 LLM 适配缝隙       │
│  └──────────┬───────────────┘                            │
│             │ 意图列表                                   │
│             ▼                                            │
│  ┌────┬────┬────┬────┬────┬────┐                        │
│  │ L0 │ L0 │ L1 │ L1 │ L2 │ L2 │  ← ADR-0004 权限模型  │
│  │order│cons│after│work│comp│hand│                      │
│  │查询│FAQ │售后│工单│投诉│人工│                        │
│  └────┴────┴────┴────┴────┴────┘                        │
└──────────────────────────────────────────────────────────┘
         │                              │
    MCP (stdio)                    REST API (HTTP)
  server_customer.py              order_api.py :8000
  17 个工具                       29 个路由</div>

<div class="callout callout-info">
<p><strong>核心约束（ADR-0001）</strong>：所有客户交互必须通过 <code>customer-service-orchestrator</code>。子 Agent 永远不直接面对客户。<code>dispatcher</code> 是 Orchestrator 的内部意图分析引擎，不是独立入口。</p>
</div>
</section>

<!-- ===== 3. 模块结构 ===== -->
<section id="s3">
<h2 class="section-title"><span class="num">3</span>模块结构（22 个源文件）</h2>

<h3>编排层</h3>
<table>
  <tr><th>文件</th><th>角色</th><th>关键内容</th></tr>
  <tr><td><code>orchestrator_runtime.py</code></td><td>核心运行时</td><td><code>CustomerServiceOrchestrator</code> + <code>LocalCustomerServiceTools</code>，确定性 Python 实现，无需 LLM 即可测试</td></tr>
  <tr><td><code>dispatcher.py</code></td><td>意图引擎</td><td><code>RuleBasedIntentDispatcher</code>（关键词+正则匹配）+ <code>HybridIntentDispatcher</code>（适配缝隙）</td></tr>
  <tr><td><code>orchestrator_mcp_tool.py</code></td><td>MCP 适配器</td><td>框架无关的 <code>handle_customer_message_tool()</code></td></tr>
  <tr><td><code>orchestrator_api.py</code></td><td>API 适配器</td><td>桥接 REST payload → runtime</td></tr>
</table>

<h3>REST API 层</h3>
<table>
  <tr><th>文件</th><th>角色</th></tr>
  <tr><td><code>order_api.py</code></td><td>FastAPI 应用，29 路由，lifespan，限流装饰器，Prometheus /metrics</td></tr>
  <tr><td><code>api_dependencies.py</code></td><td>JWT 解码 (<code>actor_dependency</code>) + request_id 注入</td></tr>
  <tr><td><code>api_client.py</code></td><td>MCP→REST 的 httpx 异步客户端，自动注入 Idempotency-Key</td></tr>
</table>

<h3>业务服务层</h3>
<table>
  <tr><th>文件</th><th>角色</th></tr>
  <tr><td><code>service_layer.py</code></td><td>RBAC 权限检查 + PII 脱敏 + 审计事件 + 状态机（Ticket/Return 转换图）</td></tr>
  <tr><td><code>security.py</code></td><td>认证/RBAC/OTP/PII/审计/幂等 全集</td></tr>
  <tr><td><code>analytics_service.py</code></td><td>使用量分析 + 日报聚合 + Markdown 渲染</td></tr>
  <tr><td><code>kb_service.py</code></td><td>FAQ RAG 检索：sentence-transformers 嵌入 → 词法回退</td></tr>
</table>

<h3>数据层</h3>
<table>
  <tr><th>文件</th><th>角色</th></tr>
  <tr><td><code>models.py</code></td><td>14 个 SQLAlchemy ORM 模型</td></tr>
  <tr><td><code>database.py</code></td><td>引擎/会话工厂，SQLite WAL pragma，MySQL 连接池</td></tr>
  <tr><td><code>numbering.py</code></td><td>编号适配器：<code>InProcessSequencer</code> / <code>MysqlCounterSequencer</code></td></tr>
  <tr><td><code>seed_data.py</code></td><td>开发环境种子数据</td></tr>
</table>

<h3>基础设施层</h3>
<table>
  <tr><th>文件</th><th>角色</th></tr>
  <tr><td><code>config.py</code></td><td><code>RuntimeConfig</code> 冻结 dataclass + Docker secrets + 生产校验</td></tr>
  <tr><td><code>log_config.py</code></td><td>structlog 配置：ProcessorFormatter 桥接 stdlib logging</td></tr>
  <tr><td><code>request_logging.py</code></td><td>纯 ASGI 中间件：contextvars 绑定 request_id</td></tr>
  <tr><td><code>rate_limit.py</code></td><td>slowapi Limiter + 4 级 LIMIT_* 常量</td></tr>
  <tr><td><code>metrics.py</code></td><td>prometheus_client：Histogram + 5 个 Gauge（multiprocess_mode）</td></tr>
</table>
</section>

<!-- ===== 4. 核心路由流 ===== -->
<section id="s4">
<h2 class="section-title"><span class="num">4</span>核心路由流：Orchestrator → Dispatcher → Sub-agent</h2>

<h3>消息处理主循环</h3>
<div class="flow">handle_message(customer_message)
  │
  ├─ 1. 空消息 → 返回问候语
  │
  ├─ 2. 情绪分级 _classify_emotion()
  │     L3 关键词(自杀/暴力) → 立即 human_handoff
  │     L2 关键词(投诉/律师/监管) → 标记 L2
  │     其他 → L1
  │
  ├─ 3. 意图分析 dispatcher.analyze() → list[IntentAnalysis]
  │     含多意图拆解 + safety_notes(注入检测)
  │
  ├─ 4. 逐意图分发（按优先级排序）
  │     satisfaction > complaint > work_order
  │     > human_handoff > after_sales > order_inquiry > consultation
  │
  ├─ 5. 结果整合 _compose_reply()
  │
  ├─ 6. 状态判定: success / needs-info / partial / needs-human
  │
  ├─ 7. 需人工? → 构建 handoff_package
  │
  ├─ 8. 持久化对话状态 → DB + LRU 缓存(256)
  │
  └─ 9. 记录 usage 事件（失败不阻断）</div>

<h3>子 Agent 权限模型（ADR-0004）</h3>
<div class="card-grid">
  <div class="card">
    <h4>L0 — 只读 <span class="tag tag-l0">order-inquiry</span> <span class="tag tag-l0">consultation</span></h4>
    <p>查询/检索/浏览，不可修改任何系统数据。</p>
    <p><code>_handle_order_inquiry</code> / <code>_handle_consultation</code></p>
  </div>
  <div class="card">
    <h4>L1 — 轻操作 <span class="tag tag-l1">after-sales</span> <span class="tag tag-l1">work-order</span></h4>
    <p>L0 + 创建记录、发起流程、小额补偿（有上限）。</p>
    <p><code>_handle_after_sales</code> / <code>_handle_work_order</code></p>
  </div>
  <div class="card">
    <h4>L2 — 无操作 <span class="tag tag-l2">complaint</span> <span class="tag tag-l2">human-handoff</span></h4>
    <p>仅对话，不接触任何业务系统。防止 prompt 注入。</p>
    <p><code>_handle_complaint</code> / <code>_handle_human_handoff</code></p>
  </div>
</div>

<h3>Dispatcher 意图引擎</h3>
<ul>
  <li><strong>规则匹配</strong>：基于关键词元组做子串匹配（l2_keywords / l3_keywords / after_sales_keywords 等）</li>
  <li><strong>正则提取</strong>：<code>ORD-\d{8}-\d{3}</code> 订单号、<code>[A-Z]{2}\d{10,16}</code> 运单号、<code>([1-5])\s*(?:星|分|star)</code> 评分</li>
  <li><strong>多意图</strong>：单条消息可匹配多意图（如"我要退款并投诉" → after_sales + complaint）</li>
  <li><strong>注入检测</strong>：<code>"ignore previous instructions"</code> / <code>"忽略之前"</code> → safety_notes</li>
  <li><strong>适配缝隙</strong>：<code>HybridIntentDispatcher</code> 封装规则引擎，标记 <code>fallback_reason</code>，为未来 LLM 意图分析预留接入点</li>
</ul>
</section>

<!-- ===== 5. 接口缝隙 ===== -->
<section id="s5">
<h2 class="section-title"><span class="num">5</span>接口缝隙与适配器模式</h2>

<h3>框架中立适配器</h3>
<p>系统设计了三层适配器，使编排逻辑与传输层完全解耦：</p>
<div class="flow">MCP (stdio) ─→ orchestrator_mcp_tool.handle_customer_message_tool()
REST (HTTP) ─→ orchestrator_api.respond_to_customer_message()
                              │
                              ▼
          orchestrator_runtime.CustomerServiceOrchestrator.handle_message()
                (纯 Python, 可无 LLM 测试)
                              │
                              ▼
                    service_layer.* (RBAC + 审计 + 状态机)
                              │
                              ▼
                    database.session_scope() → SQLAlchemy ORM</div>

<h3>ADR-0002 通信协议</h3>
<table>
  <tr><th>方向</th><th>格式</th></tr>
  <tr><td>输入</td><td><code>【客户上下文】</code> + <code>【任务】</code></td></tr>
  <tr><td>输出</td><td><code>【处理结果】</code>（状态码）+ <code>【客户回复】</code>（自然语言）+ <code>【内部备注】</code>（元信息）</td></tr>
</table>
<div class="callout callout-info">
<p>采用结构化自然语言而非纯 JSON 或纯自由文本：固定标签提供结构锚点，自然语言填充内容。LLM 友好且 Orchestrator 可解析。</p>
</div>

<h3>编号适配器缝隙（ADR-0007）</h3>
<ul>
  <li><code>NumberSequencer</code> 接口 + 工厂 <code>get_number_sequencer(database_url)</code></li>
  <li><code>InProcessSequencer</code>：SQLite 路径，线程锁 + DB max 查询</li>
  <li><code>MysqlCounterSequencer</code>：MySQL 路径，<code>sequence_counters</code> 表 + <code>LAST_INSERT_ID</code> 原子自增（连接级隔离无锁）</li>
</ul>
</section>

<!-- ===== 6. 数据流与安全模型 ===== -->
<section id="s6">
<h2 class="section-title"><span class="num">6</span>数据流与安全模型</h2>

<h3>REST API 端到端路径</h3>
<div class="flow">客户 → POST /api/orchestrator/respond
  Headers: Authorization: Bearer JWT, X-Identity-Verification: token
  │
  ├─ StructuredRequestLoggingMiddleware (纯 ASGI)
  │   bind_contextvars(request_id/method/path) → scope["state"]["request_id"]
  │
  ├─ slowapi @limiter.limit(LIMIT_ORCHESTRATOR) → 60/min, 按 IP
  │
  ├─ actor_dependency → decode_jwt_token() → Actor
  │
  ├─ load_verification(session, token) → Verification
  │
  └─ respond_to_customer_message()
        → CustomerServiceOrchestrator.handle_message()
            → dispatcher.analyze() → intents
            → handler(context) → service_layer.create_ticket/return/...
                → require_permission(actor, "ticket:create")
                → assert_verification_matches(verification, customer_id, order_id)
                → database.get_number_sequencer().next_number()
                → session.add(Ticket) → audit_event()
            → _record_usage_event() (失败不阻断)
            → _remember_conversation_state() → DB + LRU 缓存</div>

<h3>OTP / Token 验证流程（ADR-0005）</h3>
<div class="flow">1. POST /api/auth/otp/request (5/min)
   → 生成 6 位码, challenge_id
   → 存储 OtpChallenge(code_hash=SHA256(code), expires_at=now+10min)

2. POST /api/auth/otp/verify (5/min)
   → 校验 code_hash + 未过期 + 未重复验证
   → 生成 verification_token(token_urlsafe(32))

3. 受保护资源:
   → load_verification(token) 查询 OtpChallenge
   → assert_verification_matches():
       客户 scope token → 可授权该客户的所有资源
       订单 scope token → 仅授权该订单
       无 scope token → 不可访问受保护资源</div>

<h3>幂等性流程</h3>
<div class="flow">run_idempotent(session, actor, endpoint, key, payload, operation):
  1. digest = SHA256(payload) → request_hash
  2. 查询 IdempotencyKey(actor_subject, endpoint, key)
     ├─ 存在 + hash 匹配 → 返回缓存结果 (replayed=True)
     ├─ 存在 + hash 不匹配 → 409 冲突
     └─ 不存在 → 执行 operation() → 写入 IdempotencyKey
  3. IntegrityError(并发竞争) → rollback → 重查返回胜者结果</div>

<h3>JWT 认证</h3>
<ul>
  <li><strong>生产</strong>：强制 <code>OIDC_JWKS_URL</code>（RS256 via PyJWKClient）</li>
  <li><strong>开发</strong>：<code>AUTH_DEV_SECRET</code>（HS256），<code>create_dev_jwt()</code> 仅开发可用</li>
  <li><strong>角色→权限</strong>：admin 通配 <code>*</code>，orchestrator 仅 <code>orchestrator:invoke</code></li>
</ul>
</section>

<!-- ===== 7. 数据库与迁移 ===== -->
<section id="s7">
<h2 class="section-title"><span class="num">7</span>数据库与迁移</h2>

<h3>14 个 ORM 模型</h3>
<table>
  <tr><th>分类</th><th>模型</th></tr>
  <tr><td>业务实体</td><td><code>Customer</code>, <code>Product</code>, <code>Order</code>, <code>OrderItem</code>, <code>Shipment</code>, <code>ShipmentEvent</code></td></tr>
  <tr><td>客服实体</td><td><code>Ticket</code>, <code>TicketNote</code>, <code>ReturnRequest</code>, <code>SatisfactionSurvey</code></td></tr>
  <tr><td>安全/审计</td><td><code>OtpChallenge</code>, <code>AuditEvent</code>, <code>IdempotencyKey</code></td></tr>
  <tr><td>分析</td><td><code>CustomerServiceUsageEvent</code>（JSON 字段存 intents/tool_calls）</td></tr>
  <tr><td>会话</td><td><code>ConversationStateRecord</code></td></tr>
</table>

<h3>Alembic 迁移</h3>
<ul>
  <li><code>0001_initial_schema</code> — 初始 schema</li>
  <li><code>0002_customer_service_usage_events</code> — 分析事件表</li>
  <li><code>0003_conversation_states</code> — 对话状态持久化</li>
  <li><code>0004_sequence_counters</code> — MySQL 编号计数器表</li>
</ul>

<h3>连接池配置</h3>
<table>
  <tr><th>参数</th><th>SQLite</th><th>MySQL</th></tr>
  <tr><td>journal_mode</td><td>WAL</td><td>—</td></tr>
  <tr><td>busy_timeout</td><td>5000ms</td><td>—</td></tr>
  <tr><td>synchronous</td><td>NORMAL</td><td>—</td></tr>
  <tr><td>pool_size</td><td>—</td><td>10 (env 可配)</td></tr>
  <tr><td>max_overflow</td><td>—</td><td>20</td></tr>
  <tr><td>pool_recycle</td><td>—</td><td>3600s</td></tr>
  <tr><td>pool_timeout</td><td>—</td><td>30s</td></tr>
</table>
</section>

<!-- ===== 8. 基础设施层 ===== -->
<section id="s8">
<h2 class="section-title"><span class="num">8</span>基础设施层</h2>

<h3>结构化日志（ADR-0008）</h3>
<ul>
  <li><strong>structlog 26.x</strong> + <code>ProcessorFormatter</code> 桥接 stdlib <code>logging.getLogger()</code> 调用</li>
  <li><strong>生产</strong>：<code>JSONRenderer()</code>；<strong>开发</strong>：<code>ConsoleRenderer(colors=True)</code></li>
  <li><strong>纯 ASGI 中间件</strong>（非 <code>BaseHTTPMiddleware</code>）：确保 contextvars 跨同步/异步边界传播</li>
  <li><strong>MCP 日志</strong>：<code>log_to_stderr=True</code>，stdout 保留给 JSON-RPC 协议</li>
  <li><strong>零侵入</strong>：<code>foreign_pre_chain</code> 含 <code>merge_contextvars</code>，已有 LOGGER 调用自动获得 request_id</li>
</ul>

<h3>API 限流（ADR-0009）</h3>
<table>
  <tr><th>层级</th><th>端点</th><th>默认限制</th><th>环境变量</th></tr>
  <tr><td>OTP</td><td><code>/api/auth/otp/*</code></td><td>5/min</td><td><code>RATE_LIMIT_OTP</code></td></tr>
  <tr><td>Orchestrator</td><td><code>/api/orchestrator/respond</code></td><td>60/min</td><td><code>RATE_LIMIT_ORCHESTRATOR</code></td></tr>
  <tr><td>Write</td><td>POST/PUT/DELETE</td><td>30/min</td><td><code>RATE_LIMIT_WRITE</code></td></tr>
  <tr><td>Read</td><td>GET <code>/api/*</code></td><td>120/min</td><td><code>RATE_LIMIT_READ</code></td></tr>
  <tr><td>健康检查</td><td><code>/api/health</code>, <code>/ready</code>, <code>/metrics</code></td><td colspan="2">不限流</td></tr>
</table>

<h3>Prometheus 指标（ADR-0010）</h3>
<ul>
  <li><code>Histogram("http_request_duration_seconds", ["route"])</code> — 请求延迟直方图</li>
  <li>5 个 <code>Gauge</code>（<code>multiprocess_mode='mostrecent'</code>）：conversations / handoffs / tickets / returns / surveys</li>
  <li><code>/api/metrics</code> 返回 <code>generate_latest()</code> + <code>CONTENT_TYPE_LATEST</code></li>
  <li>多进程模式：<code>PROMETHEUS_MULTIPROC_DIR</code> + <code>MultiProcessCollector</code></li>
</ul>

<h3>监控栈</h3>
<ul>
  <li><strong>Prometheus</strong> (:9090) — 指标抓取</li>
  <li><strong>Grafana</strong> (:3000) — 可视化仪表盘</li>
  <li><strong>Alertmanager</strong> (:9093) — 告警路由</li>
  <li>告警规则：<code>ApiDown</code>、<code>HighHandoffRate</code>（>30%）、<code>HighApiLatencyP95</code>（>2s）</li>
</ul>
</section>

<!-- ===== 9. 测试策略 ===== -->
<section id="s9">
<h2 class="section-title"><span class="num">9</span>测试策略</h2>

<div class="stat-row">
  <div class="stat"><div class="num">84</div><div class="label">测试用例</div></div>
  <div class="stat"><div class="num">76%</div><div class="label">覆盖率</div></div>
  <div class="stat"><div class="num">~13s</div><div class="label">并行运行</div></div>
  <div class="stat"><div class="num">5x</div><div class="label">加速比</div></div>
</div>

<table>
  <tr><th>测试文件</th><th>类型</th><th>关注点</th></tr>
  <tr><td><code>test_orchestrator_e2e.py</code></td><td>集成</td><td>端到端编排流程</td></tr>
  <tr><td><code>test_api_and_migration_e2e.py</code></td><td>集成</td><td>REST API + 迁移 + /api/ready 503</td></tr>
  <tr><td><code>test_security_controls.py</code></td><td>安全</td><td>OTP / RBAC / PII / 幂等</td></tr>
  <tr><td><code>test_concurrency_*.py</code> (4 文件)</td><td>并发</td><td>对话状态隔离 / 编号无碰撞 / 幂等竞争 / SQLite WAL</td></tr>
  <tr><td><code>test_load_orchestrator.py</code></td><td>负载</td><td>P95 响应时间 / 吞吐量（串行运行）</td></tr>
  <tr><td><code>test_structlog_rate_limit.py</code></td><td>基础设施</td><td>JSON 日志 / 限流 / 429 响应</td></tr>
  <tr><td><code>test_metrics_prometheus.py</code></td><td>基础设施</td><td>Content-Type / gauge 类型 / bucket 边界</td></tr>
</table>

<h3>测试隔离机制</h3>
<ul>
  <li><code>conftest.py</code>：<code>RATE_LIMIT_ENABLED=false</code>（模块导入时）；<code>temp_db</code> fixture 创建临时 SQLite</li>
  <li><code>reset_engine_for_tests()</code>：dispose 引擎 + 重置缓存 + 重设 DATABASE_URL</li>
  <li><code>reset_conversation_states_for_tests()</code>：清空 <code>_CONVERSATION_STATES</code></li>
  <li><strong>xdist worker 隔离</strong>：每个 worker 是独立 OS 进程（Windows spawn），全局状态自动隔离</li>
  <li><strong>负载测试保持串行</strong>：<code>-m load</code> 标记，不用 xdist，确保 QPS 数据准确</li>
</ul>
</section>

<!-- ===== 10. ADR ===== -->
<section id="s10">
<h2 class="section-title"><span class="num">10</span>架构决策记录（ADR-0001~0010）</h2>

<table>
  <tr><th>ADR</th><th>标题</th><th>核心决策</th><th>状态</th></tr>
  <tr><td><span class="badge badge-adr">0001</span></td><td>单一 Orchestrator 入口</td><td>Orchestrator 为唯一客户入口，Dispatcher 降级为内部意图引擎</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0002</span></td><td>子 Agent 通信协议</td><td>结构化自然语言（【客户上下文】+【任务】→【处理结果】+【客户回复】+【内部备注】）</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0003</span></td><td>三层情绪升级阶梯</td><td>L1(Orchestrator) → L2(complaint-agent) → L3(human-handoff-agent)；关键词触发安全网</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0004</span></td><td>子 Agent 三级权限</td><td>L0 只读 / L1 轻操作 / L2 无操作；只能降级不能升级</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0005</span></td><td>Scoped Verification + 业务日分析</td><td>客户/订单 scope token；日报按 <code>REPORT_TIMEZONE</code> 业务日转 UTC 查询</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0006</span></td><td>密钥管理与部署</td><td>env 优先 + <code>_FILE</code> 增强（Docker secrets）+ 文档化轮换；compose overlay</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0007</span></td><td>数据库编号适配器</td><td>InProcessSequencer / MysqlCounterSequencer(LAST_INSERT_ID)；连接池调优</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0008</span></td><td>结构化日志</td><td>structlog + ProcessorFormatter 桥接 + 纯 ASGI 中间件 + contextvars</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0009</span></td><td>API 限流策略</td><td>slowapi 4 层分级 + env-driven；<code>headers_enabled=False</code> 兼容纯 ASGI</td><td><span class="badge badge-done">已采纳</span></td></tr>
  <tr><td><span class="badge badge-adr">0010</span></td><td>Prometheus 指标 + xdist</td><td>prometheus_client 标准库 + 多进程模式；pytest-xdist <code>--dist=loadscope</code></td><td><span class="badge badge-done">已采纳</span></td></tr>
</table>
</section>

<!-- ===== 11. 关键设计模式 ===== -->
<section id="s11">
<h2 class="section-title"><span class="num">11</span>关键设计模式</h2>

<div class="card-grid">
  <div class="card">
    <h4>适配器模式</h4>
    <p><code>orchestrator_mcp_tool</code> / <code>orchestrator_api</code> / <code>numbering sequencer</code> / <code>HybridIntentDispatcher</code> — 隔离框架、方言、实现差异。</p>
  </div>
  <div class="card">
    <h4>确定性运行时</h4>
    <p><code>orchestrator_runtime.py</code> 将 prompt 描述的路由规则转为可执行 Python，无需启动 LLM 即可测试全部业务逻辑。</p>
  </div>
  <div class="card">
    <h4>LRU + DB 双层缓存</h4>
    <p>对话状态：<code>_CONVERSATION_STATES</code> OrderedDict（max 256）+ DB 持久化。锁内操作缓存，锁外做 DB I/O（double-checked locking）。</p>
  </div>
  <div class="card">
    <h4>安全纵深</h4>
    <p>RBAC（角色→权限位）→ Verification scope → 幂等键 → 审计事件 → PII 脱敏。五层防御，每层独立。</p>
  </div>
  <div class="card">
    <h4>分析永不阻断</h4>
    <p><code>_record_usage_event()</code> 捕获 <code>SQLAlchemyError</code> 后仅 warning 返回，不阻塞客户回复路径。</p>
  </div>
  <div class="card">
    <h4>ProcessorFormatter 桥接</h4>
    <p><code>foreign_pre_chain</code> 含 <code>merge_contextvars</code>，使 <code>logging.getLogger(__name__)</code> 调用自动获得 request_id — 零侵入迁移。</p>
  </div>
</div>
</section>

<!-- ===== 12. 优化演进 ===== -->
<section id="s12">
<h2 class="section-title"><span class="num">12</span>优化演进路线（P1–P5）</h2>

<table>
  <tr><th>阶段</th><th>主题</th><th>关键产出</th><th>验证</th></tr>
  <tr><td><span class="badge badge-done">P1</span></td><td>生产部署加固</td><td>MySQL 迁移（编号适配器 + 连接池）、密钥管理（Docker secrets）、监控栈（Prometheus+Grafana+Alertmanager）、CI/CD</td><td>52 passed; ADR-0006/0007</td></tr>
  <tr><td><span class="badge badge-done">P2</span></td><td>并发与负载测试</td><td>修复 3 个并发 bug（OrderedDict 竞争、SQLite 无 WAL、幂等 TOCTOU）；22 个新测试</td><td>70 passed; 3 层测试体系</td></tr>
  <tr><td><span class="badge badge-done">P3</span></td><td>CI & 工具加固</td><td>覆盖率跟踪、pip-audit、全表迁移烟雾测试、/api/ready 503 修复、OpenAPI 文档、异常收窄</td><td>71 passed; cov 75%</td></tr>
  <tr><td><span class="badge badge-done">P4</span></td><td>结构化日志 + API 限流</td><td>structlog（JSON/console 双模式 + contextvars 传播）、slowapi 4 层限流</td><td>81 passed; cov 76%</td></tr>
  <tr><td><span class="badge badge-done">P5</span></td><td>Prometheus 迁移 + 并行测试</td><td>prometheus_client 标准库（多进程模式）、pytest-xdist（5x 加速）</td><td>84 passed; cov 76%</td></tr>
</table>

<div class="callout">
<p><strong>当前状态</strong>：所有计划阶段（P1–P5）均已完成。84 个测试通过，覆盖率 76.04%，测试套件运行时间从 63s 降至 13s。14 个功能全部 <code>done</code>，无活跃阻塞器，无计划中的下一步任务。</p>
</div>
</section>

<footer>
  客服智能体 2.0 — 代码库架构理解文档<br>
  生成于 2026-07-01 · 基于 22 个源文件、10 份 ADR、84 个测试用例的完整分析
</footer>

</div>
</body>
</html>
