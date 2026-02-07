"""
抖音分享链接解析与无水印视频下载。
参考: https://github.com/yzfly/douyin-mcp-server
"""

import json
import re

import httpx

# 模拟移动端，便于解析分享页
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
}


def parse_douyin_share_url(share_text: str) -> dict:
    """
    从分享文本/链接中解析出无水印视频信息。
    返回: {"url": 无水印播放地址, "title": 视频标题/描述, "video_id": 视频 ID}
    """
    urls = re.findall(
        r"https?://(?:[a-zA-Z0-9]|[$-_.+!*(),]|(?:%[0-9a-fA-F]{2}))+",
        share_text,
    )
    if not urls:
        raise ValueError("未找到有效的抖音分享链接")

    share_url = urls[0].strip()
    with httpx.Client(timeout=15.0, follow_redirects=True, headers=HEADERS) as client:
        r = client.get(share_url)
        r.raise_for_status()
        final_url = str(r.url)

    # 从最终 URL 取 video_id（如 iesdouyin.com/share/video/xxxxx）
    parts = final_url.split("?")[0].rstrip("/").split("/")
    video_id = parts[-1] if parts else ""
    if not video_id:
        raise ValueError("无法从链接中解析视频 ID")

    page_url = f"https://www.iesdouyin.com/share/video/{video_id}"
    with httpx.Client(timeout=15.0, headers=HEADERS) as client:
        r = client.get(page_url)
        r.raise_for_status()
        html = r.text

    # 页面内 _ROUTER_DATA 含视频信息
    pattern = re.compile(
        r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
        re.DOTALL,
    )
    match = pattern.search(html)
    if not match or not match.group(1):
        raise ValueError("从页面解析视频信息失败")

    try:
        data = json.loads(match.group(1).strip())
    except json.JSONDecodeError as e:
        raise ValueError(f"解析页面 JSON 失败: {e}") from e

    loader = data.get("loaderData") or {}
    video_page_key = "video_(id)/page"
    note_page_key = "note_(id)/page"
    if video_page_key in loader:
        info = loader[video_page_key].get("videoInfoRes") or {}
    elif note_page_key in loader:
        info = loader[note_page_key].get("videoInfoRes") or {}
    else:
        raise ValueError("无法从页面数据中获取视频或图集信息")

    item_list = (info.get("item_list") or [])
    if not item_list:
        raise ValueError("视频列表为空")
    item = item_list[0]
    play_addr = (item.get("video") or {}).get("play_addr") or {}
    url_list = play_addr.get("url_list") or []
    if not url_list:
        raise ValueError("未找到播放地址")
    # 去水印：playwm -> play
    video_url = url_list[0].replace("playwm", "play")
    desc = (item.get("desc") or "").strip() or f"douyin_{video_id}"
    desc = re.sub(r'[\\/:*?"<>|]', "_", desc)

    return {
        "url": video_url,
        "title": desc,
        "video_id": video_id,
    }


def download_douyin_video(video_url: str) -> bytes:
    """下载抖音无水印视频，返回完整字节内容。"""
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=HEADERS) as client:
        r = client.get(video_url)
        r.raise_for_status()
        return r.content


def download_douyin_video_to_path(video_url: str, path: str) -> int:
    """下载抖音无水印视频到本地文件（流式写入），返回写入字节数。用于大文件时避免整文件进内存。"""
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=HEADERS) as client:
        with client.stream("GET", video_url) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                return sum(f.write(chunk) for chunk in r.iter_bytes(chunk_size=65536))
