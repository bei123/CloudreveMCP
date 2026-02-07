"""
网易云音乐：搜索、获取播放链接、下载。
参考: https://github.com/bei123/astrbot_plugin_so_vits_svc/blob/master/netease_api.py
"""

import json
import logging
from hashlib import md5
from random import randrange

logger = logging.getLogger(__name__)

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_KEY = b"e82ckenh8dichen8"
BASE_URL = "https://interface3.music.163.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.164 Safari/537.36",
    "Referer": "",
}


def _hex_digest(data: bytes) -> str:
    return "".join(hex(d)[2:].zfill(2) for d in data)


def _hash_hex_digest(text: str) -> str:
    return _hex_digest(md5(text.encode("utf-8")).digest())


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 填充至 block_size 的整数倍（若已对齐则补一整块）。"""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _encrypt_params(url_path: str, payload: dict) -> str:
    url2 = url_path.replace("/eapi/", "/api/")
    digest = _hash_hex_digest(f"nobody{url2}use{json.dumps(payload)}md5forencrypt")
    params_str = f"{url2}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
    raw = params_str.encode("utf-8")
    padded = _pkcs7_pad(raw, 16)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
    encryptor = cipher.encryptor()
    enc = encryptor.update(padded) + encryptor.finalize()
    return _hex_digest(enc)


def _post(path: str, payload: dict, cookie: str = "") -> dict:
    url = BASE_URL + path
    params_hex = _encrypt_params(path, payload)
    headers = {**HEADERS}
    cookies = {"os": "pc", "appver": "", "osver": "", "deviceId": "pyncm!"}
    if cookie:
        for item in cookie.strip().split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookies[k.strip()] = v.strip()
    with httpx.Client(timeout=15.0, headers=headers, cookies=cookies) as client:
        r = client.post(url, data={"params": params_hex})
        r.raise_for_status()
        return r.json()


def search(keyword: str, limit: int = 30, cookie: str = "") -> list[dict]:
    """搜索歌曲，返回 [{id, name, artists, album, pic_url}, ...]。"""
    config = {
        "os": "pc",
        "appver": "",
        "osver": "",
        "deviceId": "pyncm!",
        "requestId": str(randrange(20000000, 30000000)),
    }
    payload = {
        "hlpretag": "<span class=\"s-fc7\">",
        "hlposttag": "</span>",
        "s": keyword,
        "type": "1",
        "offset": "0",
        "total": "true",
        "limit": str(limit),
        "header": json.dumps(config),
    }
    result = _post("/eapi/search/get", payload, cookie=cookie)
    if "result" not in result or "songs" not in result["result"]:
        return []
    songs = result["result"]["songs"]
    out = []
    for s in songs:
        if not s.get("id") or not s.get("name"):
            continue
        album = s.get("album") or {}
        pic_url = album.get("picUrl") or album.get("pic_url") or ""
        out.append({
            "id": s.get("id"),
            "name": s.get("name", "未知"),
            "artists": [a.get("name", "") for a in s.get("artists", [])],
            "album": album.get("name", "未知"),
            "pic_url": pic_url,
        })
    return out


def _parse_cookies(cookie: str) -> dict:
    out = {}
    for item in (cookie or "").strip().split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def get_song_detail(song_id: int | str, cookie: str = "") -> dict | None:
    """获取歌曲详情（名称、歌手、专辑、封面图）。不走 eapi 加密。传 cookie 时可能返回更完整数据。"""
    sid = int(song_id)
    url = f"{BASE_URL}/api/v3/song/detail"
    data = {"c": json.dumps([{"id": sid, "v": 0}])}
    headers = {**HEADERS, "Referer": "https://music.163.com/"}
    cookies = _parse_cookies(cookie)
    song = None
    with httpx.Client(timeout=15.0, headers=headers, cookies=cookies) as client:
        r = client.post(url, data=data)
        r.raise_for_status()
        result = json.loads(r.text)
        songs = result.get("songs") or (result.get("data") or {}).get("songs")
        if songs:
            song = songs[0]
        if not song and r.status_code == 200:
            # 备用：music.163.com 老接口 GET
            r2 = client.get(
                "https://music.163.com/api/song/detail/",
                params={"id": sid, "ids": f"[{sid}]"},
                headers=headers,
            )
            r2.raise_for_status()
            raw = json.loads(r2.text)
            if raw.get("songs"):
                song = raw["songs"][0]
    if not song:
        return None
    # 部分接口用 al/ar，部分用 album/artists
    al = song.get("al") or song.get("album") or {}
    ar = song.get("ar") or song.get("artists") or []
    pic_url = (
        al.get("picUrl") or al.get("pic_url")
        or song.get("picUrl") or song.get("pic_url")
        or ""
    )
    name = song.get("name", "未知")
    artists = [a.get("name", str(a)) if isinstance(a, dict) else str(a) for a in ar] if ar else []
    return {
        "id": song.get("id"),
        "name": name,
        "artists": artists,
        "album": al.get("name", "未知"),
        "pic_url": pic_url,
    }


def get_song_url(song_id: int | str, level: str = "lossless", cookie: str = "") -> dict | None:
    """获取歌曲播放/下载链接。level: standard, exhigh, lossless, hires 等。"""
    config = {
        "os": "pc",
        "appver": "",
        "osver": "",
        "deviceId": "pyncm!",
        "requestId": str(randrange(20000000, 30000000)),
    }
    payload = {
        "ids": [int(song_id)],
        "level": level,
        "encodeType": "flac",
        "header": json.dumps(config),
    }
    result = _post("/eapi/song/enhance/player/url/v1", payload, cookie=cookie)
    if "data" not in result or not result["data"]:
        return None
    d = result["data"][0]
    if not d.get("url"):
        return None
    return {"url": d["url"], "size": d.get("size", 0), "level": d.get("level", "")}


def get_song_with_best_url(keyword_or_id: str, cookie: str = "") -> dict | None:
    """根据关键词或歌曲 ID 获取歌曲信息及最高可用音质下载链接。优先尝试无损音质，兜底次高音质、标准音质。封面等元数据统一走 get_song_detail。"""
    if keyword_or_id.strip().isdigit():
        song_id = int(keyword_or_id.strip())
        fallback = {"id": song_id, "name": "", "artists": [], "album": "", "pic_url": ""}
    else:
        songs = search(keyword_or_id, limit=1, cookie=cookie)
        if not songs:
            return None
        fallback = songs[0]
        song_id = fallback["id"]
    # 封面、歌名等优先从详情接口取（搜索接口常不返回 picUrl）；传 cookie 便于拿到封面
    detail = get_song_detail(song_id, cookie=cookie)
    first = detail if detail else fallback
    pic_url = first.get("pic_url") or fallback.get("pic_url") or ""
    # 优先无损 lossless，兜底极高 exhigh、标准 standard
    for level in ("lossless", "exhigh", "standard"):
        url_info = get_song_url(song_id, level=level, cookie=cookie)
        if url_info:
            return {
                "id": song_id,
                "name": first.get("name", "未知"),
                "artists": first.get("artists", []),
                "album": first.get("album", ""),
                "pic_url": pic_url,
                "url": url_info["url"],
                "size": url_info.get("size", 0),
                "level": url_info.get("level", ""),
            }
    return None


def download_netease_song_to_path(url: str, path: str) -> int:
    """下载网易云歌曲到本地文件，返回写入字节数。"""
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        with open(path, "wb") as f:
            n = 0
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
                n += len(chunk)
        return n


def _detect_audio_format(path: str) -> str | None:
    """根据文件头判断格式：'mp3'、'flac' 或 'm4a'（MP4 容器）。"""
    with open(path, "rb") as f:
        head = f.read(16)
    if len(head) < 8:
        return None
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    # MP4/M4A: 前 4 字节为 box 长度，接着 4 字节为类型，常见为 ftyp
    if head[4:8] == b"ftyp":
        return "m4a"
    return None


def embed_cover_into_audio(audio_path: str, cover_url: str) -> bool:
    """将封面图写入音频文件元数据（MP3 用 ID3 APIC，FLAC 用 picture）。封面从 cover_url 下载。
    返回 True 表示已嵌入，False 表示跳过（格式不支持等）；下载或写入失败时抛异常。"""
    if not cover_url or not cover_url.strip().startswith("http"):
        logger.debug("embed_cover: 无效或非 http 封面 URL，跳过")
        return False
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(cover_url)
            r.raise_for_status()
            cover_data = r.content
    except Exception as e:
        logger.warning("embed_cover: 下载封面失败 %s - %s", cover_url[:60], e)
        raise
    if not cover_data or len(cover_data) < 50:
        logger.warning("embed_cover: 封面数据为空或过短 (%s bytes)", len(cover_data) if cover_data else 0)
        return False
    # 只接受真实图片：JPG/JPEG（网易云封面多为 jpg）或 PNG，MIME 用标准 image/jpeg / image/png
    if cover_data[:2] == b"\xff\xd8":
        mime = "image/jpeg"  # .jpg / .jpeg
    elif cover_data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    else:
        logger.warning("embed_cover: 封面非 JPEG/PNG，前 8 字节 %s", cover_data[:8].hex() if len(cover_data) >= 8 else "不足")
        return False
    fmt = _detect_audio_format(audio_path)
    if not fmt:
        logger.warning("embed_cover: 无法识别音频格式 %s", audio_path)
        return False
    try:
        if fmt == "flac":
            from mutagen.flac import FLAC, Picture

            flac = FLAC(audio_path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = cover_data
            flac.clear_pictures()
            flac.add_picture(pic)
            flac.save()
            logger.debug("embed_cover: FLAC 封面写入成功")
            return True
        if fmt == "m4a":
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(audio_path)
            fmt_cover = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
            audio["covr"] = [MP4Cover(cover_data, fmt_cover)]
            audio.save()
            logger.debug("embed_cover: M4A 封面写入成功")
            return True
        # MP3：有 ID3 则直接改，无 ID3 则手动在文件头前插入 ID3 块
        from mutagen.id3 import ID3
        from mutagen.id3._frames import APIC

        apic = APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover_data)
        try:
            tags = ID3(audio_path)
        except Exception:
            tags = None
        if tags is not None:
            tags.add(apic)
            tags.save(audio_path)
            logger.debug("embed_cover: MP3 已有 ID3，封面写入成功")
            return True
        # 无 ID3：新建 ID3，写入内存，再拼到原文件前面
        tags = ID3()
        tags.add(apic)
        import io

        buf = io.BytesIO()
        tags.save(buf, padding=lambda _: 0)
        id3_bytes = buf.getvalue()
        with open(audio_path, "r+b") as f:
            audio_bytes = f.read()
        with open(audio_path, "wb") as f:
            f.write(id3_bytes)
            f.write(audio_bytes)
        logger.debug("embed_cover: MP3 无 ID3，已插入 ID3 封面块")
        return True
    except Exception as e:
        logger.warning("embed_cover: 写入元数据失败 %s - %s", audio_path, e, exc_info=True)
        raise


def download_netease_song_to_path_by_keyword(keyword_or_id: str, path: str, cookie: str = "") -> int:
    """根据关键词或歌曲 ID 获取最佳音质链接并下载到 path，返回写入字节数。"""
    info = get_song_with_best_url(keyword_or_id, cookie=cookie)
    if not info or not info.get("url"):
        raise RuntimeError("未获取到歌曲下载链接")
    return download_netease_song_to_path(info["url"], path)
