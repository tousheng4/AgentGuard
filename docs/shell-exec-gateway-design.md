# 受控 Shell Exec 网关设计

## 目标

为 AgentGuard 增加第一条正式的 `shell.exec` 工具调用链路。

当前的 `/debug/sandbox/run` 接口会直接把命令交给沙箱执行器。它适合调试，但还不是计划中的 AgentGuard 正式执行流程。下一步应该加入一个很薄但结构正确的网关层：

```text
HTTP 请求 -> Tool Gateway -> Action -> Policy Engine -> 沙箱执行或受控返回
```

这一版刻意不实现审批队列、审计日志、工作区快照和回滚，避免 MVP 一开始就变重。

## 范围

这一版新增：

- `ShellExecAction`：规范化后的命令执行 Action 模型。
- `PolicyDecision`：包含 `ALLOW`、`DENY`、`REQUIRE_APPROVAL` 三种策略结果。
- 一个进程内的简单 Shell 策略引擎。
- 一个 Gateway 服务，在执行前先做策略判断。
- 一个正式 API：`POST /tools/shell/exec`。

这一版不新增：

- 真实用户审批存储。
- SQLite 审计表。
- 工作区挂载。
- 文件工具。
- 网络工具。
- 回滚能力。

## API 行为

请求示例：

```json
{
  "argv": ["python3", "-c", "print('hello')"],
  "cwd": "/workspace",
  "timeout_seconds": 10
}
```

允许执行时返回：

```json
{
  "status": "executed",
  "decision": {
    "effect": "ALLOW",
    "risk_level": "low",
    "rule_id": "shell.allowlisted_command",
    "reason": "Command is allowlisted for MVP execution."
  },
  "result": {
    "exit_code": 0,
    "stdout": "hello\n",
    "stderr": ""
  }
}
```

拒绝执行时返回：

```json
{
  "status": "denied",
  "decision": {
    "effect": "DENY",
    "risk_level": "critical",
    "rule_id": "shell.dangerous_command",
    "reason": "Command is explicitly blocked."
  },
  "result": null
}
```

需要审批时返回：

```json
{
  "status": "requires_approval",
  "decision": {
    "effect": "REQUIRE_APPROVAL",
    "risk_level": "medium",
    "rule_id": "shell.default_requires_approval",
    "reason": "Command is not allowlisted for automatic execution."
  },
  "result": null
}
```

## 初始策略

MVP 策略先保持保守和确定：

- 空 `argv` 由 Pydantic 校验直接拒绝。
- 可执行文件名为 `sudo`、`su`、`docker`、`kubectl`、`chmod`、`chown`、`mkfs`、`mount`、`umount` 的命令直接拒绝。
- 命令文本中包含高风险删除片段时直接拒绝，例如 `rm -rf /`、`rm -rf /*`、`rm -fr /`、`rm -fr /*`，以及 Windows 风格的根目录强制递归删除。
- 简单开发命令直接允许，例如 `echo`、`python`、`python3`、`pytest`。
- 其它命令统一返回 `REQUIRE_APPROVAL`。

这不是完整的 Shell 安全模型。它只是第一条明确的策略边界，后续可以在不改变公共流程的前提下，继续接入 YAML 策略、命令解析、审批和审计。

## 组件

`agentguard.gateway.models`

定义规范化 Action 和 API 响应模型。

`agentguard.policy.models`

定义策略枚举和策略决策数据结构。

`agentguard.policy.shell`

实现第一版 Shell 策略。这个模块不依赖 Docker，可以快速做单元测试。

`agentguard.gateway.shell`

协调策略判断和沙箱执行。

`agentguard.api.tools`

暴露 `POST /tools/shell/exec`。

## 测试

单元测试覆盖：

- 白名单命令会调用 executor，并返回 `executed`。
- 拒绝命令不会调用 executor，并返回 `denied`。
- 未知命令不会调用 executor，并返回 `requires_approval`。
- `/health` 仍然可以在没有 Docker daemon 的情况下导入和测试。

真实 Docker 执行留给后续集成测试处理。
