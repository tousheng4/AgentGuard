# AgentGuard

AgentGuard 是一个用于学习和迭代 AI Agent 安全执行能力的小型沙箱控制面。

当前已完成阶段 2：在最小执行闭环上补齐文件读写、上传下载和目录列表。

```text
创建沙箱 -> 写入代码 -> 执行代码 -> 读取结果 -> 删除沙箱
```

## 组件

- `agentguard.server.app`：FastAPI 服务入口。
- `agentguard.server.api.lifecycle`：沙箱生命周期 API，负责创建、查询、删除沙箱，以及解析沙箱内服务地址。
- `agentguard.server.sandbox.docker`：基于 Docker 的沙箱生命周期实现。
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
        await sandbox.files.write_file(
            "/workspace/main.py",
            "print(1 + 1)",
        )
        result = await sandbox.commands.run("python /workspace/main.py")
        print(result.stdout)
    finally:
        await sandbox.kill()


asyncio.run(main())
```

这段代码会：

```text
1. 请求 AgentGuard Server 创建一个 Docker 沙箱
2. 解析这个沙箱里的 execd 地址
3. 通过 execd 写入 `/workspace/main.py`
4. 在沙箱中执行 Python 文件
5. 输出 `2`
6. 最后删除沙箱
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

- 支持 Docker runtime。
- 支持创建和删除持久沙箱容器。
- 支持解析沙箱内 `execd` 的 `44772` 端口。
- 支持通过 `POST /command` 执行 shell 命令。
- 支持文件写入、读取、上传、下载和目录列表。
- 支持 async Python SDK 的 `sandbox.commands` 和 `sandbox.files`。

暂时还没有实现：

- 任意基础镜像的 execd 自动注入。
- 大文件分块上传和 HTTP Range 下载。
- 流式输出。
- ingress。
- egress。
- Kubernetes。
- 审批、审计、回滚。
