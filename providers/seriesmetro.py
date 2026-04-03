import re
import unicodedata

import requests


TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
BASE_URL = "https://www3.seriesmetro.net"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
LANG_PRIORITY = ["latino", "lat", "castellano", "español", "esp", "vose", "sub", "subtitulado"]
LANG_LABELS = {
    "latino": "Latino",
    "lat": "Latino",
    "castellano": "Español",
    "español": "Español",
    "esp": "Español",
    "vose": "Subtitulado",
    "sub": "Subtitulado",
    "subtitulado": "Subtitulado",
}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _normalize_slug(text):
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"-+", "-", re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", " ", text.lower()))).strip("-")


def _tmdb_metadata(tmdb_id, media_type, context):
    languages = (("es-MX", "Latino"), ("es-ES", "España"), ("en-US", "English"))
    for language, label in languages:
        try:
            response = requests.get(
                "https://api.themoviedb.org/3/{}/{}".format(media_type, tmdb_id),
                params={"api_key": TMDB_API_KEY, "language": language},
                headers={"User-Agent": USER_AGENT},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            title = data.get("title") if media_type == "movie" else data.get("name")
            original_title = data.get("original_title") if media_type == "movie" else data.get("original_name")
            if title:
                _log(context, "[SeriesMetro] TMDB {}: {}".format(label, title))
                return {"title": title, "original_title": original_title}
        except Exception as exc:
            _log(context, "[SeriesMetro] TMDB {} failed: {}".format(label, exc))
    return None


def _fetch_by_slug(metadata, media_type, context):
    section = "pelicula" if media_type == "movie" else "serie"
    candidates = []
    if metadata.get("title"):
        candidates.append(_normalize_slug(metadata["title"]))
    if metadata.get("original_title") and metadata.get("original_title") != metadata.get("title"):
        candidates.append(_normalize_slug(metadata["original_title"]))
    for slug in candidates:
        if not slug:
            continue
        url = "%s/%s/%s/" % (BASE_URL, section, slug)
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
            if response.status_code == 200 and ("trembed=" in response.text or "data-post=" in response.text):
                _log(context, "[SeriesMetro] slug matched %s" % url)
                return {"url": url, "html": response.text}
        except Exception as exc:
            _log(context, "[SeriesMetro] slug fetch failed %s: %s" % (url, exc))
    return None


def _episode_url(page_url, html, season, episode):
    match = re.search(r'data-post="(\d+)"', html)
    if not match:
        return None
    response = requests.post(
        BASE_URL + "/wp-admin/admin-ajax.php",
        data={"action": "action_select_season", "post": match.group(1), "season": str(season)},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": page_url, **DEFAULT_HEADERS},
        timeout=8,
    )
    response.raise_for_status()
    for url in re.findall(r'href="([^"]+/capitulo/[^"]+)"', response.text):
        ep_match = re.search(r"temporada-(\d+)-capitulo-(\d+)", url)
        if ep_match and int(ep_match.group(1)) == int(season) and int(ep_match.group(2)) == int(episode):
            return url
    return None


def _fastream_unpack(payload, radix, table):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    def unbase(value):
        result = 0
        for char in value:
            index = alphabet.find(char)
            if index == -1:
                return None
            result = result * radix + index
        return result
    return re.sub(r"\b([0-9a-zA-Z]+)\b", lambda match: table[idx] if (idx := unbase(match.group(1))) is not None and idx < len(table) and table[idx] else match.group(1), payload)


def _m3u8_quality(url, headers):
    try:
        response = requests.get(url, headers=headers, timeout=3)
        response.raise_for_status()
        text = response.text
        best_w = best_h = 0
        for width, height in re.findall(r"RESOLUTION=(\d+)x(\d+)", text):
            width = int(width)
            height = int(height)
            if height > best_h:
                best_h, best_w = height, width
        if best_h >= 1080 or best_w >= 1920:
            return "1080p"
        if best_h >= 720 or best_w >= 1280:
            return "720p"
        if best_h >= 480:
            return "480p"
    except Exception:
        pass
    return "1080p"


def _resolve_fastream(embed_url, context):
    try:
        response = requests.get(
            embed_url,
            headers={"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"},
            timeout=10,
        )
        response.raise_for_status()
        packed = response.text
        match = re.search(r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('([\s\S]*?)',(\d+),(\d+),'([\s\S]*?)'\.split\('\|'\)\)\)", packed)
        if not match:
            return None
        unpacked = _fastream_unpack(match.group(1), int(match.group(2)), match.group(4).split("|"))
        file_match = re.search(r'file:"(https?://[^"]+\.m3u8[^"]*)"', unpacked)
        if not file_match:
            return None
        stream_url = file_match.group(1)
        headers = {"User-Agent": USER_AGENT, "Referer": "https://fastream.to/"}
        return {"url": stream_url, "quality": _m3u8_quality(stream_url, headers), "headers": headers}
    except Exception as exc:
        _log(context, "[SeriesMetro] fastream resolve failed: %s" % exc)
        return None


def _extract_streams(page_url, referer_url, context):
    response = requests.get(page_url, headers={**DEFAULT_HEADERS, "Referer": referer_url}, timeout=8)
    response.raise_for_status()
    html = response.text
    options = re.findall(r'href="#options-(\d+)"[^>]*>[\s\S]*?<span class="server">([\s\S]*?)</span>', html)
    trembed = re.findall(r'\?trembed=(\d+)(?:&#038;|&)trid=(\d+)(?:&#038;|&)trtype=(\d+)', html)
    if not options or not trembed:
        return []
    trid, trtype = trembed[0][1], trembed[0][2]
    streams = []
    for option_id, server_html in options:
        server_text = re.sub(r"<[^>]+>", "", server_html).strip().lower()
        lang_key = server_text.split("-")[-1].strip()
        lang_label = LANG_LABELS.get(lang_key, "Latino")
        try:
            embed_page = requests.get(
                "%s/?trembed=%s&trid=%s&trtype=%s" % (BASE_URL, option_id, trid, trtype),
                headers={**DEFAULT_HEADERS, "Referer": page_url},
                timeout=8,
            )
            embed_page.raise_for_status()
            iframe_match = re.search(r'<iframe[^>]*src="(https?://fastream\.to/[^"]+)"', embed_page.text, re.I)
            if not iframe_match:
                continue
            resolved = _resolve_fastream(iframe_match.group(1), context)
            if not resolved:
                continue
            streams.append({
                "title": "%s · %s · Fastream" % (resolved.get("quality") or "1080p", lang_label),
                "type": "direct",
                "provider": "SeriesMetro",
                "url": resolved["url"],
                "quality": resolved.get("quality") or "1080p",
                "languages": ["es"],
                "headers": resolved.get("headers") or {},
            })
            if lang_label == "Latino":
                break
        except Exception as exc:
            _log(context, "[SeriesMetro] embed %s failed: %s" % (option_id, exc))
    return streams


def get_streams(context):
    tmdb_id = (context.get("ids") or {}).get("tmdb_id")
    media_type = context.get("media_type")
    season = context.get("season")
    episode = context.get("episode")
    if not tmdb_id or media_type not in ("movie", "tv"):
        return []
    metadata = _tmdb_metadata(tmdb_id, media_type, context)
    if not metadata:
        return []
    match = _fetch_by_slug(metadata, media_type, context)
    if not match:
        _log(context, "[SeriesMetro] no slug match")
        return []
    page_url = match["url"]
    if media_type == "tv" and season and episode:
        page_url = _episode_url(match["url"], match["html"], season, episode)
        if not page_url:
            _log(context, "[SeriesMetro] episode S%sE%s not found" % (season, episode))
            return []
    streams = _extract_streams(page_url, match["url"], context)
    _log(context, "[SeriesMetro] %d streams found" % len(streams))
    return streams
