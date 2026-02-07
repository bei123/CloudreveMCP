"""
MCP 服务：Cloudreve 登录、上传、直链等工具。
支持 SSE 传输（平台 SSE 模板：GET /sse 建流，POST /messages?session_id=xxx）。
"""

import base64
import json
import os
import time

from mcp.server.fastmcp import FastMCP

from . import cloudreve

NAME = "cloudreve-sse-mcp"

_host = os.environ.get("HOST", "0.0.0.0")
_port = int(os.environ.get("PORT", "3001"))

mcp = FastMCP(
    NAME,
    json_response=True,
    host=_host,
    port=_port,
)


# ----- 示例工具 -----
@mcp.tool()
def echo(message: str) -> str:
    """回显传入的文本"""
    return f"Echo: {message}"


@mcp.tool()
def get_time() -> str:
    """返回服务器当前时间"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ----- Cloudreve：验证码与登录 -----
@mcp.tool()
def cloudreve_get_captcha() -> str:
    """获取 Cloudreve 登录验证码。返回 base64 图片和 ticket。仅当站点开启验证码时需要。"""
    data = cloudreve.get_captcha()
    out = {
        "ticket": data["ticket"],
        "image_data_url": data["image"],
        "hint": "请将图片中的验证码文字识别出来，然后调用 cloudreve_login，传入 email、password、ticket、captcha。",
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def cloudreve_login(
    email: str,
    password: str,
    ticket: str = "",
    captcha: str = "",
) -> str:
    """使用邮箱和密码登录 Cloudreve。上传文件前必须先调用本工具获得 access_token。若站点未开启验证码，ticket 和 captcha 可留空。"""
    data = cloudreve.password_sign_in(email, password, ticket, captcha)
    token = data["token"]
    user = data.get("user") or {}
    out = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "access_expires": token.get("access_expires"),
        "refresh_expires": token.get("refresh_expires"),
        "user_id": user.get("id"),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


# ----- Cloudreve：上传会话与分块 -----
@mcp.tool()
def cloudreve_create_upload_session(
    access_token: str,
    uri: str,
    size: int,
    policy_id: str,
    last_modified: int | None = None,
    mime_type: str = "application/octet-stream",
) -> str:
    """创建 Cloudreve 文件上传会话。须先 cloudreve_login。返回 session_id、chunk_size 等。"""
    data = cloudreve.create_upload_session(
        access_token, uri, size, policy_id,
        last_modified=last_modified, mime_type=mime_type,
    )
    out = {
        "session_id": data["session_id"],
        "chunk_size": data["chunk_size"],
        "expires": data.get("expires"),
        "uri": data.get("uri"),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def cloudreve_upload_file_chunk(
    access_token: str,
    session_id: str,
    index: int,
    chunk_base64: str,
) -> str:
    """向已创建的上传会话上传一个分块。分块从 index=0 开始按顺序上传；chunk_base64 为该分块的 Base64。"""
    chunk = base64.b64decode(chunk_base64)
    cloudreve.upload_file_chunk(access_token, session_id, index, chunk)
    return f"分块 {index} 上传成功"


@mcp.tool()
def cloudreve_upload_file(
    access_token: str,
    target_uri: str,
    policy_id: str,
    file_path: str | None = None,
    file_base64: str | None = None,
    mime_type: str | None = None,
) -> str:
    """将本地文件或 Base64 内容上传到 Cloudreve。须先 cloudreve_login。可传 file_path 或 file_base64；上传完成后会自动尝试获取直链。"""
    if file_path:
        with open(file_path, "rb") as f:
            buffer = f.read()
    elif file_base64:
        buffer = base64.b64decode(file_base64)
    else:
        return json.dumps({"error": "必须提供 file_path 或 file_base64 之一"}, ensure_ascii=False)

    size = len(buffer)
    session = cloudreve.create_upload_session(
        access_token, target_uri, size, policy_id,
        mime_type=mime_type or "application/octet-stream",
    )
    chunk_size = session["chunk_size"]
    session_id = session["session_id"]
    index = 0
    for offset in range(0, size, chunk_size):
        end = min(offset + chunk_size, size)
        cloudreve.upload_file_chunk(access_token, session_id, index, buffer[offset:end])
        index += 1

    direct_link_text = ""
    try:
        links = cloudreve.create_direct_links(access_token, [target_uri])
        if links and links[0].get("link"):
            direct_link_text = f"，直链：{links[0]['link']}"
    except Exception as e:
        direct_link_text = f"（获取直链失败：{e}）"

    return f"上传完成：{target_uri}，共 {index} 个分块，总大小 {size} 字节{direct_link_text}"


@mcp.tool()
def cloudreve_create_direct_links(access_token: str, uris: list[str]) -> str:
    """为指定文件创建直链，返回可直接访问的 URL 列表。须先登录。"""
    links = cloudreve.create_direct_links(access_token, uris)
    return json.dumps(
        [{"link": item["link"], "file_url": item["file_url"]} for item in links],
        ensure_ascii=False, indent=2,
    )
