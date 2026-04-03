import base64
import json
import re
from html import unescape

import requests


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _headers(referer=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8,es;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _rot13(text):
    chars = []
    for char in text:
        if "A" <= char <= "Z":
            chars.append(chr((ord(char) - ord("A") + 13) % 26 + ord("A")))
        elif "a" <= char <= "z":
            chars.append(chr((ord(char) - ord("a") + 13) % 26 + ord("a")))
        else:
            chars.append(char)
    return "".join(chars)


def _decrypt_voi_payload(encoded_string):
    patterns = ["@$", "^^", "~@", "%?", "*~", "!!", "#&"]
    stage = _rot13(encoded_string)
    for pattern in patterns:
        stage = stage.replace(pattern, "_")
    stage = stage.replace("_", "")
    stage = base64.b64decode(stage + "=" * (-len(stage) % 4)).decode("utf-8")
    shifted = "".join(chr(ord(char) - 3) for char in stage)
    reversed_stage = shifted[::-1]
    final = base64.b64decode(reversed_stage + "=" * (-len(reversed_stage) % 4)).decode("utf-8")
    return json.loads(final)


def _extract_packed_m3u8(script):
    unpack_match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('([\s\S]*?)',(\d+),(\d+),'([\s\S]*?)'\.split\('\|'\)",
        script,
    )
    if not unpack_match:
        return None
    payload = unpack_match.group(1)
    radix = int(unpack_match.group(2))
    table = unpack_match.group(4).split("|")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def unbase(value):
        result = 0
        for char in value:
            index = alphabet.find(char)
            if index == -1:
                return None
            result = result * radix + index
        return result

    unpacked = re.sub(
        r"\b([0-9A-Za-z]+)\b",
        lambda match: table[idx]
        if (idx := unbase(match.group(1))) is not None and idx < len(table) and table[idx]
        else match.group(1),
        payload,
    )
    file_match = re.search(r'file\s*[:=]\s*["\']((?:https?:)?//[^"\']+\.m3u8[^"\']*)', unpacked)
    return file_match.group(1) if file_match else None


def _extract_vidhide_m3u8(html):
    packed = re.search(r"(eval\(function\(p,a,c,k,e,d\)[\s\S]*?)</script>", html)
    if not packed:
        return None
    unpacked = _extract_packed_m3u8(packed.group(1))
    return unpacked


def _resolve_goodstream(url, referer=None):
    response = requests.get(url, headers=_headers(referer), timeout=10)
    response.raise_for_status()
    html = response.text
    file_match = re.search(r'file\s*:\s*["\']([^"\']+)["\']', html)
    if not file_match:
        return None
    return {
        "url": file_match.group(1),
        "headers": {"User-Agent": USER_AGENT, "Referer": referer or url},
        "quality": "1080p",
    }


def _resolve_vidsonic(url, referer=None):
    response = requests.get(url, headers=_headers(referer), timeout=10)
    response.raise_for_status()
    encoded_match = re.search(r"const\s+_0x1\s*=\s*'([^']+)'", response.text)
    if not encoded_match:
        return None
    clean = encoded_match.group(1).replace("|", "")
    decoded = "".join(chr(int(clean[index : index + 2], 16)) for index in range(0, len(clean), 2))
    media_url = decoded[::-1]
    if not media_url.startswith(("http://", "https://")):
        return None
    return {
        "url": media_url,
        "headers": {"User-Agent": USER_AGENT, "Referer": "https://vidsonic.net/"},
        "quality": "1080p",
    }


def _resolve_okru(url, referer=None):
    response = requests.get(url, headers=_headers(referer or "https://ok.ru/"), timeout=10)
    response.raise_for_status()
    html = response.text.replace(r"\u0026", "&").replace("\\", "")
    matches = re.findall(r'"name":"([^"]+)","url":"([^"]+)"', html)
    if not matches:
        return None
    quality_map = {"full": "1080p", "hd": "720p", "sd": "480p", "low": "360p", "lowest": "240p"}
    order = ["full", "hd", "sd", "low", "lowest"]
    matches.sort(key=lambda item: order.index(item[0]) if item[0] in order else 99)
    stream_type, stream_url = matches[0]
    return {
        "url": stream_url,
        "headers": {"User-Agent": USER_AGENT, "Referer": "https://ok.ru/"},
        "quality": quality_map.get(stream_type, "720p"),
    }


def _resolve_streamwish(url, referer=None):
    response = requests.get(url, headers=_headers(referer), timeout=12, allow_redirects=True)
    response.raise_for_status()
    html = response.text

    if "Page is loading, please wait" in html:
        return None

    direct_match = re.search(r'file\s*[:=]\s*["\']((?:https?:)?//[^"\']+\.m3u8[^"\']*)', html)
    if direct_match:
        final_url = direct_match.group(1)
        if final_url.startswith("//"):
            final_url = "https:" + final_url
        return {
            "url": final_url,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": referer or url,
                "Origin": "https://%s" % re.sub(r"^https?://([^/]+)/?.*$", r"\1", response.url),
            },
            "quality": "1080p",
        }

    packed = _extract_packed_m3u8(html)
    if packed:
        if packed.startswith("//"):
            packed = "https:" + packed
        return {
            "url": packed,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": referer or url,
                "Origin": "https://%s" % re.sub(r"^https?://([^/]+)/?.*$", r"\1", response.url),
            },
            "quality": "1080p",
        }
    return None


def _resolve_vidhide(url, referer=None):
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer or (re.match(r"^https?://[^/]+", url).group(0) if re.match(r"^https?://[^/]+", url) else url),
            "Origin": referer or (re.match(r"^https?://[^/]+", url).group(0) if re.match(r"^https?://[^/]+", url) else url),
        },
        timeout=12,
        allow_redirects=True,
    )
    response.raise_for_status()
    html = response.text
    if "Countries are not allowed" in html:
        return None
    m3u8 = _extract_vidhide_m3u8(html)
    if not m3u8:
        return None
    if m3u8.startswith("/"):
        origin = re.match(r"^https?://[^/]+", response.url)
        if origin:
            m3u8 = origin.group(0) + m3u8
    return {
        "url": m3u8,
        "headers": {"User-Agent": USER_AGENT, "Referer": referer or response.url},
        "quality": "1080p",
    }


def _resolve_voe(url, referer=None):
    try:
        response = requests.get(url, headers=_headers(referer or url), timeout=10, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return None
    html = response.text

    raw_json = re.search(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.S)
    encoded = raw_json.group(1).strip() if raw_json else None
    if encoded:
        try:
            data = _decrypt_voi_payload(encoded)
            source = data.get("source")
            if source:
                return {
                    "url": source,
                    "headers": {"User-Agent": USER_AGENT, "Referer": response.url},
                    "quality": "1080p",
                }
        except Exception:
            pass

    redirect_match = re.search(r'https://([a-zA-Z0-9.-]+)(?:/[^"\']*)?', html)
    if redirect_match:
        redirected_base = "https://%s" % redirect_match.group(1)
        path = re.sub(r"^https?://[^/]+", "", url)
        try:
            redirected = requests.get(redirected_base + path, headers=_headers(url), timeout=10)
            redirected.raise_for_status()
            encoded = re.search(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', redirected.text, re.S)
            if encoded:
                data = _decrypt_voi_payload(encoded.group(1).strip())
                source = data.get("source")
                if source:
                    return {
                        "url": source,
                        "headers": {"User-Agent": USER_AGENT, "Referer": redirected.url},
                        "quality": "1080p",
                    }
        except Exception:
            pass

    source_match = re.search(r'"source"\s*:\s*"([^"]+\.m3u8[^"]*)"', html)
    if source_match:
        source = unescape(source_match.group(1)).replace("\\/", "/")
        return {
            "url": source,
            "headers": {"User-Agent": USER_AGENT, "Referer": response.url},
            "quality": "1080p",
        }
    return None


def extract_video(url, server_name=None, referer=None):
    lowered = str(url or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return None
    if any(host in lowered for host in ("goodstream.one", "goodstream.uno")):
        return _resolve_goodstream(url, referer)
    if "vidsonic" in lowered:
        return _resolve_vidsonic(url, referer)
    if any(host in lowered for host in ("minochinos.com", "filelions.to", "dintezuvio.com", "vidhideplus.com", "peytonepre.com")):
        return _resolve_vidhide(url, referer)
    if any(host in lowered for host in ("streamwish", "hlswish", "playerwish", "swish", "wish", "hglink.to", "iplayerhls", "vidhide", "filelions", "vibuxer", "vidnest", "streamwish.site", "streamwish.to", "strwish")):
        return _resolve_streamwish(url, referer)
    if any(host in lowered for host in ("voe.sx", "voe-network", "voeunblock", "jilliandescribecompany", "walterprettytheir", "dianaavoidthey")):
        return _resolve_voe(url, referer)
    if "ok.ru" in lowered or "okcdn.ru" in lowered:
        return _resolve_okru(url, referer)
    if any(token in lowered for token in (".m3u8", ".mp4", "googleusercontent.com", "pixeldrain.com/api/file/", "archive.org/download/", "rumble.cloud/")):
        return {
            "url": url,
            "headers": {"User-Agent": USER_AGENT, "Referer": referer or url},
            "quality": "1080p" if ".m3u8" in lowered or ".mp4" in lowered else "HD",
        }
    return None
