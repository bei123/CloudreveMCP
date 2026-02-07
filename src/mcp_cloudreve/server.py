"""
MCP 服务：Cloudreve 登录、上传、直链等工具。
支持 SSE 传输（平台 SSE 模板：GET /sse 建流，POST /messages?session_id=xxx）。
"""

import base64
import json
import logging
import os
import re
import tempfile
import time

logger = logging.getLogger(__name__)

from mcp.server.fastmcp import FastMCP

from . import bilibili
from . import cloudreve
from . import douyin
from . import netease

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
    """使用邮箱和密码登录 Cloudreve。上传文件前必须先调用本工具获得 access_token。若站点未开启验证码，ticket 和 captcha 可留空。返回的 refresh_token 可用于 cloudreve_refresh_token 或随后续工具传入以自动刷新。"""
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


@mcp.tool()
def cloudreve_refresh_token(refresh_token: str) -> str:
    """使用 refresh_token 刷新令牌，返回新的 access_token 与 refresh_token。access_token 过期时可调用本工具或在下述工具中传入 refresh_token 以自动刷新。"""
    data = cloudreve.refresh_token_api(refresh_token)
    out = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "access_expires": data.get("access_expires"),
        "refresh_expires": data.get("refresh_expires"),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def cloudreve_list_storage_policies(access_token: str, refresh_token: str = "") -> str:
    """获取当前用户可用的存储策略列表（id、name、type、max_size 等）。上传文件时 policy_id 填此处返回的 id。须先 cloudreve_login。可传 refresh_token 以在 token 过期时自动刷新。"""
    policies, refreshed = cloudreve.list_storage_policies(
        access_token, refresh_token=refresh_token or None,
    )
    out = [{"id": p.get("id"), "name": p.get("name"), "type": p.get("type"), "max_size": p.get("max_size"), "relay": p.get("relay")} for p in policies]
    result = json.dumps(out, ensure_ascii=False, indent=2)
    if refreshed:
        result += "\n\n刷新后的令牌（后续请求请使用）：\n" + json.dumps({
            "access_token": refreshed["access_token"],
            "refresh_token": refreshed["refresh_token"],
            "access_expires": refreshed.get("access_expires"),
            "refresh_expires": refreshed.get("refresh_expires"),
        }, ensure_ascii=False, indent=2)
    return result


@mcp.tool()
def cloudreve_create_folder(
    access_token: str,
    folder_uri: str,
    refresh_token: str = "",
    err_on_conflict: bool = False,
) -> str:
    """在网盘中创建文件夹。folder_uri 为完整 URI，如 cloudreve://my/douyin 或 cloudreve://douyin（会自动补为 cloudreve://my/douyin）；若祖先目录不存在会自动创建。err_on_conflict 为 False 时若文件夹已存在则返回现有信息不报错。须先 cloudreve_login。"""
    try:
        folder = folder_uri.strip().rstrip("/")
        if folder.startswith("cloudreve://") and "/" not in folder[len("cloudreve://"):]:
            folder = f"cloudreve://my/{folder[len('cloudreve://'):]}"
        file_data, refreshed = cloudreve.create_file(
            access_token,
            folder,
            "folder",
            refresh_token=refresh_token or None,
            err_on_conflict=err_on_conflict,
        )
        out = {
            "path": file_data.get("path"),
            "id": file_data.get("id"),
            "name": file_data.get("name"),
            "type": "folder",
        }
        result = json.dumps(out, ensure_ascii=False, indent=2)
        if refreshed:
            result += "\n\n刷新后的令牌（后续请求请使用）：\n" + json.dumps({
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed["refresh_token"],
                "access_expires": refreshed.get("access_expires"),
                "refresh_expires": refreshed.get("refresh_expires"),
            }, ensure_ascii=False, indent=2)
        return result
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e) or repr(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False, indent=2)


# ----- Cloudreve：上传会话与分块 -----
@mcp.tool()
def cloudreve_create_upload_session(
    access_token: str,
    uri: str,
    size: int,
    policy_id: str,
    refresh_token: str = "",
    last_modified: int | None = None,
    mime_type: str = "application/octet-stream",
) -> str:
    """创建 Cloudreve 文件上传会话。须先 cloudreve_login。可传 refresh_token，access_token 过期时会自动刷新。返回 session_id、chunk_size 等；若发生刷新会包含 refreshed_tokens。"""
    session_data, refreshed = cloudreve.create_upload_session(
        access_token, uri, size, policy_id,
        refresh_token=refresh_token or None,
        last_modified=last_modified, mime_type=mime_type,
    )
    out = {
        "session_id": session_data["session_id"],
        "chunk_size": session_data["chunk_size"],
        "expires": session_data.get("expires"),
        "uri": session_data.get("uri"),
    }
    if refreshed:
        out["refreshed_tokens"] = {
            "access_token": refreshed["access_token"],
            "refresh_token": refreshed["refresh_token"],
            "access_expires": refreshed.get("access_expires"),
            "refresh_expires": refreshed.get("refresh_expires"),
        }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def cloudreve_upload_file_chunk(
    access_token: str,
    session_id: str,
    index: int,
    chunk_base64: str,
    refresh_token: str = "",
) -> str:
    """向已创建的上传会话上传一个分块。分块从 index=0 开始按顺序上传；chunk_base64 为该分块的 Base64。可传 refresh_token，token 过期时自动刷新后重试。"""
    chunk = base64.b64decode(chunk_base64)
    _, refreshed = cloudreve.upload_file_chunk(
        access_token, session_id, index, chunk,
        refresh_token=refresh_token or None,
    )
    if refreshed:
        return json.dumps({
            "message": f"分块 {index} 上传成功",
            "refreshed_tokens": {
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed["refresh_token"],
                "access_expires": refreshed.get("access_expires"),
                "refresh_expires": refreshed.get("refresh_expires"),
            },
        }, ensure_ascii=False, indent=2)
    return f"分块 {index} 上传成功"


@mcp.tool()
def cloudreve_upload_file(
    access_token: str,
    target_uri: str,
    policy_id: str,
    file_path: str | None = None,
    file_base64: str | None = None,
    refresh_token: str = "",
    mime_type: str | None = None,
) -> str:
    """将本地文件或 Base64 内容上传到 Cloudreve。须先 cloudreve_login。可传 file_path 或 file_base64；可传 refresh_token，access_token 过期时自动刷新。上传完成后会自动尝试获取直链。"""
    if file_path:
        with open(file_path, "rb") as f:
            buffer = f.read()
    elif file_base64:
        buffer = base64.b64decode(file_base64)
    else:
        return json.dumps({"error": "必须提供 file_path 或 file_base64 之一"}, ensure_ascii=False)

    size = len(buffer)
    rft = refresh_token or None
    refreshed = chunk_refreshed = link_refreshed = None
    session_data, refreshed = cloudreve.create_upload_session(
        access_token, target_uri, size, policy_id,
        refresh_token=rft,
        mime_type=mime_type or "application/octet-stream",
    )
    if refreshed:
        access_token = refreshed["access_token"]
        rft = refreshed.get("refresh_token")
    chunk_size = session_data["chunk_size"] or size
    if chunk_size <= 0:
        chunk_size = size
    session_id = session_data["session_id"]
    index = 0
    for offset in range(0, size, chunk_size):
        end = min(offset + chunk_size, size)
        _, chunk_refreshed = cloudreve.upload_file_chunk(
            access_token, session_id, index, buffer[offset:end],
            refresh_token=rft,
        )
        if chunk_refreshed:
            access_token = chunk_refreshed["access_token"]
            rft = chunk_refreshed.get("refresh_token")
        index += 1

    direct_link_text = ""
    link_refreshed = None
    try:
        links, link_refreshed = cloudreve.create_direct_links(access_token, [target_uri], refresh_token=rft)
        if link_refreshed:
            access_token = link_refreshed["access_token"]
            rft = link_refreshed.get("refresh_token")
        if links and links[0].get("link"):
            direct_link_text = f"，直链：{links[0]['link']}"
    except Exception as e:
        direct_link_text = f"（获取直链失败：{e}）"

    result = f"上传完成：{target_uri}，共 {index} 个分块，总大小 {size} 字节{direct_link_text}"
    if refreshed or chunk_refreshed or link_refreshed:
        final_refreshed = refreshed or chunk_refreshed or link_refreshed
        if final_refreshed:
            result += "\n\n刷新后的令牌（后续请求请使用）：\n" + json.dumps({
                "access_token": final_refreshed["access_token"],
                "refresh_token": final_refreshed["refresh_token"],
                "access_expires": final_refreshed.get("access_expires"),
                "refresh_expires": final_refreshed.get("refresh_expires"),
            }, ensure_ascii=False, indent=2)
    return result


@mcp.tool()
def cloudreve_create_direct_links(access_token: str, uris: list[str], refresh_token: str = "") -> str:
    """为指定文件创建直链，返回可直接访问的 URL 列表。须先登录。可传 refresh_token，access_token 过期时自动刷新。"""
    links, refreshed = cloudreve.create_direct_links(
        access_token, uris, refresh_token=refresh_token or None,
    )
    out = [{"link": item["link"], "file_url": item["file_url"]} for item in links]
    result = json.dumps(out, ensure_ascii=False, indent=2)
    if refreshed:
        result += "\n\n刷新后的令牌（后续请求请使用）：\n" + json.dumps({
            "access_token": refreshed["access_token"],
            "refresh_token": refreshed["refresh_token"],
            "access_expires": refreshed.get("access_expires"),
            "refresh_expires": refreshed.get("refresh_expires"),
        }, ensure_ascii=False, indent=2)
    return result


# ----- 抖音：解析 → 下载 → 上传网盘 → 直链 -----
@mcp.tool()
def cloudreve_upload_douyin_video(
    access_token: str,
    douyin_share_link: str,
    policy_id: str,
    refresh_token: str = "",
    folder_uri: str = "",
    target_uri: str | None = None,
) -> str:
    """MCP 流程：登入网盘 → 解析抖音链接 → 下载视频 → 上传到网盘。本工具完成后三步：解析抖音分享链接、将无水印视频下载到临时文件、在网盘创建/确认文件夹后分块上传并返回直链，上传完毕后删除临时文件。须先调用 cloudreve_login 获得 access_token；policy_id 可用 cloudreve_list_storage_policies 查询。可传 refresh_token 以在 token 过期时自动刷新。folder_uri 不传则默认上传到 cloudreve://my/douyin/{视频ID}.mp4；可传 folder_uri（如 cloudreve://my/douyin 或 cloudreve://douyin）指定目录。target_uri 可覆盖最终文件 URI。"""
    try:
        return _cloudreve_upload_douyin_video_impl(
            access_token=access_token,
            douyin_share_link=douyin_share_link,
            policy_id=policy_id,
            refresh_token=refresh_token,
            folder_uri=folder_uri,
            target_uri=target_uri,
        )
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e) or repr(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False, indent=2)


def _cloudreve_upload_douyin_video_impl(
    access_token: str,
    douyin_share_link: str,
    policy_id: str,
    refresh_token: str,
    folder_uri: str,
    target_uri: str | None,
) -> str:
    info = douyin.parse_douyin_share_url(douyin_share_link)
    video_url = info["url"]
    title = info["title"]
    video_id = info["video_id"]

    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()
        douyin.download_douyin_video_to_path(video_url, tmp_path)
        size = os.path.getsize(tmp_path)

        rft = refresh_token or None
        if (target_uri or "").strip():
            uri = (target_uri or "").strip()
        elif (folder_uri or "").strip():
            folder = folder_uri.strip().rstrip("/")
            if folder.startswith("cloudreve://") and "/" not in folder[len("cloudreve://"):]:
                folder = f"cloudreve://my/{folder[len('cloudreve://'):]}"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{video_id}.mp4"
        else:
            folder = "cloudreve://my/douyin"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{video_id}.mp4"
        refreshed = chunk_refreshed = link_refreshed = None
        session_data, refreshed = cloudreve.create_upload_session(
            access_token, uri, size, policy_id,
            refresh_token=rft,
            mime_type="video/mp4",
        )
        if refreshed:
            access_token = refreshed["access_token"]
            rft = refreshed.get("refresh_token")
        chunk_size = session_data["chunk_size"] or size
        if chunk_size <= 0:
            chunk_size = size
        session_id = session_data["session_id"]
        index = 0
        with open(tmp_path, "rb") as f:
            for offset in range(0, size, chunk_size):
                chunk = f.read(chunk_size)
                _, chunk_refreshed = cloudreve.upload_file_chunk(
                    access_token, session_id, index, chunk,
                    refresh_token=rft,
                )
                if chunk_refreshed:
                    access_token = chunk_refreshed["access_token"]
                    rft = chunk_refreshed.get("refresh_token")
                index += 1

        link_refreshed = None
        direct_link = ""
        try:
            links, link_refreshed = cloudreve.create_direct_links(access_token, [uri], refresh_token=rft)
            if link_refreshed:
                access_token = link_refreshed["access_token"]
                rft = link_refreshed.get("refresh_token")
            if links and links[0].get("link"):
                direct_link = links[0]["link"]
        except Exception as e:
            direct_link = f"（获取直链失败：{e}）"

        out = {
            "status": "success",
            "video_id": video_id,
            "title": title,
            "target_uri": uri,
            "size_bytes": size,
            "direct_link": direct_link,
        }
        if refreshed or chunk_refreshed or link_refreshed:
            final_refreshed = refreshed or chunk_refreshed or link_refreshed
            if final_refreshed:
                out["refreshed_tokens"] = {
                    "access_token": final_refreshed["access_token"],
                    "refresh_token": final_refreshed["refresh_token"],
                    "access_expires": final_refreshed.get("access_expires"),
                    "refresh_expires": final_refreshed.get("refresh_expires"),
                }
        return json.dumps(out, ensure_ascii=False, indent=2)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


# ----- 哔哩哔哩：解析 → 下载 → 上传网盘 → 直链 -----
@mcp.tool()
def cloudreve_upload_bilibili_video(
    access_token: str,
    bilibili_share_link: str,
    policy_id: str,
    refresh_token: str = "",
    folder_uri: str = "",
    target_uri: str | None = None,
    cookie: str = "",
) -> str:
    """MCP 流程：登入网盘 → 解析哔哩哔哩链接 → 下载视频（DASH/durl）→ 上传到网盘。本工具完成后三步；须先 cloudreve_login。未登录时画质通常只有 360p/480p，建议传 B 站 cookie 以获取 1080p 等更高画质；cookie 也会用于获取播放地址和下载音视频片段。DASH 流会合并音视频，多段 durl 会合并后上传，需本机安装 ffmpeg。folder_uri 不传则默认 cloudreve://my/bilibili/{bvid}.mp4。"""
    try:
        return _cloudreve_upload_bilibili_video_impl(
            access_token=access_token,
            bilibili_share_link=bilibili_share_link,
            policy_id=policy_id,
            refresh_token=refresh_token,
            folder_uri=folder_uri,
            target_uri=target_uri,
            cookie=cookie,
        )
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e) or repr(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False, indent=2)


def _cloudreve_upload_bilibili_video_impl(
    access_token: str,
    bilibili_share_link: str,
    policy_id: str,
    refresh_token: str,
    folder_uri: str,
    target_uri: str | None,
    cookie: str,
) -> str:
    parsed = bilibili.parse_bilibili_share_url(bilibili_share_link)
    bvid = parsed["bvid"]
    info = bilibili.get_video_info(bvid, cookie=cookie or "")
    title = info.get("title", "")

    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()
        bilibili.download_bilibili_video_to_path(bvid, tmp_path, cookie=cookie or "")
        size = os.path.getsize(tmp_path)

        rft = refresh_token or None
        if (target_uri or "").strip():
            uri = (target_uri or "").strip()
        elif (folder_uri or "").strip():
            folder = folder_uri.strip().rstrip("/")
            if folder.startswith("cloudreve://") and "/" not in folder[len("cloudreve://"):]:
                folder = f"cloudreve://my/{folder[len('cloudreve://'):]}"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{bvid}.mp4"
        else:
            folder = "cloudreve://my/bilibili"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{bvid}.mp4"
        refreshed = chunk_refreshed = link_refreshed = None
        session_data, refreshed = cloudreve.create_upload_session(
            access_token, uri, size, policy_id,
            refresh_token=rft,
            mime_type="video/mp4",
        )
        if refreshed:
            access_token = refreshed["access_token"]
            rft = refreshed.get("refresh_token")
        chunk_size = session_data["chunk_size"] or size
        if chunk_size <= 0:
            chunk_size = size
        session_id = session_data["session_id"]
        index = 0
        with open(tmp_path, "rb") as f:
            for offset in range(0, size, chunk_size):
                chunk = f.read(chunk_size)
                _, chunk_refreshed = cloudreve.upload_file_chunk(
                    access_token, session_id, index, chunk,
                    refresh_token=rft,
                )
                if chunk_refreshed:
                    access_token = chunk_refreshed["access_token"]
                    rft = chunk_refreshed.get("refresh_token")
                index += 1

        link_refreshed = None
        direct_link = ""
        try:
            links, link_refreshed = cloudreve.create_direct_links(access_token, [uri], refresh_token=rft)
            if link_refreshed:
                access_token = link_refreshed["access_token"]
                rft = link_refreshed.get("refresh_token")
            if links and links[0].get("link"):
                direct_link = links[0]["link"]
        except Exception as e:
            direct_link = f"（获取直链失败：{e}）"

        out = {
            "status": "success",
            "bvid": bvid,
            "title": title,
            "target_uri": uri,
            "size_bytes": size,
            "direct_link": direct_link,
        }
        if refreshed or chunk_refreshed or link_refreshed:
            final_refreshed = refreshed or chunk_refreshed or link_refreshed
            if final_refreshed:
                out["refreshed_tokens"] = {
                    "access_token": final_refreshed["access_token"],
                    "refresh_token": final_refreshed["refresh_token"],
                    "access_expires": final_refreshed.get("access_expires"),
                    "refresh_expires": final_refreshed.get("refresh_expires"),
                }
        return json.dumps(out, ensure_ascii=False, indent=2)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


# ----- 网易云音乐：搜索/ID → 获取最佳音质链接 → 下载 → 上传网盘 → 直链 -----
@mcp.tool()
def cloudreve_upload_netease_song(
    access_token: str,
    keyword_or_song_id: str,
    policy_id: str,
    refresh_token: str = "",
    folder_uri: str = "",
    target_uri: str | None = None,
    netease_cookie: str = "",
) -> str:
    """MCP 流程：登入网盘 → 根据关键词或歌曲 ID 获取网易云最佳音质链接 → 下载到临时文件 → 将封面图（JPG）嵌入音频元数据（MP3 ID3 / FLAC picture）→ 上传到网盘并返回直链。须先 cloudreve_login。keyword_or_song_id 可为搜索关键词或歌曲 ID（纯数字）。可选传 netease_cookie 以获取更高音质（如无损）。folder_uri 不传则默认 cloudreve://my/netease/{歌曲名 - 歌手}.mp3。返回中含 cover_url、direct_link。"""
    try:
        return _cloudreve_upload_netease_song_impl(
            access_token=access_token,
            keyword_or_song_id=keyword_or_song_id,
            policy_id=policy_id,
            refresh_token=refresh_token,
            folder_uri=folder_uri,
            target_uri=target_uri,
            netease_cookie=netease_cookie,
        )
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e) or repr(e),
            "error_type": type(e).__name__,
        }, ensure_ascii=False, indent=2)


def _cloudreve_upload_netease_song_impl(
    access_token: str,
    keyword_or_song_id: str,
    policy_id: str,
    refresh_token: str,
    folder_uri: str,
    target_uri: str | None,
    netease_cookie: str,
) -> str:
    info = netease.get_song_with_best_url(keyword_or_song_id, cookie=netease_cookie or "")
    if not info or not info.get("url"):
        raise RuntimeError("未获取到歌曲或下载链接")
    name = (info.get("name") or "未知").strip()
    artists = info.get("artists") or []
    safe = re.sub(r'[\\/:*?"<>|]', "", f"{name} - {', '.join(artists) if artists else '未知'}".strip() or "song")
    filename = f"{safe}.mp3"

    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        # 1) 下载音频到临时文件
        netease.download_netease_song_to_path(info["url"], tmp_path)
        # 2) 上传到网盘前，先将封面图（JPG）嵌入音频元数据（MP3 ID3 / FLAC picture）
        pic_url = info.get("pic_url") or ""
        cover_embedded = False
        cover_embed_error: str | None = None
        if pic_url:
            try:
                logger.info("网易云上传：正在将封面嵌入音频 %s", tmp_path)
                cover_embedded = netease.embed_cover_into_audio(tmp_path, pic_url)
                if cover_embedded:
                    logger.info("网易云上传：封面嵌入成功")
                else:
                    logger.info("网易云上传：跳过嵌入（格式不支持或无有效封面）")
            except Exception as e:
                cover_embed_error = str(e) or type(e).__name__
                logger.warning("网易云上传：封面嵌入失败 - %s", cover_embed_error, exc_info=True)
        else:
            logger.debug("网易云上传：无封面 URL，跳过嵌入")
        # 3) 嵌封面后重新取大小，再创建上传会话并分块上传
        size = os.path.getsize(tmp_path)

        rft = refresh_token or None
        if (target_uri or "").strip():
            uri = (target_uri or "").strip()
        elif (folder_uri or "").strip():
            folder = folder_uri.strip().rstrip("/")
            if folder.startswith("cloudreve://") and "/" not in folder[len("cloudreve://"):]:
                folder = f"cloudreve://my/{folder[len('cloudreve://'):]}"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{filename}"
        else:
            folder = "cloudreve://my/netease"
            _, folder_refreshed = cloudreve.create_file(
                access_token, folder, "folder",
                refresh_token=rft, err_on_conflict=False,
            )
            if folder_refreshed:
                access_token = folder_refreshed["access_token"]
                rft = folder_refreshed.get("refresh_token")
            uri = f"{folder}/{filename}"
        refreshed = chunk_refreshed = link_refreshed = None
        session_data, refreshed = cloudreve.create_upload_session(
            access_token, uri, size, policy_id,
            refresh_token=rft,
            mime_type="audio/mpeg",
        )
        if refreshed:
            access_token = refreshed["access_token"]
            rft = refreshed.get("refresh_token")
        chunk_size = session_data["chunk_size"] or size
        if chunk_size <= 0:
            chunk_size = size
        session_id = session_data["session_id"]
        index = 0
        with open(tmp_path, "rb") as f:
            for offset in range(0, size, chunk_size):
                chunk = f.read(chunk_size)
                _, chunk_refreshed = cloudreve.upload_file_chunk(
                    access_token, session_id, index, chunk,
                    refresh_token=rft,
                )
                if chunk_refreshed:
                    access_token = chunk_refreshed["access_token"]
                    rft = chunk_refreshed.get("refresh_token")
                index += 1

        direct_link = ""
        try:
            links, link_refreshed = cloudreve.create_direct_links(access_token, [uri], refresh_token=rft)
            if link_refreshed:
                access_token = link_refreshed["access_token"]
                rft = link_refreshed.get("refresh_token")
            if links and links[0].get("link"):
                direct_link = links[0]["link"]
        except Exception as e:
            direct_link = f"（获取直链失败：{e}）"

        out = {
            "status": "success",
            "song_id": info.get("id"),
            "name": name,
            "artists": artists,
            "cover_url": info.get("pic_url") or "",
            "cover_embedded": cover_embedded,
            "target_uri": uri,
            "size_bytes": size,
            "direct_link": direct_link,
        }
        if cover_embed_error is not None:
            out["cover_embed_error"] = cover_embed_error
        if refreshed or chunk_refreshed or link_refreshed:
            final_refreshed = refreshed or chunk_refreshed or link_refreshed
            if final_refreshed:
                out["refreshed_tokens"] = {
                    "access_token": final_refreshed["access_token"],
                    "refresh_token": final_refreshed["refresh_token"],
                    "access_expires": final_refreshed.get("access_expires"),
                    "refresh_expires": final_refreshed.get("refresh_expires"),
                }
        return json.dumps(out, ensure_ascii=False, indent=2)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
