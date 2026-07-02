# ADR-0006: 密钥管理与部署模式

## 状态

已采纳 (2026-06-30)

## 背景

P1 生产部署加固需要确定密钥管理策略。项目使用 docker-compose 部署(非 K8s),已有 `.env.example` 文档化全部配置,`.claude/mcp.json` 已移除静态凭据。需要决定:

1. 生产密钥如何注入(纯 env vars vs Docker secrets vs Vault)
2. 密钥轮换流程
3. 部署 compose 模式

## 决策

采用 **env 优先 + `_FILE` 增强 + 文档化轮换** 模式:

- 敏感配置项(`AUTH_DEV_SECRET`、`DB_PASSWORD`)支持 `_FILE` 后缀读取文件内容(标准 Docker secret 模式)
- `_FILE` 未设时回退到普通 env var,不破坏现有 env-only 部署
- 不引入 Vault/Sealed Secrets —— 匹配 compose 部署规模,避免过度工程化
- 生产 compose 用 `docker-compose.prod.yml` overlay 叠加,dev compose 保持不动

### 密钥轮换 Runbook

| 密钥类型 | 轮换方式 | 影响 |
|----------|---------|------|
| JWT/OIDC 签名密钥 | JWKS 双 key 并存期 ≥ token TTL;本项仅消费 JWKS,无需重启 | 无中断 |
| AUTH_DEV_SECRET | 部署新 secret → 滚动重启所有实例 → 旧 token 失效 | OTP TTL 默认 10 分钟,低峰期操作 |
| MySQL 密码 | 双用户:CREATE 新用户→更新 secret→滚动重启→DROP 旧用户 | 无中断 |
| 备份加密 | `scripts/backup_mysql.py` 产出 gz 产物,落地到加密卷 | 按备份保留策略 |

## 理由

1. **增量兼容**:`_FILE` 后缀是 Docker secrets 的标准模式(Postgres、MySQL 均使用),不破坏现有 env-only 流程
2. **规模匹配**:compose 部署不需要 Vault 的动态密钥生成和租约管理
3. **可审计**:密钥来源为文件或 env,`/api/ready` 已检查 `oidc_jwks_configured`,配置校验阻止 dev 默认值进入生产

## 后果

- `config.py` 新增 `_read_secret()` 辅助函数,`security.py` 的 AUTH_DEV_SECRET 读取改为使用它
- `.env.example` 文档化所有 `_FILE` 后缀选项
- `docker-compose.prod.yml` 用 `secrets` 段引用外部 secret 文件
- 密钥轮换流程文档化在 `docs/production-hardening.md`
