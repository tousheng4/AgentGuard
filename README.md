# AgentGuard

AgentGuard 是一个用于学习和迭代 AI Agent 安全执行能力的小型沙箱控制面。

当前已完成 Docker 生命周期、文件能力和 SSE 命令执行主链。

```text
创建沙箱 -> 写入代码 -> 执行代码 -> 读取结果 -> 删除沙箱
```

## 组件

- `agentguard.server.app`：FastAPI 服务入口。
- `agentguard.server.api.lifecycle`：薄 HTTP 适配层，负责参数解析和领域错误映射。
- `agentguard.server.sandbox.service`：与具体后端无关的 `SandboxRuntime` 契约。
- `agentguard.server.sandbox.factory`：根据配置创建 Docker 等 runtime 后端。
- `agentguard.server.sandbox.docker`：Docker runtime，负责生命周期、状态、端口和过期回收。
- `agentguard.server.sandbox.expiration`：与后端无关的过期调度和持久化。
- `agentguard.server.sandbox.injector`：在目标 Python 镜像启动前注入 execd payload 和 bootstrap。
- `agentguard.execd.server`：运行在沙箱容器里的小型 HTTP 服务，负责执行命令和文件操作。
- `agentguard.sdk.client`：async Python 客户端，提供 `commands` 和 `files` 服务。

## 目录结构

当前目录按 OpenSandbox 风格拆成三块：

```text
src/agentguard/
  server/        # 控制面：FastAPI API、Docker 生命周期实现、旧 debug executor
  execd/         # 沙箱内组件：执行命令和处理文件
  sdk/           # 用户侧 Python 客户端

  gateway/       # AgentGuard 原有工具网关，后续接审批/审计
  policy/        # AgentGuard 原有策略模块
  approval/      # 预留：审批
  audit/         # 预留：审计
  network/       # 预留：网络/egress
  workspace/     # 预留：工作区和回滚
```

`agentguard.main` 仍然保留为兼容入口，实际 app 来自 `agentguard.server.app`。

## Runtime 配置

Server 启动时通过 runtime factory 创建后端，并由 FastAPI lifespan 管理其生命周期。
当前支持的后端是 `docker`：

```bash
export AGENTGUARD_RUNTIME__TYPE=docker
export AGENTGUARD_DOCKER__IMAGE=agentguard-sandbox:latest
export AGENTGUARD_DOCKER__DATA_DIR=data
export AGENTGUARD_DOCKER__EXECD_READY_TIMEOUT_SECONDS=5
export AGENTGUARD_DOCKER__BIND_HOST=127.0.0.1
```

未配置时使用上述默认值。生命周期 API、工具网关和 Python SDK 不依赖具体后端；
后续新增 runtime 只需实现 `SandboxRuntime`，按需实现 `SandboxCommandRunner`，
并通过 `register_runtime()` 接入 factory。

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

from agentguard.sdk import AgentGuardClient, ExecutionHandlers, OutputMessage


async def print_stdout(message: OutputMessage) -> None:
    print(message.text, end="")


async def main() -> None:
    client = AgentGuardClient("http://127.0.0.1:8000")
    sandbox = await client.create_sandbox(
        "python:3.12-slim",
        timeout_seconds=1800,
        metadata={"task": "example"},
    )
    try:
        await sandbox.files.write_file(
            "/workspace/main.py",
            "print(1 + 1)",
        )
        execution = await sandbox.commands.run(
            "python -u /workspace/main.py",
            handlers=ExecutionHandlers(on_stdout=print_stdout),
        )
        print(f"exit code: {execution.exit_code}")
    finally:
        await sandbox.kill()


asyncio.run(main())
```

这段代码会：

```text
1. 请求 AgentGuard Server 创建一个 Docker 沙箱
2. 解析这个沙箱里的 execd 地址
3. 通过 execd 写入 `/workspace/main.py`
4. 在沙箱中执行 Python 文件，并通过 SSE 实时接收 stdout/stderr
5. 输出 `2`，SDK 同时将事件累计到 `execution.logs`
6. 最后删除沙箱
```

## 直接调用 HTTP API

创建沙箱：

```bash
curl -X POST http://127.0.0.1:8000/v1/sandboxes \
  -H 'Content-Type: application/json' \
  -d '{
    "image":"python:3.12-slim",
    "entrypoint":["tail","-f","/dev/null"],
    "timeout_seconds":1800,
    "metadata":{"task":"example"},
    "exposed_ports":[8080]
  }'
```

Server 会先创建但不启动目标容器，注入 `/opt/agentguard` 和 bootstrap，
再同时启动用户 entrypoint 与 execd。当前 runtime 注入要求目标镜像包含
`/bin/sh`，以及 Python 3.11+（命令名为 `python3` 或 `python`）。

列出、暂停、恢复和续期：

```bash
curl http://127.0.0.1:8000/v1/sandboxes

curl -X POST http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/pause
curl -X POST http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/resume

curl -X POST \
  http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/renew-expiration \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds":3600}'
```

解析 execd 或用户服务地址：

```bash
curl http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/endpoints/44772
curl http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>/endpoints/8080
```

直接请求 execd 执行命令：

```bash
curl -X POST http://<execd-endpoint>/command \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"command":"echo hi"}'
```

`POST /command` 只返回 SSE，不再返回单个 JSON 结果。事件包括：

- `init`：命令 execution ID。
- `stdout`：标准输出。
- `stderr`：标准错误。
- `error`：启动失败、非零退出或超时。
- `execution_complete`：命令成功完成。
- `ping`：长时间无输出时保持连接。

不需要实时回调时可以省略 `handlers`，SDK 仍会消费 SSE 并累计输出：

```python
execution = await sandbox.commands.run("echo hello")

print(execution.text)
print(execution.logs.stderr)
print(execution.exit_code)
```

写入和读取文本文件：

```bash
curl -X POST http://<execd-endpoint>/files/write \
  -H 'Content-Type: application/json' \
  -d '{"path":"/workspace/main.py","content":"print(123)"}'

curl --get http://<execd-endpoint>/files/read \
  --data-urlencode 'path=/workspace/main.py'
```

上传、下载和列出目录：

```bash
curl -X POST http://<execd-endpoint>/files/upload \
  -F 'metadata={"path":"/workspace/input.bin"};type=application/json' \
  -F 'file=@./input.bin;type=application/octet-stream'

curl --get http://<execd-endpoint>/files/download \
  --data-urlencode 'path=/workspace/input.bin' \
  --output input.bin

curl --get http://<execd-endpoint>/directories/list \
  --data-urlencode 'path=/workspace'
```

删除沙箱：

```bash
curl -X DELETE http://127.0.0.1:8000/v1/sandboxes/<sandbox-id>
```

## 文件能力与安全边界

- `write_file` 和 `read_file` 用于 UTF-8 等编码的文本内容。
- `upload_file` 和 `download_file` 用于原始二进制内容。
- execd 只允许访问 `/workspace` 目录树。
- 文件路径必须是绝对路径。
- 路径穿越和通过符号链接逃逸工作区会被拒绝。
- 单个 HTTP 请求体当前限制为 32 MiB。

## 当前阶段的边界

- 支持通过配置和 factory 选择 runtime，当前实现为 Docker。
- `/tools/shell/exec` 和 debug one-shot 执行复用应用级 runtime，不再单独连接 Docker。
- 支持在 Python 基础镜像中自动注入 execd runtime。
- 支持创建、查询、列表、删除、暂停和恢复容器。
- 支持 metadata 过滤和分页。
- 支持自动过期、续期和 Server 重启后的计时器恢复。
- 支持 execd 和声明过的用户服务端口。
- 支持 CPU、内存和 PID 资源上限。
- 支持通过 `POST /command` 执行 shell 命令。
- 支持文件写入、读取、上传、下载和目录列表。
- 支持 async Python SDK 的 `sandbox.commands` 和 `sandbox.files`。
- 支持基于 SSE 的实时 stdout/stderr 命令执行。

当前限制和暂未实现：

- runtime 注入目前只支持带 `/bin/sh` 和 Python 3.11+ 的 Linux 镜像；尚不是
  OpenSandbox 那种可注入任意 glibc 镜像的独立 Go 二进制。
- 大文件分块上传和 HTTP Range 下载。
- 后台命令、显式 interrupt 和命令状态查询。
- ingress。
- egress。
- Kubernetes。
- 审批、审计、回滚。
