"""
Cloudreve API v4 客户端
文档: https://cloudrevev4.apifox.cn/
"""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://cloudreve.2000gallery.art/api/v4"


def _base_url() -> str:
    url = os.environ.get("CLOUDREVE_BASE_URL", DEFAULT_BASE_URL)
    return url.rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict | None = None,
    content: bytes | None = None,
) -> dict:
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
        r.raise_for_status()
        data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(data.get("msg", "请求失败"))
    return data


def get_captcha() -> dict:
    """获取登录验证码（image base64 + ticket）"""
    data = _request("GET", "/site/captcha")
    return data["data"]


def password_sign_in(
    email: str,
    password: str,
    ticket: str = "",
    captcha: str = "",
) -> dict:
    """密码登录，返回 user + token（含 access_token, refresh_token）"""
    data = _request(
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


def create_upload_session(
    access_token: str,
    uri: str,
    size: int,
    policy_id: str,
    *,
    last_modified: int | None = None,
    mime_type: str = "application/octet-stream",
) -> dict:
    """创建上传会话，返回 session_id, chunk_size 等"""
    import time
    data = _request(
        "PUT",
        "/file/upload",
        token=access_token,
        json={
            "uri": uri,
            "size": size,
            "policy_id": policy_id,
            "last_modified": last_modified or int(time.time() * 1000),
            "mime_type": mime_type,
        },
    )
    return data["data"]


def upload_file_chunk(
    access_token: str,
    session_id: str,
    index: int,
    chunk: bytes,
) -> None:
    """上传一个分块"""
    url = f"{_base_url()}/file/upload/{session_id}/{index}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(chunk)),
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, headers=headers, content=chunk)
        r.raise_for_status()
        data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(data.get("msg", f"上传分块 {index} 失败"))


def create_direct_links(access_token: str, uris: list[str]) -> list[dict]:
    """创建文件直链，返回 [{link, file_url}, ...]"""
    data = _request(
        "PUT",
        "/file/source",
        token=access_token,
        json={"uris": uris},
    )
    raw = data.get("data") or []
    return raw if isinstance(raw, list) else []
