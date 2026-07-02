// 方锐杰 — 简历（AI 工具落地赋能实习生）
// 编译命令：typst compile resume.typ

#set page(
  paper: "a4",
  margin: (top: 0.9cm, bottom: 0.7cm, left: 1.2cm, right: 1.2cm),
)

#set text(
  font: ("DFHeiW5-GB", "Segoe UI"),
  size: 8.5pt,
  lang: "zh",
)

#set par(leading: 0.45em)

// ============ 颜色变量 ============
#let accent   = rgb("#1a56db")
#let dark     = rgb("#1f2937")
#let muted    = rgb("#6b7280")
#let light-bg = rgb("#f3f4f6")
#let rule     = rgb("#d1d5db")

// ============ 通用函数 ============
#let section-title(body) = {
  v(0.2em)
  block(
    width: 100%,
    stroke: (bottom: 0.6pt + rule),
    inset: (bottom: 1.5pt),
  )[#text(size: 10pt, weight: "bold", fill: accent)[#body]]
  v(0.08em)
}

#let bullet-item(body) = {
  text(size: 8pt)[
    #h(0.3em)▸ #body
  ]
  v(0.02em)
}

#let bold-accent(body) = text(weight: "bold", fill: accent)[#body]
#let bold-dark(body)   = text(weight: "bold", fill: dark)[#body]

// ============ 个人信息 ============
#block(
  fill: accent,
  inset: (x: 0.7em, y: 0.4em),
  radius: 3pt,
)[
  #text(size: 14pt, weight: "bold", fill: white)[方锐杰]
  #h(0.4em)
  #text(size: 8pt, fill: rgb("bdd3f0"))[求职意向：AI 工具落地赋能实习生]
  #h(1fr)
  #text(size: 7.5pt, fill: rgb("e8edf5"))[17876378089  ·  3162990953\@qq.com]
]

v(0.2em)

// ============ 核心能力摘要 ============
#section-title[核心能力]
#block(
  fill: light-bg,
  inset: (x: 0.6em, y: 0.25em),
  radius: 3pt,
)[#text(size: 8pt)[
  #bold-dark[数学专业背景] + #bold-dark[自主掌握 AI Agent 全栈能力]，从零构建准生产级 #bold-accent[多 Agent 智能客服平台]。
  精通 #bold-accent[Agent 编排架构]设计、#bold-accent[RAG 检索增强生成]落地、#bold-accent[生产级工程化]部署——
  覆盖意图识别、工单生命周期、售后闭环、情绪升级完整业务链路，具备将 AI 工具从概念验证推进到生产部署的 #bold-accent[完整落地能力]。
]]

v(0.12em)

// ============ 项目经历 ============
#section-title[项目经历]

#text(size: 9.5pt, weight: "bold", fill: dark)[多 Agent 智能客服系统 —— 从架构设计到生产部署]
#text(size: 7.5pt, fill: muted)[2026.06 – 2026.07 | 个人项目 | #link("https://github.com/K-boy-666/Customer-Service-Agent")[GitHub 仓库]]
v(0.05em)

#text(size: 8pt, fill: muted)[
  从零构建准生产级多 Agent 智能客服平台，覆盖意图识别、工单生命周期、售后退换货、满意度闭环、情绪升级等完整业务链路，支持 SQLite 本地开发到 MySQL + Docker 生产部署的全环境切换。
]
v(0.05em)

#bullet-item[
  #bold-dark[多 Agent 架构设计：]设计 Orchestrator 单一入口 + Dispatcher 意图引擎 + 6 子 Agent 的分层架构，制定 Agent 间结构化自然语言通信协议，按 L0 只读 / L1 轻操作 / L2 无操作三级权限控制 Agent 能力边界。编写 10 份架构决策记录覆盖单入口设计、通信协议、情绪升级、权限模型、密钥管理等关键决策。
]
#bullet-item[
  #bold-dark[意图识别与 RAG 知识库：]构建规则 + 语义混合意图分发器，支持 7 类业务意图的并行识别与置信度排序，涵盖中英文关键字、正则模式匹配、多意图拆解与去重。基于 sentence-transformers 实现 FAQ 语义检索系统，嵌入向量匹配与字符 n-gram 词法评分双通道自动降级，包含查询扩展与意图加权评分。
]
#bullet-item[
  #bold-dark[生产级工程化：]完成 MySQL 方言适配序列号生成器、Docker Compose 生产部署、Prometheus + Grafana + Alertmanager 三层监控告警栈（转人工率超 30% / P95 延迟超 2s / 服务宕机）。搭建 structlog 零侵入结构化日志、slowapi 四层分级 API 限流（OTP 5/min · 核心 60/min · 写 30/min · 读 120/min），覆盖 29 个路由。
]
#bullet-item[
  #bold-dark[并发安全与性能优化：]独立定位并修复 3 个生产级并发 Bug——LRU 有序字典竞态（引入 RLock + 双重检查锁定）、SQLite WAL 模式缺失（journal\_mode=WAL + busy\_timeout=5000ms）、幂等 TOCTOU 问题（IntegrityError 捕获 + 回滚重查）。编写 18 个并发测试 + 4 个负载测试，P95 响应延迟 < 2s。通过 pytest-xdist 并行化实现测试套件 5 倍加速（63s → 13s）。
]
#bullet-item[
  #bold-dark[安全纵深防御：]实现五层安全模型——RBAC 角色权限位图、JWT OTP 客户验证（SHA256 哈希 + 10 分钟过期）、订单级与客户级作用域令牌、SHA256 请求指纹幂等写入保护、审计事件全链路记录。配合 Docker Secrets 生产密钥管理，17 个安全测试用例验证通过。
]
#bullet-item[
  #bold-dark[CI/CD 与质量保障：]搭建 GitHub Actions 自动化流水线——ruff 代码规范检查 + mypy 类型检查（零错误）+ pip-audit 依赖安全审计 + Python 3.10/3.11/3.12 测试矩阵 + MySQL 8.4 迁移冒烟测试 + GHCR 镜像构建推送。84 个测试用例全部通过，分支覆盖率 76%，经历 5 个迭代阶段（P1–P5）从核心功能到生产加固的系统性演进。
]

#block(
  fill: light-bg,
  inset: (x: 0.6em, y: 0.15em),
  radius: 3pt,
)[#text(size: 7.5pt, fill: muted)[
  #bold-dark[技术栈：]Python 3.10+ · FastAPI · FastMCP · SQLAlchemy 2.0 · Alembic · MySQL 8.4 · SQLite · Docker · Prometheus · Grafana · Alertmanager · structlog · slowapi · pytest · pytest-xdist · GitHub Actions · sentence-transformers · Claude Code
]]

v(0.12em)

// ============ 竞赛与奖项 ============
#section-title[竞赛与奖项]

#bullet-item[
  #bold-dark[全国大学生数学建模大赛] 广东赛区优胜奖 #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.09 | 建模手 + 编程手]
]
#bullet-item[
  #bold-dark[全国大学生统计建模大赛] 广东赛区三等奖 #h(0.3em) #text(size: 7.5pt, fill: muted)[2026.03–05 | 数据筛选与建模分析]
]
#bullet-item[
  #bold-dark[全国大学生数学竞赛] 广东赛区二等奖 #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.11]
]
#bullet-item[
  #bold-dark[全国大学生市场调研大赛] 广东赛区二等奖 #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.11–2026.04 | 项目策划与数据分析]
]
#bullet-item[
  #bold-dark[百千万工程突击队] 省级多彩乡村调研报告二等奖 #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.04–10 | 问卷收集与问卷分析]
]

v(0.12em)

// ============ 学术论文 ============
#section-title[学术论文]
#bullet-item[
  #bold-dark[教育人工智能的伦理框架、认知现状及治理策略] —— 数据采集、筛选与分析，已发表于中国知网 #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.12–2026.04]
]

v(0.12em)

// ============ 教育背景 ============
#section-title[教育背景]
#text(size: 8.5pt)[
  #bold-dark[广东工业大学] · 数学与应用数学 · 本科 #h(0.5em) #text(size: 7.5pt, fill: muted)[2024.09 – 2028.07]
]
#text(size: 8pt, fill: muted)[
  GPA 3.44/4.0 · 大学英语四级 · 2025 年校级三等奖学金
]
#text(size: 8pt, fill: muted)[
  核心课程：Python 语言设计 · 数据结构与算法 · 概率论与数理统计 · 随机过程 · 数值分析 · 数学建模
]

v(0.12em)

// ============ 学生工作 ============
#section-title[学生工作]
#bullet-item[
  #bold-dark[团支部书记] #h(0.3em) #text(size: 7.5pt, fill: muted)[2024.09 至今] —— 荣获学校百优团支部、学校班级之星
]
#bullet-item[
  #bold-dark[百千万工程突击队队长] #h(0.3em) #text(size: 7.5pt, fill: muted)[2025.04–10] —— 校级优秀实践队伍
]