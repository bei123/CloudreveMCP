# Cloudreve MCP Server

基于 MCP（Model Context Protocol）的 Cloudreve 工具服务端（Python），提供登录、上传、直链等能力，供 Cursor 等 MCP 客户端调用。使用 **uv** / **uvx** 启动。

### 项目结构（src 布局）

```
CloudreveMCP/
├── run.py              # 根目录启动脚本：uv run python run.py
├── pyproject.toml
├── README.md
├── .gitignore
└── src/
    └── mcp_cloudreve/
        ├── __init__.py
        ├── __main__.py      # python -m mcp_cloudreve
        ├── main.py          # 入口逻辑，mcp.run(sse)
        ├── server.py        # FastMCP 与工具注册
        └── cloudreve.py     # Cloudreve API 客户端
```

---

## 一、启动服务

需已安装 [uv](https://docs.astral.sh/uv/)（`pip install uv` 或见官网）。

在项目根目录执行（任选其一）：

```bash
# 方式一：根目录启动脚本（推荐）
uv run python run.py

# 方式二：命令行入口
uv run mcp-cloudreve

# 方式三：以模块方式运行
uv run python -m mcp_cloudreve
```

或使用 **uvx**（从 PyPI 拉取并运行，无需先 clone 本仓库时可用）：

```bash
uvx mcp-cloudreve
```

- 默认监听 **3001** 端口（避免与部分环境 8000 冲突）；可通过环境变量 `PORT`、`HOST` 修改。
- SSE 端点：**GET** `http://localhost:3001/sse` 建立 SSE 流；**POST** `http://localhost:3001/messages?session_id=xxx` 发送请求。平台 SSE 模板中的 **url** 填 `http://localhost:3001/sse`。

### 环境变量（可选）

| 变量 | 说明 |
|------|------|
| `PORT` | 服务端口，默认 `3001` |
| `HOST` | 监听地址，默认 `127.0.0.1` |
| `CLOUDREVE_BASE_URL` | Cloudreve API 根地址，默认 `https://cloudreve.2000gallery.art/api/v4` |

---

## 二、在平台中使用（含 SSE 模板）

### 1. 平台给的 SSE 模板

若平台要求填写「SSE」类型、并给出类似下面的模板，按下面方式填写即可：

```json
{
  "transport": "sse",
  "url": "http://localhost:3001/sse",
  "headers": {},
  "timeout": 5,
  "sse_read_timeout": 300
}
```

- **url**：填 `http://localhost:3001/sse`（端口与启动时一致，默认 3001）。
- **transport**：保持 `"sse"`。
- **headers**：一般留空 `{}` 即可；如需鉴权再按平台要求加。
- **timeout** / **sse_read_timeout**：可按平台建议保留或适当调大。

服务端行为：

- **GET** 你填的 `url`（即 `/sse`）→ 建立 SSE 流，返回会话 ID。
- **POST** 到 `/messages?session_id=xxx` → 发送 JSON-RPC（会话 ID 由 GET 阶段得到）。

### 2. 在 Cursor 中使用

1. 打开 Cursor 设置：**File → Preferences → Cursor Settings**（或 `Ctrl+,`），搜索 **MCP**。
2. 在 **MCP Servers** 里添加本服务，例如在配置文件中加入（具体路径以 Cursor 文档为准，可能是 `~/.cursor/mcp.json` 或项目内 `.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "cloudreve": {
      "url": "http://localhost:3001/sse"
    }
  }
}
```

3. 确保本仓库里的服务已用 `uv run mcp-cloudreve` 在本地跑起来（默认 `http://localhost:3001`）。
4. 重启 Cursor 或重新加载 MCP，使新配置生效。

### 3. 使用方式

- 在对话中让 AI 调用 Cloudreve 相关工具即可，例如：
  - **登录**：先让 AI 调用 `cloudreve_login`（邮箱、密码；若站点开验证码需先 `cloudreve_get_captcha`）。
  - **上传**：登录后让 AI 调用 `cloudreve_upload_file`（本地路径或 Base64 + 目标 URI + `policy_id`），上传完成后会自动尝试获取直链并返回。
  - **直链**：可单独调用 `cloudreve_create_direct_links`（传入文件 URI 列表）为已有文件创建直链。

- 工具列表（均在「先登录」前提下使用除验证码外的接口）：
  - `cloudreve_get_captcha` — 获取登录验证码（仅站点开启验证码时需要）
  - `cloudreve_login` — 密码登录，返回 `access_token`
  - `cloudreve_create_upload_session` — 创建上传会话
  - `cloudreve_upload_file_chunk` — 上传单个分块
  - `cloudreve_upload_file` — 上传整个文件（支持本地路径或 Base64），上传后自动获取直链
  - `cloudreve_create_direct_links` — 为指定文件 URI 创建直链
  - `echo` / `get_time` — 示例工具

---

## 三、协议说明

本服务使用 **SSE**（Server-Sent Events）传输：

- **GET** `http://localhost:3001/sse` → 建立 SSE 流，服务端返回会话 ID（在 SSE 事件中）。
- **POST** `http://localhost:3001/messages?session_id=<id>` → 发送 JSON-RPC 请求（Python MCP SDK 使用 `session_id` 查询参数）。

适合使用平台提供的「SSE」模板、只需填一个 `url` 的场景；`url` 填 `http://localhost:3001/sse` 即可。
