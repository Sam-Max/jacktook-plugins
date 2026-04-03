import base64
import re
from urllib.parse import quote, unquote

import requests


BASE_URL = "https://www.fuegocine.com"
SEARCH_BASE = BASE_URL + "/feeds/posts/default?alt=json&max-results=10&q="
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _headers(extra=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        "Referer": BASE_URL + "/",
    }
    if extra:
        headers.update(extra)
    return headers


def _resolve_vidsonic(embed_url):
    response = requests.get(
        embed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://vidsonic.net/",
        },
        timeout=10,
    )
    response.raise_for_status()
    html = response.text
    encoded_match = re.search(r"const\s+_0x1\s*=\s*'([^']+)'", html)
    if not encoded_match:
        return None

    clean = encoded_match.group(1).replace("|", "")
    decoded = "".join(chr(int(clean[index : index + 2], 16)) for index in range(0, len(clean), 2))
    media_url = decoded[::-1]
    if not media_url.startswith(("http://", "https://")):
        return None

    return {
        "url": media_url,
        "headers": {
            "User-Agent": USER_AGENT,
            "Referer": "https://vidsonic.net/",
        },
    }


def _search_entries(title, year, media_type, season, episode, context):
    query = "%s %s" % (title, year) if media_type == "movie" and year else title
    if media_type == "tv" and season and episode:
        query = "%s %sx%s" % (title, season, episode)
    for candidate in (query, title):
        try:
            response = requests.get(SEARCH_BASE + quote(candidate), headers=_headers(), timeout=10)
            response.raise_for_status()
            entries = (response.json() or {}).get("feed", {}).get("entry", []) or []
            _log(context, "[FuegoCine] search %r returned %d entries" % (candidate, len(entries)))
            if entries:
                return entries
        except Exception as exc:
            _log(context, "[FuegoCine] search %r failed: %s" % (candidate, exc))
    return []


def _extract_links(html):
    links = []
    match = re.search(r"const\s+_SV_LINKS\s*=\s*\[([\s\S]*?)\]\s*;", html)
    if not match:
        return links
    for entry in re.findall(r"\{([\s\S]*?)\}", match.group(1)):
        lang_match = re.search(r"lang\s*:\s*[\"']([^\"']+)[\"']", entry)
        name_match = re.search(r"name\s*:\s*[\"']([^\"']+)[\"']", entry)
        quality_match = re.search(r"quality\s*:\s*[\"']([^\"']+)[\"']", entry)
        url_match = re.search(r"url\s*:\s*[\"']([^\"']+)[\"']", entry)
        if not url_match:
            continue
        links.append(
            {
                "lang": (lang_match.group(1) if lang_match else "").strip(),
                "name": (name_match.group(1) if name_match else "").strip(),
                "quality": (quality_match.group(1) if quality_match else "HD").strip(),
                "url": url_match.group(1),
            }
        )
    return links


def _decode_url(url):
    while True:
        b64_match = re.search(r"[?&]r=([A-Za-z0-9+/=]+)", url)
        if b64_match:
            try:
                url = base64.b64decode(b64_match.group(1)).decode("utf-8")
                continue
            except Exception:
                pass
        link_match = re.search(r"[?&]link=([^&]+)", url)
        if link_match:
            try:
                url = unquote(link_match.group(1))
                continue
            except Exception:
                pass
        break
    return url


def _extract_drive_id(url):
    patterns = [
        r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)",
        r"drive\.google\.com/open\?.*id=([A-Za-z0-9_-]+)",
        r"drive\.google\.com/uc\?.*id=([A-Za-z0-9_-]+)",
        r"drive\.usercontent\.google\.com/download\?.*id=([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _server_name(url, fallback="FC"):
    lowered = str(url or "").lower()
    if "drive.google.com" in lowered or "drive.usercontent.google.com" in lowered:
        return "GoogleDrive"
    if "ok.ru" in lowered:
        return "OK.RU"
    if "vidsonic" in lowered:
        return "VidSonic"
    if "voe.sx" in lowered:
        return "VOE"
    if "pixeldrain.com" in lowered:
        return "PixelDrain"
    if "turbovid" in lowered:
        return "TurboVid"
    if "vidnest" in lowered:
        return "VidNest"
    return fallback


def get_streams(context):
    media_type = context.get("media_type")
    if media_type not in ("movie", "tv"):
        return []

    title = str(context.get("query") or "").strip()
    if not title:
        return []
    season = context.get("season")
    episode = context.get("episode")
    year = str((context.get("settings") or {}).get("year") or "")

    entries = _search_entries(title, year, media_type, season, episode, context)
    if not entries:
        return []

    streams = []
    seen_urls = set()
    for entry in entries:
        try:
            post_url = next((item.get("href") for item in (entry.get("link") or []) if item.get("rel") == "alternate"), None)
            if not post_url:
                continue
            post_url = post_url + ("&m=0" if "?" in post_url else "?m=0")
            post_response = requests.get(post_url, headers=_headers(), timeout=10)
            post_response.raise_for_status()
            links = _extract_links(post_response.text)
            _log(context, "[FuegoCine] %s yielded %d encoded links" % (post_url, len(links)))
            for link in links:
                decoded_url = _decode_url(link.get("url", ""))
                if not decoded_url:
                    continue
                target_url = decoded_url
                target_headers = {"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"}
                if "vidsonic.net/" in target_url:
                    resolved = _resolve_vidsonic(target_url)
                    if not resolved:
                        continue
                    target_url = resolved.get("url")
                    target_headers = resolved.get("headers") or target_headers
                if "drive.google.com" in target_url:
                    drive_id = _extract_drive_id(target_url)
                    if drive_id:
                        target_url = "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % drive_id
                if target_url in seen_urls:
                    continue
                seen_urls.add(target_url)
                quality = link.get("quality") or "HD"
                language = (link.get("lang") or "Lat").upper()
                server = _server_name(target_url, link.get("name") or "FC")
                streams.append(
                    {
                        "title": "%s · [%s] · %s" % (quality, language, server),
                        "type": "direct",
                        "provider": "FuegoCine",
                        "url": target_url,
                        "quality": quality,
                        "languages": ["es"],
                        "headers": target_headers,
                    }
                )
        except Exception as exc:
            _log(context, "[FuegoCine] entry processing failed: %s" % exc)
    _log(context, "[FuegoCine] %d streams found" % len(streams))
    return streams
