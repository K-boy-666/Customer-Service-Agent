"""
Git Agent MCP Server — Git 版本控制 MCP 服务端
暴露 13 个工具给 Claude Code 调用，绕过 Agent 工具的 reasoning_effort 限制。
内置安全拦截层，拒绝高危 Git 操作。
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── 工作目录 ──────────────────────────────────────────────────
WORK_DIR = Path(__file__).resolve().parent.parent

# ── 安全拦截 ──────────────────────────────────────────────────

FORBIDDEN_PATTERNS = [
    (r"reset\s+.*--hard",            "git reset --hard 不可逆地丢弃工作区改动。替代：git stash 暂存后再 git reset --soft"),
    (r"push\s+.*((-f(?![a-z]))|--force(?!-with-lease))", "禁止 git push --force。替代：git push --force-with-lease（安全强制推送）"),
    (r"branch\s+-D\s+(main|master)",  "禁止删除主分支（main/master）"),
    (r"clean\s+-[a-z]*[dfx]",        "禁止清空未追踪文件。替代：git clean -n 先预览影响范围"),
    (r"rm\s+.*-rf.*\.git",            "禁止删除 .git 目录（销毁仓库）"),
    (r"push\s+.*--delete.*(main|master)", "禁止删除远程主分支"),
]


def safety_check(cmd: list[str]) -> tuple[bool, str]:
    """检查命令是否命中安全拦截规则。返回 (是否被拦截, 拦截原因)"""
    cmd_str = " ".join(cmd)
    for pattern, reason in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd_str):
            return True, f"⛔ 安全拦截：{reason}\n被拦截命令：{cmd_str}"
    return False, ""


def run_git(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """在项目根目录执行 git 命令。返回 (returncode, stdout, stderr)"""
    # 安全拦截
    blocked, reason = safety_check(args)
    if blocked:
        return 1, "", reason

    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(WORK_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "命令执行超时（30s）"
    except FileNotFoundError:
        return 1, "", "未找到 git 命令，请确认已安装 Git"


def ok(data=None, **kwargs) -> dict:
    """构造成功响应"""
    result = {"success": True}
    if data is not None:
        result["data"] = data
    result.update(kwargs)
    return result


def fail(error: str, data=None) -> dict:
    """构造失败响应"""
    result = {"success": False, "error": error}
    if data is not None:
        result["data"] = data
    return result


# ── 工具实现 ──────────────────────────────────────────────────

def handle_git_status() -> dict:
    """获取工作区状态"""
    rc, out, err = run_git(["status", "--short", "--branch"])
    if rc != 0:
        return fail(err or "git status 执行失败")
    return ok({"status_output": out})


def handle_git_log(count: int = 10, oneline: bool = True) -> dict:
    """获取提交历史"""
    args = ["log", f"-{max(1, min(count, 50))}"]
    if oneline:
        args.append("--oneline")
    args.extend(["--decorate", "--graph"])
    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or "git log 执行失败")
    return ok({"commits": out})


def handle_git_diff(staged: bool = False, path: str = "") -> dict:
    """获取差异对比"""
    args = ["diff"]
    if staged:
        args.append("--staged")
    if path:
        args.extend(["--", path])
    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or "git diff 执行失败")
    return ok({"diff": out[:8000] if len(out) > 8000 else out, "truncated": len(out) > 8000})


def handle_git_branch_list() -> dict:
    """获取分支列表"""
    rc, out, err = run_git(["branch", "-a", "-v", "-v"])
    if rc != 0:
        return fail(err or "git branch 执行失败")
    # 同时获取当前分支名
    rc2, current, _ = run_git(["branch", "--show-current"])
    return ok({"branches": out, "current": current if rc2 == 0 else ""})


def handle_git_remote_info() -> dict:
    """获取远程仓库信息"""
    rc, out, err = run_git(["remote", "-v"])
    if rc != 0:
        return fail(err or "git remote 执行失败")
    return ok({"remotes": out})


def handle_git_conflict_files() -> dict:
    """获取冲突文件列表"""
    rc, out, err = run_git(["diff", "--name-only", "--diff-filter=U"])
    files = [f for f in out.split("\n") if f.strip()] if rc == 0 else []
    return ok({
        "conflict_files": files,
        "has_conflicts": len(files) > 0,
        "hint": "使用 git diff [file] 查看具体冲突内容；git merge --abort / git rebase --abort 中止操作"
    })


def handle_git_stage(files: list[str]) -> dict:
    """暂存文件 (git add)"""
    if not files:
        return fail("未指定要暂存的文件")
    rc, out, err = run_git(["add", "--"] + files)
    if rc != 0:
        return fail(err or "git add 失败")
    return ok({"staged_files": files})


def handle_git_commit(message: str) -> dict:
    """提交 (git commit)"""
    if not message or not message.strip():
        return fail("commit message 不能为空")
    rc, out, err = run_git(["commit", "-m", message.strip()])
    if rc != 0:
        return fail(err or "git commit 失败")
    # 获取当前 commit hash
    rc2, hash_out, _ = run_git(["rev-parse", "--short", "HEAD"])
    return ok({"commit": hash_out if rc2 == 0 else "", "output": out})


def handle_git_push(branch: str = "", force_with_lease: bool = False) -> dict:
    """推送到远程 (git push)"""
    args = ["push"]
    if force_with_lease:
        args.append("--force-with-lease")
    if branch:
        args.append(branch)
    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or "git push 失败")
    return ok({"output": out})


def handle_git_pull(rebase: bool = False) -> dict:
    """从远程拉取 (git pull)"""
    args = ["pull"]
    if rebase:
        args.append("--rebase")
    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or "git pull 失败", {"hint": "可能存在冲突，用 git_conflict_files 查看冲突文件"})
    return ok({"output": out})


def handle_git_fetch() -> dict:
    """获取远程分支信息 (git fetch)"""
    rc, out, err = run_git(["fetch", "--all", "--prune"])
    if rc != 0:
        return fail(err or "git fetch 失败")
    return ok({"output": out or "已同步远程分支信息"})


def handle_git_branch_op(action: str, name: str) -> dict:
    """分支操作 (create/switch/delete)"""
    if not name:
        return fail("未指定分支名称")

    valid_actions = {"create", "switch", "delete"}
    if action not in valid_actions:
        return fail(f"无效操作 '{action}'，支持：{', '.join(sorted(valid_actions))}")

    if action == "create":
        args = ["checkout", "-b", name]
    elif action == "switch":
        args = ["switch", name]
    elif action == "delete":
        # 额外安全检查：不允许删除主分支
        if name.lower() in ("main", "master"):
            return fail("禁止删除主分支")
        args = ["branch", "-d", name]  # 用 -d 而非 -D，避免强制删除

    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or f"git branch {action} 失败")
    return ok({"output": out})


def handle_git_stash(action: str = "push") -> dict:
    """暂存操作 (push/pop/list/apply)"""
    valid_actions = {"push", "pop", "list", "apply"}
    if action not in valid_actions:
        return fail(f"无效操作 '{action}'，支持：{', '.join(sorted(valid_actions))}")

    args = ["stash"]
    if action == "push":
        pass  # git stash
    elif action == "pop":
        args.append("pop")
    elif action == "list":
        args.append("list")
    elif action == "apply":
        args.append("apply")

    rc, out, err = run_git(args)
    if rc != 0:
        return fail(err or f"git stash {action} 失败")
    return ok({"output": out or "stash 操作完成"})


# ── 工具名 → 处理函数映射 ───────────────────────────────────────

TOOL_HANDLERS = {
    "git_status":        lambda a: handle_git_status(),
    "git_log":           lambda a: handle_git_log(
                            count=a.get("count", 10), oneline=a.get("oneline", True)),
    "git_diff":          lambda a: handle_git_diff(
                            staged=a.get("staged", False), path=a.get("path", "")),
    "git_branch_list":   lambda a: handle_git_branch_list(),
    "git_remote_info":   lambda a: handle_git_remote_info(),
    "git_conflict_files": lambda a: handle_git_conflict_files(),
    "git_stage":         lambda a: handle_git_stage(a.get("files", [])),
    "git_commit":        lambda a: handle_git_commit(a.get("message", "")),
    "git_push":          lambda a: handle_git_push(
                            branch=a.get("branch", ""),
                            force_with_lease=a.get("force_with_lease", False)),
    "git_pull":          lambda a: handle_git_pull(rebase=a.get("rebase", False)),
    "git_fetch":         lambda a: handle_git_fetch(),
    "git_branch_op":     lambda a: handle_git_branch_op(
                            action=a.get("action", ""), name=a.get("name", "")),
    "git_stash":         lambda a: handle_git_stash(action=a.get("action", "push")),
}

# ── MCP 应用 ──────────────────────────────────────────────────

app = Server("git-agent-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="git_status",
            description="查看 Git 仓库状态（分支、未暂存/已暂存改动）",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="git_log",
            description="查看 Git 提交历史",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "显示的 commit 数量，默认 10，最大 50"},
                    "oneline": {"type": "boolean", "description": "是否单行摘要模式，默认 true"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_diff",
            description="查看工作区或暂存区代码差异",
            inputSchema={
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "true 查看暂存区差异，默认 false 查看工作区差异"},
                    "path": {"type": "string", "description": "可选，限定到指定文件路径"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_branch_list",
            description="列出所有本地和远程分支，标注当前分支",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="git_remote_info",
            description="查看远程仓库地址（fetch/push URL）",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="git_conflict_files",
            description="列出当前冲突文件，提供解决提示",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="git_stage",
            description="暂存指定文件（git add）",
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要暂存的文件路径列表，如 ['src/app.py', 'README.md']",
                    },
                },
                "required": ["files"],
            },
        ),
        Tool(
            name="git_commit",
            description="提交暂存区改动（git commit）",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "commit 信息（Conventional Commits 规范）"},
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="git_push",
            description="推送本地提交到远程仓库。force push 需设置 force_with_lease=true，--force 被禁止。",
            inputSchema={
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "推送的目标分支（可选）"},
                    "force_with_lease": {"type": "boolean", "description": "使用 --force-with-lease（安全强制推送），默认 false"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_pull",
            description="从远程仓库拉取最新代码",
            inputSchema={
                "type": "object",
                "properties": {
                    "rebase": {"type": "boolean", "description": "使用 --rebase 模式拉取，默认 false"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_fetch",
            description="获取远程分支信息（不合并）",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="git_branch_op",
            description="分支操作：创建、切换、删除分支",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作类型：create（创建并切换）、switch（切换）、delete（安全删除）"},
                    "name": {"type": "string", "description": "分支名称"},
                },
                "required": ["action", "name"],
            },
        ),
        Tool(
            name="git_stash",
            description="暂存/恢复工作区改动（git stash）",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作类型：push（暂存）、pop（恢复并删除）、list（列表）、apply（恢复保留）"},
                },
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = TOOL_HANDLERS.get(name)
    if handler:
        result = handler(arguments)
    else:
        result = fail(f"未知工具: {name}")

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


# ── 入口 ────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
