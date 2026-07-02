# 修复 mypy 12 个存量类型错误

## 摘要

CI 中 mypy 以 `continue-on-error: true` 运行(非阻断)。本计划修复全部 12 个错误,使 mypy 可以转为阻断模式。所有修复均为类型标注或局部变量提取,不改变运行时行为。

## 错误清单与修复方案

### 1. `kb_service.py:48` — `None.strip()` union-attr

**原因**:`backend or os.getenv("FAQ_RAG_BACKEND", DEFAULT_BACKEND)` 中,mypy 认为 `os.getenv` 可能返回 `None`。
**修复**:提取局部变量,用 `or DEFAULT_BACKEND` 兜底:
```python
raw_backend = backend or os.getenv("FAQ_RAG_BACKEND") or DEFAULT_BACKEND
self.backend = raw_backend.strip().lower()
```

### 2. `seed_data.py:360` — RETURN_SEEDS 缺类型标注

**原因**:`RETURN_SEEDS = []` 空列表无法推断元素类型。
**修复**:添加标注 `RETURN_SEEDS: list[tuple] = []`

### 3. `seed_data.py:491` — None 赋值给 str 变量

**原因**:`TICKET_SEEDS` 元组中 `order_id` 为 `None`(如第 306 行),但其他元组中为 `int`。mypy 推断 `order_id` 为 `int`,赋值 `None` 报错。
**修复**:解包时添加类型标注 `order_id: int | None`

### 4. `seed_data.py:523` — None 赋值给 str 变量

**原因**:`SURVEY_SEEDS` 中 `order_id` 全为 `None`,mypy 推断为 `None` 类型,但赋值给变量时与 `str` 不兼容。
**修复**:解包时添加类型标注 `order_id: int | None`

### 5. `analytics_service.py:211` — dict / int 操作符错误

**原因**:`usage` 字典值类型为 `int | dict[str, int]` 混合,`usage["needs_human_count"]` 被推断为该联合类型,无法执行 `/` 操作。
**修复**:在构建 `usage` 前提取局部变量 `needs_human_count`,直接用变量做除法。

### 6. `analytics_service.py:256` — max key 类型错误

**原因**:`max(top_agents, key=top_agents.get)` 中 `dict.get` 返回 `int | None`,mypy 不接受。
**修复**:改为 lambda:`max(top_agents, key=lambda k: top_agents.get(k, 0))`

### 7. `security.py:72` — HTTPException detail 类型不匹配

**原因**:Starlette `HTTPException.detail` 类型存根声明为 `str | None`,实际接受任意可序列化值。传入 `dict` 报错。
**修复**:`from typing import cast, Any`,用 `cast(Any, {...})` 包装 detail 值。

### 8-12. `server_customer.py` 5 处 — params 字典混合类型

**原因**:`params = {"title": title, ...}` 推断为 `dict[str, str]`,后续 `params["customer_id"] = customer_id`(int)报错。同理 `{"limit": limit, "offset": offset}` 推断为 `dict[str, int]`,后续 `params["status"] = status`(str)报错。
**涉及行**:210、260、262、372、421
**修复**:4 处 `params` 声明改为 `params: dict[str, Any] = {...}`(行 203、258、366、419 对应的 `params =`)

## 具体文件变更

| 文件 | 变更 |
|------|------|
| `src/kb_service.py` | 第 48 行:提取 `raw_backend` 局部变量 |
| `src/seed_data.py` | 第 360 行:添加 `list[tuple]` 标注;第 491、523 行:添加 `order_id: int \| None` |
| `src/analytics_service.py` | 第 187 行附近:提取 `needs_human_count` 变量;第 211 行:用变量替代字典索引;第 256 行:改 lambda |
| `src/security.py` | 第 72 行:`cast(Any, {...})` 包装 detail |
| `src/server_customer.py` | 4 处 `params =` 改为 `params: dict[str, Any] =` |

## 验证步骤

1. `.\.venv\Scripts\mypy.exe src` → 0 errors
2. `.\.venv\Scripts\ruff.exe check src tests` → All checks passed
3. `.\.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider` → 全量通过
4. 将 `ci.yml` 中 mypy 步骤的 `continue-on-error: true` 移除
