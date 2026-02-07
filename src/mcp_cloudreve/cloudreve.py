"""
Cloudreve API v4 客户端
文档: https://cloudrevev4.apifox.cn/
"""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://cloudreve.2000gallery.art/api/v4"

# 当 access_token 失效且提供了 refresh_token 时，会刷新并重试，返回 (data, new_tokens)；否则为 (data, None)
RefreshedTokens = dict[str, Any] | None


def _base_url() -> str:
    url = os.environ.get("CLOUDREVE_BASE_URL", DEFAULT_BASE_URL)
    return url.rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    refresh_token: str | None = None,
    json: dict | None = None,
    content: bytes | None = None,
) -> tuple[dict, RefreshedTokens]:
    url = f"{_base_url()}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=30.0) as client:
        r = client.request(
            method,
            url,
            headers=headers,
            json=json,
            content=content,
        )
        if r.status_code == 401 and refresh_token and token:
            new_tokens = refresh_token_api(refresh_token)
            data, _ = _request(
                method,
                path,
                token=new_tokens["access_token"],
                refresh_token=new_tokens.get("refresh_token"),
                json=json,
                content=content,
            )
            return (data, new_tokens)
        r.raise_for_status()
        data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(data.get("msg", "请求失败"))
    return (data, None)


def refresh_token_api(refresh_token: str) -> dict:
    """使用 refresh_token 刷新，返回新的 access_token、refresh_token 及过期时间。"""
    data, _ = _request(
        "POST",
        "/session/token/refresh",
        json={"refresh_token": refresh_token},
    )
    return data["data"]


def get_captcha() -> dict:
    """获取登录验证码（image base64 + ticket）"""
    data, _ = _request("GET", "/site/captcha")
    return data["data"]


def password_sign_in(
    email: str,
    password: str,
    ticket: str = "",
    captcha: str = "",
) -> dict:
    """密码登录，返回 user + token（含 access_token, refresh_token）"""
    data, _ = _request(
        "POST",
        "/session/token",
        json={
            "email": email,
            "password": password,
            "ticket": ticket or "",
            "captcha": captcha or "",
        },
    )
    return data["data"]


def list_storage_policies(
    access_token: str,
    *,
    refresh_token: str | None = None,
) -> tuple[list[dict], RefreshedTokens]:
    """获取当前用户可用的存储策略列表。返回 ([{id, name, type, max_size, ...}, ...], 若刷新则返回新 token 信息)。"""
    data, refreshed = _request(
        "GET",
        "/user/setting/policies",
        token=access_token,
        refresh_token=refresh_token,
    )
    raw = data.get("data") or []
    return (raw if isinstance(raw, list) else [], refreshed)


def create_file(
    access_token: str,
    uri: str,
    type: str,
    *,
    refresh_token: str | None = None,
    metadata: dict | None = None,
    err_on_conflict: bool | None = None,
) -> tuple[dict, RefreshedTokens]:
    """创建文件或文件夹。type 为 'file' 或 'folder'。若祖先目录不存在会自动创建。返回 (创建结果, 若刷新则返回新 token)。"""
    payload = {"uri": uri, "type": type}
    if metadata is not None:
        payload["metadata"] = metadata
    if err_on_conflict is not None:
        payload["err_on_conflict"] = err_on_conflict
    data, refreshed = _request(
        "POST",
        "/file/create",
        token=access_token,
        refresh_token=refresh_token,
        json=payload,
    )
    return (data["data"], refreshed)


def create_upload_session(
    access_token: str,
    uri: str,
    size: int,
    policy_id: str,
    *,
    refresh_token: str | None = None,
    last_modified: int | None = None,
    mime_type: str = "application/octet-stream",
) -> tuple[dict, RefreshedTokens]:
    """创建上传会话，返回 (session_id, chunk_size 等, 若刷新则返回新 token 信息)。"""
    import time
    data, refreshed = _request(
        "PUT",
        "/file/upload",
        token=access_token,
        refresh_token=refresh_token,
        json={
            "uri": uri,
            "size": size,
            "policy_id": policy_id,
            "last_modified": last_modified or int(time.time() * 1000),
            "mime_type": mime_type,
        },
    )
    return (data["data"], refreshed)


def upload_file_chunk(
    access_token: str,
    session_id: str,
    index: int,
    chunk: bytes,
    *,
    refresh_token: str | None = None,
) -> tuple[None, RefreshedTokens]:
    """上传一个分块。若因 token 过期返回 401 且提供了 refresh_token，则自动刷新后重试。"""
    url = f"{_base_url()}/file/upload/{session_id}/{index}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(chunk)),
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, headers=headers, content=chunk)
        if r.status_code == 401 and refresh_token:
            new_tokens = refresh_token_api(refresh_token)
            upload_file_chunk(
                new_tokens["access_token"],
                session_id,
                index,
                chunk,
                refresh_token=new_tokens.get("refresh_token"),
            )
            return (None, new_tokens)
        r.raise_for_status()
        data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(data.get("msg", f"上传分块 {index} 失败"))
    return (None, None)


def create_direct_links(
    access_token: str,
    uris: list[str],
    *,
    refresh_token: str | None = None,
) -> tuple[list[dict], RefreshedTokens]:
    """创建文件直链，返回 ([{link, file_url}, ...], 若刷新则返回新 token 信息)。"""
    data, refreshed = _request(
        "PUT",
        "/file/source",
        token=access_token,
        refresh_token=refresh_token,
        json={"uris": uris},
    )
    raw = data.get("data") or []
    return (raw if isinstance(raw, list) else [], refreshed)
