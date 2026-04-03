import re
from urllib.parse import quote

import requests


BASE_URL = "https://pelisgo.online"
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


def _is_probably_playable(url):
    lowered = str(url or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    blocked = (
        "unlimplay.com/play.php/embed/",
        "unlimplay.com/embed/",
        "unlimplay.com/play/embed/",
        "accounts.google.com/",
        "buzzheavier.com/",
        "mediafire.com/",
        "ranoz.gg/",
    )
    if any(token in lowered for token in blocked):
        return False
    if any(token in lowered for token in (
        ".m3u8",
        ".mp4",
        "pixeldrain.com/api/file/",
        "okcdn.ru/",
        "archive.org/download/",
        "googleusercontent.com/",
        "rumble.cloud/",
        "goodstream.one/",
        "fastream.to/",
    )):
        return True
    return False


def _normalize_title(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _search_paths(title, media_type, context):
    try:
        response = requests.get(
            BASE_URL + "/search",
            params={"q": title},
            headers=_headers(),
            timeout=10,
        )
        response.raise_for_status()
        html = response.text
        paths = re.findall(r'/(movies|series)/([a-z0-9\-]+)', html)
        target_prefix = "/movies/" if media_type == "movie" else "/series/"
        unique_paths = []
        for section, slug in paths:
            path = "/%s/%s" % (section, slug)
            if "/%s/" % section != target_prefix or path in unique_paths:
                continue
            unique_paths.append(path)
        _log(context, "[PelisGO] search returned %d candidate paths" % len(unique_paths))
        return unique_paths
    except Exception as exc:
        _log(context, "[PelisGO] search failed: %s" % exc)
        return []


def _pick_content_path(paths, year, context):
    found_path = None
    for path in paths[:5]:
        try:
            response = requests.get(BASE_URL + path, headers=_headers(), timeout=10)
            if response.status_code == 200 and year and str(year) in response.text:
                found_path = path
                break
        except Exception:
            continue
    if not found_path and paths:
        found_path = paths[0]
    _log(context, "[PelisGO] selected path: %s" % (found_path or "none"))
    return found_path


def _content_url(found_path, media_type, season, episode):
    if media_type == "tv":
        slug = found_path.split("/")[-1]
        return "%s/series/%s/temporada/%s/episodio/%s" % (BASE_URL, slug, season, episode)
    return BASE_URL + found_path


def _download_ids(content_url, context):
    try:
        response = requests.get(content_url, headers=_headers(), timeout=10)
        response.raise_for_status()
        ids = list(dict.fromkeys(re.findall(r"/download/([a-z0-9]+)", response.text)))
        _log(context, "[PelisGO] found %d download ids" % len(ids))
        return ids
    except Exception as exc:
        _log(context, "[PelisGO] content fetch failed: %s" % exc)
        return []


def _resolve_server(server, url):
    server = str(server or "").lower()
    if "vidsonic" in server or "vidsonic.net/" in url:
        resolved = _resolve_vidsonic(url)
        if resolved:
            return resolved
    if "google drive" in server or "googledrive" in server:
        match = re.search(r"/d/([^/?&#]+)", url) or re.search(r"[?&]id=([^&]+)", url)
        if match:
            return None
    if "pixeldrain" in server:
        match = re.search(r"pixeldrain\.com/u/([^?&#/]+)", url)
        if match:
            return {"url": "https://pixeldrain.com/api/file/%s?download" % match.group(1), "headers": {"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"}}
    if not _is_probably_playable(url):
        return None
    return {"url": url, "headers": {"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"}}


def get_streams(context):
    media_type = context.get("media_type")
    if media_type not in ("movie", "tv"):
        return []

    title = str(context.get("query") or "").strip()
    year = str((context.get("settings") or {}).get("year") or "")
    season = context.get("season")
    episode = context.get("episode")
    if not title:
        return []

    _log(context, "[PelisGO] searching title=%r media_type=%s" % (title, media_type))
    paths = _search_paths(title, media_type, context)
    if not paths:
        return []

    found_path = _pick_content_path(paths, year, context)
    if not found_path:
        return []

    content_url = _content_url(found_path, media_type, season, episode)
    download_ids = _download_ids(content_url, context)
    streams = []
    seen_urls = set()
    for download_id in download_ids:
        try:
            response = requests.get(
                BASE_URL + "/api/download/" + download_id,
                headers=_headers({"Accept": "application/json"}),
                timeout=10,
            )
            response.raise_for_status()
            data = response.json() or {}
            raw_url = data.get("url")
            if not raw_url:
                continue
            resolved = _resolve_server(data.get("server", ""), raw_url)
            if not resolved or not resolved.get("url") or resolved.get("url") in seen_urls:
                continue
            resolved_url = resolved.get("url")
            if not _is_probably_playable(resolved_url):
                _log(context, "[PelisGO] skipping non-playable url: %s" % resolved_url)
                continue
            seen_urls.add(resolved_url)
            quality = str(data.get("quality") or "HD")
            language = str(data.get("language") or "Lat").upper()
            server = str(data.get("server") or "PelisGO")
            streams.append(
                {
                    "title": "%s · [%s] · %s" % (quality, language, server),
                    "type": "direct",
                    "provider": "PelisGO",
                    "url": resolved_url,
                    "quality": quality,
                    "languages": ["es"],
                    "headers": resolved.get("headers") or {"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"},
                }
            )
        except Exception as exc:
            _log(context, "[PelisGO] download id %s failed: %s" % (download_id, exc))
    _log(context, "[PelisGO] %d streams found" % len(streams))
    return streams
