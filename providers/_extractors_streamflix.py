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


def _resolve_voe(url, referer=None):
    response = requests.get(url, headers=_headers(referer or url), timeout=10, allow_redirects=True)
    response.raise_for_status()
    html = response.text
    encoded_json = re.search(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.S)
    if encoded_json:
        try:
            data = json.loads(encoded_json.group(1).strip())
            source = data.get("source")
            if source:
                return {
                    "url": source,
                    "headers": {"User-Agent": USER_AGENT, "Referer": response.url},
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
