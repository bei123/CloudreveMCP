"""
哔哩哔哩视频解析与下载（含 WBI 签名、DASH/durl 流）。
参考: https://github.com/bei123/astrbot_plugin_so_vits_svc/blob/master/bilibili_api.py
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from functools import reduce
from hashlib import md5

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "referer": "https://www.bilibili.com",
}

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def _enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin_key = _get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {
        k: "".join(filter(lambda c: c not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    params["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return params


def _unescape_url(url: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), url)


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name)


def get_wbi_keys(client: httpx.Client) -> tuple[str, str]:
    r = client.get("https://api.bilibili.com/x/web-interface/nav", headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key


def parse_bilibili_share_url(share_text: str) -> dict:
    """
    从分享文本/链接中解析出 bvid。
    返回: {"bvid": "BVxxx", "title": "", "cid": ""}（title/cid 需后续 get_video_info 获取）
    """
    urls = re.findall(r"https?://(?:[a-zA-Z0-9]|[$-_.+!*(),]|(?:%[0-9a-fA-F]{2}))+", share_text)
    if not urls:
        raise ValueError("未找到有效的哔哩哔哩链接")
    url = urls[0].strip()
    with httpx.Client(timeout=15.0, follow_redirects=True, headers=HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        final = str(r.url)
    match = re.search(r"(BV[\w]+)", final, re.I)
    if not match:
        raise ValueError("无法从链接中解析视频 BV 号")
    bvid = match.group(1)
    return {"bvid": bvid}


def get_video_info(bvid: str, cookie: str = "") -> dict:
    """获取视频信息（title, cid, owner 等）。"""
    h = {**HEADERS}
    if cookie:
        h["cookie"] = cookie
    with httpx.Client(timeout=15.0, headers=h) as client:
        r = client.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取视频信息失败"))
    d = data["data"]
    return {
        "bvid": bvid,
        "title": d.get("title", ""),
        "cid": d.get("cid"),
        "owner": (d.get("owner") or {}).get("name", ""),
        "pic": d.get("pic", ""),
    }


def _download_to_path(url: str, path: str, headers: dict | None = None) -> None:
    h = headers or HEADERS
    last_err = None
    for attempt in range(5):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(30.0, read=600.0),
                headers=h,
                http2=False,
                follow_redirects=True,
            ) as client:
                r = client.get(url)
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            return
        except Exception as e:
            last_err = e
            if attempt < 4:
                time.sleep(3.0 * (attempt + 1))
            else:
                raise
    if last_err:
        raise last_err


def download_bilibili_video_to_path(bvid: str, path: str, cookie: str = "") -> int:
    """
    下载哔哩哔哩视频到本地文件（DASH 会合并音视频，durl 会合并分段）。
    传入 cookie 可获取更高画质（未登录通常只有 360p/480p，登录后可达 1080p）。
    返回写入字节数。
    """
    h = {**HEADERS}
    if cookie:
        h["cookie"] = cookie
    info = get_video_info(bvid, cookie)
    cid = info["cid"]
    title = _sanitize_filename(info["title"]) or bvid
    last_err = None
    for attempt in range(4):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(30.0, read=60.0),
                headers=h,
                http2=False,
            ) as client:
                img_key, sub_key = get_wbi_keys(client)
                params = _enc_wbi({
                    "bvid": bvid,
                    "cid": str(cid),
                    "qn": "80",
                    "fnval": "16",
                    "fnver": "0",
                    "fourk": "1",
                    "otype": "json",
                    "platform": "web",
                }, img_key, sub_key)
                r = client.get("https://api.bilibili.com/x/player/wbi/playurl", params=params)
                r.raise_for_status()
                data = r.json()
            break
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(2.0 * (attempt + 1))
            else:
                raise
    if data.get("code") != 0:
        raise RuntimeError(data.get("message", "获取播放地址失败"))
    stream = data.get("data") or {}

    if "dash" in stream:
        dash = stream["dash"]
        video_list = dash.get("video") or []
        audio_list = dash.get("audio") or []
        if not video_list:
            raise RuntimeError("DASH 无视频流")
        video_url = _unescape_url(video_list[0]["baseUrl"])
        audio_url = None
        if audio_list:
            audio_url = _unescape_url(audio_list[0]["baseUrl"])
        tmp_dir = tempfile.mkdtemp()
        try:
            video_path = f"{tmp_dir}/video.m4s"
            _download_to_path(video_url, video_path, headers=h)
            if audio_url:
                audio_path = f"{tmp_dir}/audio.m4s"
                _download_to_path(audio_url, audio_path, headers=h)
                subprocess.run([
                    "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                    "-c:v", "copy", "-c:a", "copy", "-f", "mp4", path
                ], check=True, capture_output=True)
            else:
                subprocess.run([
                    "ffmpeg", "-y", "-i", video_path, "-c", "copy", "-f", "mp4", path
                ], check=True, capture_output=True)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return os.path.getsize(path)

    if "durl" in stream:
        durl = stream["durl"]
        if not durl:
            raise RuntimeError("durl 为空")
        if len(durl) == 1:
            url = _unescape_url(durl[0]["url"])
            _download_to_path(url, path, headers=h)
            return os.path.getsize(path)
        tmp_dir = tempfile.mkdtemp()
        try:
            seg_paths = []
            for i, item in enumerate(durl):
                seg_url = _unescape_url(item["url"])
                seg_path = f"{tmp_dir}/seg{i}.flv"
                _download_to_path(seg_url, seg_path, headers=h)
                seg_paths.append(seg_path)
            concat = "|".join(seg_paths)
            subprocess.run([
                "ffmpeg", "-y", "-i", f"concat:{concat}", "-c", "copy", path
            ], check=True, capture_output=True)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return os.path.getsize(path)

    raise RuntimeError("未获取到视频流信息（非 DASH 且非 durl）")
