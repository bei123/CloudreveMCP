# Cloudreve MCP Server

基于 MCP（Model Context Protocol）的 Cloudreve 工具服务端（Python），供 Cursor 等 MCP 客户端调用。**推荐流程**：登入网盘 → 解析抖音/哔哩哔哩链接或搜索网易云音乐 → 下载视频/歌曲 → 上传到网盘（可返回直链）。使用 **uv** / **uvx** 启动。

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
        ├── cloudreve.py     # Cloudreve API 客户端
        ├── douyin.py        # 抖音分享链接解析与无水印下载
        ├── bilibili.py      # 哔哩哔哩 WBI 签名、DASH/durl 下载（[参考](https://github.com/bei123/astrbot_plugin_so_vits_svc/blob/master/bilibili_api.py)）
        └── netease.py       # 网易云音乐搜索、eapi 加密、获取播放链接与下载（[参考](https://github.com/bei123/astrbot_plugin_so_vits_svc/blob/master/netease_api.py)）
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
| `HOST` | 监听地址，默认 `0.0.0.0` |
| `CLOUDREVE_BASE_URL` | Cloudreve API 根地址，默认 `https://cloudreve.2000gallery.art/api/v4` |

**上传大文件若出现 413 Request Entity Too Large**：  
分块大小由 Cloudreve 创建会话时返回的 `chunk_size` 决定，客户端**必须**按该大小上传每个分块（不能改小），否则会报 Invalid Content-Length。413 表示**请求体超过了 Cloudreve 或反向代理（如 Nginx）的请求体上限**，需要由服务端/运维调大限制，本 MCP 无法绕过。

- **若你自建 Cloudreve 且前面有 Nginx**：在 Nginx 配置里（`http`、`server` 或 `location` 中）增加或修改：`client_max_body_size 200m;`（或更大，需 ≥ 单块大小），然后 `nginx -s reload`。
- **若使用他人提供的 Cloudreve（如 cloudreve.2000gallery.art）**：需联系站点管理员提高 API 或反向代理的请求体上限；或改用你自己能改 Nginx/配置的 Cloudreve 实例。

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

**MCP 推荐流程（抖音/哔哩哔哩视频进网盘）：**

1. **登入网盘**：调用 `cloudreve_login`（邮箱、密码；若站点开验证码需先 `cloudreve_get_captcha`），拿到 `access_token` 与 `refresh_token`。
2. **（可选）查存储策略**：调用 `cloudreve_list_storage_policies(access_token)`，取要用的策略 `id` 作为上传时的 `policy_id`。
3. **抖音链接 → 网盘**：`cloudreve_upload_douyin_video(access_token, douyin_share_link, policy_id, ...)`。流程：解析抖音分享链接 → 下载无水印视频到临时文件 → 创建/确认文件夹 → 上传 → 删临时文件 → 返回直链。
4. **哔哩哔哩链接 → 网盘**：`cloudreve_upload_bilibili_video(access_token, bilibili_share_link, policy_id, ..., cookie=...)`。流程：解析 BV 号 → 获取 WBI 签名与播放地址（DASH 或 durl）→ 下载到临时文件（DASH 会合并音视频，多段会合并）→ 创建/确认文件夹 → 上传 → 删临时文件 → 返回直链。**建议传 B 站 cookie**：未登录时画质通常只有 360p/480p，传入登录后的 cookie 可获取 1080p 等更高画质；需要登录才能看的视频也必须传 cookie。**需本机已安装 ffmpeg**（DASH 音视频合并、多段合并）。
5. **网易云音乐 → 网盘**：`cloudreve_upload_netease_song(access_token, keyword_or_song_id, policy_id, ...)`。**MCP 流程**：根据关键词或歌曲 ID 搜索/获取歌曲 → 获取最佳可用音质链接（无损/极高/标准）→ 下载到临时文件 → **将封面图（JPG）嵌入音频元数据（MP3 ID3 / FLAC picture）** → 创建/确认文件夹 → 上传 → 删临时文件 → 返回直链。可选传 `netease_cookie` 以获取更高音质（如无损）；返回中含 `cover_url` 供展示。

其他常用能力：

- **刷新令牌**：access_token 过期时可调用 `cloudreve_refresh_token(refresh_token)`，或在需要 token 的工具中传入 `refresh_token`，接口返回 401 时会自动刷新并重试。
- **上传本地/Base64 文件**：`cloudreve_upload_file`（本地路径或 Base64 + 目标 URI + `policy_id`），上传完成后会自动尝试获取直链。
- **直链**：`cloudreve_create_direct_links`（传入文件 URI 列表）为已有文件创建直链。
- **创建文件夹**：`cloudreve_create_folder(access_token, folder_uri)`，如 `cloudreve://my/douyin` 或 `cloudreve://douyin`（会自动补为 `cloudreve://my/douyin`）。

- 工具列表（均在「先登录」前提下使用除验证码外的接口）：
  - `cloudreve_get_captcha` — 获取登录验证码（仅站点开启验证码时需要）
  - `cloudreve_login` — 密码登录，返回 `access_token`、`refresh_token`
  - `cloudreve_refresh_token` — 使用 refresh_token 刷新，返回新的 access_token 与 refresh_token（[API 文档](https://cloudrevev4.apifox.cn/refresh-token-289504601e0)）
  - `cloudreve_list_storage_policies` — 获取当前用户可用的存储策略列表（上传时 policy_id 填返回的 id）（[API 文档](https://cloudrevev4.apifox.cn/list-available-storage-policies-308312707e0)）
  - `cloudreve_create_folder` — 在网盘创建文件夹（如 `cloudreve://my/douyin`），祖先目录不存在会自动创建（[API 文档](https://cloudrevev4.apifox.cn/create-file-300253321e0)）
  - `cloudreve_create_upload_session` — 创建上传会话（可传 `refresh_token` 以自动刷新）
  - `cloudreve_upload_file_chunk` — 上传单个分块（可传 `refresh_token` 以自动刷新）
  - `cloudreve_upload_file` — 上传整个文件（支持本地路径或 Base64），上传后自动获取直链（可传 `refresh_token` 以自动刷新）
  - `cloudreve_create_direct_links` — 为指定文件 URI 创建直链（可传 `refresh_token` 以自动刷新）
  - `cloudreve_upload_douyin_video` — 从抖音分享链接解析无水印视频、下载并上传到网盘，返回直链（可传 `folder_uri`、`refresh_token`、可选 `target_uri`）
  - `cloudreve_upload_bilibili_video` — 从哔哩哔哩链接解析 BV、下载视频（DASH/durl，需 ffmpeg）并上传到网盘，返回直链；**建议传 `cookie` 以获取高画质（1080p）**（可传 `folder_uri`、`refresh_token`、可选 `target_uri`）
  - `cloudreve_upload_netease_song` — **MCP 流程**：关键词/歌曲 ID → 获取最佳音质链接 → 下载 → **封面图嵌入音频元数据** → 上传网盘 → 返回直链；可选传 `netease_cookie` 以获取更高音质（可传 `folder_uri`、`refresh_token`、可选 `target_uri`）
  - `echo` / `get_time` — 示例工具

---

## 三、协议说明

本服务使用 **SSE**（Server-Sent Events）传输：

- **GET** `http://localhost:3001/sse` → 建立 SSE 流，服务端返回会话 ID（在 SSE 事件中）。
- **POST** `http://localhost:3001/messages?session_id=<id>` → 发送 JSON-RPC 请求（Python MCP SDK 使用 `session_id` 查询参数）。

适合使用平台提供的「SSE」模板、只需填一个 `url` 的场景；`url` 填 `http://localhost:3001/sse` 即可。


<audio id="audio" controls="" preload="none">
      <source id="mp3" src="https://cloudreve.2000gallery.art/f/Lvce/%E7%A6%BB%E5%BC%80%E6%88%91%E7%9A%84%E4%BE%9D%E8%B5%96%20-%20%E7%8E%8B%E8%89%B3%E8%96%87.mp3">
</audio>

<iframe frameborder="no" border="0" marginwidth="0" marginheight="0" width=330 height=86 src="//music.163.com/outchain/player?type=2&id=1488737309&auto=1&height=66"></iframe>
