import base64
import re
import unicodedata

import requests


TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
BASE_URL = "https://www.cinecalidad.vg"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
    "Referer": BASE_URL + "/",
}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _slug(text):
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"-+", "-", re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", "", text.lower()))).strip("-")


def _tmdb_metadata(tmdb_id, context):
    for language, label in (("es-MX", "Latino"), ("es-ES", "España"), ("en-US", "English")):
        try:
            response = requests.get(
                "https://api.themoviedb.org/3/movie/%s" % tmdb_id,
                params={"api_key": TMDB_API_KEY, "language": language},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            title = data.get("title")
            if title:
                _log(context, "[CineCalidad] TMDB %s: %s" % (label, title))
                return {
                    "title": title,
                    "original_title": data.get("original_title"),
                    "year": (data.get("release_date") or "")[:4],
                }
        except Exception as exc:
            _log(context, "[CineCalidad] TMDB %s failed: %s" % (label, exc))
    return None


def _direct_slug(title_slug, year):
    for suffix in ("", "-2", "-3"):
        url = "%s/pelicula/%s%s/" % (BASE_URL, title_slug, suffix)
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
            if response.status_code != 200:
                continue
            match = re.search(r"<h1[^>]*>[^<]*\((\d{4})\)[^<]*</h1>", response.text)
            found_year = match.group(1) if match else None
            if not year or not found_year or year == found_year:
                return url
        except Exception:
            continue
    return None


def _unpack_data_src(page_url, context):
    response = requests.get(page_url, headers=DEFAULT_HEADERS, timeout=8)
    response.raise_for_status()
    encoded = re.findall(r'data-src="([A-Za-z0-9+/=]{20,})"', response.text)
    urls = []
    for item in encoded:
        try:
            decoded = base64.b64decode(item).decode("utf-8")
            if decoded.startswith("http") and decoded not in urls:
                urls.append(decoded)
        except Exception:
            continue
    _log(context, "[CineCalidad] decoded %d embed urls" % len(urls))
    return urls


def _extract_file(url, context):
    try:
        response = requests.get(url, headers={**DEFAULT_HEADERS, "Referer": url}, timeout=10)
        response.raise_for_status()
        file_match = re.search(r'file\s*:\s*"([^"]+)"', response.text)
        if file_match:
            return {"url": file_match.group(1), "headers": {"User-Agent": USER_AGENT, "Referer": url}}
        iframe = re.search(r'<iframe[^>]+src="([^"]+)"', response.text)
        if iframe and iframe.group(1).startswith("http"):
            return None
    except Exception as exc:
        _log(context, "[CineCalidad] resolver fetch failed: %s" % exc)
    return None


def _server_label(url):
    lowered = str(url or "").lower()
    if "goodstream" in lowered:
        return "GoodStream"
    if any(token in lowered for token in ("hlswish", "streamwish", "strwish")):
        return "StreamWish"
    if "voe.sx" in lowered:
        return "VOE"
    if "vimeos" in lowered:
        return "Vimeos"
    return "Online"


def get_streams(context):
    tmdb_id = (context.get("ids") or {}).get("tmdb_id")
    if context.get("media_type") != "movie" or not tmdb_id:
        return []
    metadata = _tmdb_metadata(tmdb_id, context)
    if not metadata:
        return []
    page_url = _direct_slug(_slug(metadata["title"]), metadata.get("year"))
    if not page_url and metadata.get("original_title") and metadata.get("original_title") != metadata.get("title"):
        page_url = _direct_slug(_slug(metadata["original_title"]), metadata.get("year"))
    if not page_url:
        _log(context, "[CineCalidad] no slug match")
        return []
    embeds = _unpack_data_src(page_url, context)
    streams = []
    seen = set()
    for embed in embeds:
        resolved = _extract_file(embed, context)
        if not resolved or resolved["url"] in seen:
            continue
        seen.add(resolved["url"])
        streams.append({
            "title": "1080p · %s" % _server_label(embed),
            "type": "direct",
            "provider": "CineCalidad",
            "url": resolved["url"],
            "quality": "1080p",
            "languages": ["es"],
            "headers": resolved.get("headers") or {},
        })
    _log(context, "[CineCalidad] %d streams found" % len(streams))
    return streams
