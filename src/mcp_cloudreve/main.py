"""
入口：使用 uv 或 uvx 启动 MCP 服务。

  uv run mcp-cloudreve
  uvx mcp-cloudreve
  python -m mcp_cloudreve

端口/主机由环境变量 PORT、HOST 控制（默认 3001 / 127.0.0.1），在导入 server 前生效。
"""

import os

os.environ.setdefault("PORT", "3001")
os.environ.setdefault("MCP_PORT", os.environ["PORT"])

from .server import mcp


def main() -> None:
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
