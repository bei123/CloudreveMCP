"""
项目根目录启动脚本。

  uv run python run.py
  python run.py   # 需先 uv sync 或 pip install -e .

端口与主机由环境变量 PORT、HOST 控制，默认 3001 / 127.0.0.1。
"""

import os

os.environ.setdefault("PORT", "3001")
os.environ.setdefault("MCP_PORT", os.environ["PORT"])

from mcp_cloudreve.main import main

if __name__ == "__main__":
    main()
