# AgentGuard

AgentGuard 是一个用于学习和迭代 AI Agent 安全执行能力的小型沙箱控制面。

当前实现的是阶段 1：先跑通最小可用闭环。

```text
创建沙箱 -> 解析 execd 地址 -> 执行命令 -> 删除沙箱
```

## 组件

- `agentguard.server.app`：FastAPI 服务入口。
- `agentguard.server.api.lifecycle`：沙箱生命周期 API，负责创建、查询、删除沙箱，以及解析沙箱内服务地址。
- `agentguard.server.sandbox.docker`：基于 Docker 的沙箱生命周期实现。
- `agentguard.execd.server`：运行在沙箱容器里的小型 HTTP 服务，负责执行 shell 命令。
- `agentguard.sdk.client`：最小 async Python 客户端。

## 目录结构

当前目录按 OpenSandbox 风格拆成三块：

```text
src/agentguard/
  server/        # 控制面：FastAPI API、Docker 生命周期实现、旧 debug executor
  execd/         # 沙箱内组件：接收 /command 并在容器内执行命令
  sdk/           # 用户侧 Python 客户端

  gateway/       # AgentGuard 原有工具网关，后续接审批/审计
  policy/        # AgentGuard 原有策略模块
  approval/      # 预留：审批
  audit/         # 预留：审计
  network/       # 预留：网络/egress
  workspace/     # 预留：工作区和回滚
```

`agentguard.main` 仍然保留为兼容入口，实际 app 来自 `agentguard.server.app`。

## 构建沙箱镜像

```bash
docker build -f docker/sandbox.Dockerfile -t agentguard-sandbox:latest .
```

## 启动 Server

```bash
uv run agentguard
```

服务默认监听：

```text
http://127.0.0.1:8000
```

## 使用 Python 客户端

```python
import asyncio

from agentguard.sdk.client import AgentGuardClient


async def main() -> None:
    client = AgentGuardClient("http://127.0.0.1:8000")
    sandbox = await client.create_sandbox()
    try:
        result = await sandbox.commands.run("echo hi")
        print(result.stdout)
    finally:
        await sandbox.kill()


asyncio.run(main())
```

这段代码会：

```text
1. 请求 AgentGuard Server 创建一个 Docker 沙箱
2. 解析这个沙箱里的 execd 地址
3. 通过 execd 执行 echo hi
4. 最后删除沙箱
```

## 直接调用 HTTP API

创建沙箱：

```bash
curl -X POST http://127.0.0.1:8000/v1/sandboxes \
  -H 'Content-Type: application/json' \
  -d '{}'
```

解析 execd 地址：

```bash
curl http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/endpoints/44772
```

直接请求 execd 执行命令：

```bash
curl -X POST http://<execd-endpoint>/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"echo hi"}'
```

删除沙箱：

```bash
curl -X DELETE http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>
```

## 当前阶段的边界

阶段 1 只实现最小闭环：

- 支持 Docker runtime。
- 支持创建和删除持久沙箱容器。
- 支持解析沙箱内 `execd` 的 `44772` 端口。
- 支持通过 `POST /command` 执行 shell 命令。
- 支持最小 Python client。

暂时还没有实现：

- 文件 API。
- 流式输出。
- ingress。
- egress。
- Kubernetes。
- 审批、审计、回滚。
