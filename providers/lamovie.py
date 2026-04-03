import re
import unicodedata

import requests


TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
BASE_URL = "https://la.movie"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
JSON_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _slug(text, year=""):
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    slug = re.sub(r"-+", "-", re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", " ", text.lower()))).strip("-")
    return "%s-%s" % (slug, year) if year else slug


def _tmdb_metadata(tmdb_id, media_type, context):
    for language, label in (("es-MX", "Latino"), ("en-US", "English")):
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
                return {
                    "title": title,
                    "original_title": original_title,
                    "year": (data.get("release_date") or data.get("first_air_date") or "")[:4],
                }
        except Exception as exc:
            _log(context, "[LaMovie] TMDB %s failed: %s" % (label, exc))
    return None


def _post_id(category, slug):
    url = "%s/%s/%s/" % (BASE_URL, category, slug)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
    if response.status_code != 200:
        return None
    match = re.search(r"rel=['\"]shortlink['\"]\s+href=['\"][^'\"]*\?p=(\d+)['\"]", response.text)
    return match.group(1) if match else None


def _discover_post(metadata, media_type):
    categories = ["peliculas"] if media_type == "movie" else ["series", "animes"]
    slugs = []
    if metadata.get("title"):
        slugs.append(_slug(metadata["title"], metadata.get("year")))
    if metadata.get("original_title") and metadata.get("original_title") != metadata.get("title"):
        slugs.append(_slug(metadata["original_title"], metadata.get("year")))
    for slug in slugs:
        for category in categories:
            try:
                post_id = _post_id(category, slug)
                if post_id:
                    return post_id
            except Exception:
                continue
    return None


def _episode_post_id(series_id, season, episode):
    response = requests.get(
        BASE_URL + "/wp-api/v1/single/episodes/list",
        params={"_id": series_id, "season": season, "page": 1, "postsPerPage": 50},
        headers=JSON_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    posts = ((response.json() or {}).get("data") or {}).get("posts") or []
    for item in posts:
        if str(item.get("season_number")) == str(season) and str(item.get("episode_number")) == str(episode):
            return item.get("_id")
    return None


def _extract_direct(embed_url):
    response = requests.get(embed_url, headers={"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"}, timeout=10)
    response.raise_for_status()
    match = re.search(r'file\s*:\s*"([^"]+)"', response.text)
    if not match:
        return None
    return {"url": match.group(1), "headers": {"User-Agent": USER_AGENT, "Referer": embed_url}}


def _server_name(url):
    lowered = str(url or "").lower()
    if "goodstream" in lowered:
        return "GoodStream"
    if any(token in lowered for token in ("streamwish", "hlswish", "strwish")):
        return "StreamWish"
    if "voe.sx" in lowered:
        return "VOE"
    if "vimeos.net" in lowered:
        return "Vimeos"
    return "Online"


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
    post_id = _discover_post(metadata, media_type)
    if not post_id:
        _log(context, "[LaMovie] no slug match")
        return []
    if media_type == "tv" and season and episode:
        post_id = _episode_post_id(post_id, season, episode)
        if not post_id:
            _log(context, "[LaMovie] episode S%sE%s not found" % (season, episode))
            return []
    response = requests.get(
        BASE_URL + "/wp-api/v1/player",
        params={"postId": post_id, "demo": 0},
        headers=JSON_HEADERS,
        timeout=8,
    )
    response.raise_for_status()
    embeds = ((response.json() or {}).get("data") or {}).get("embeds") or []
    streams = []
    seen = set()
    for embed in embeds:
        embed_url = embed.get("url")
        if not embed_url or embed_url in seen:
            continue
        seen.add(embed_url)
        try:
            resolved = _extract_direct(embed_url)
            if not resolved:
                continue
            quality = str(embed.get("quality") or "1080p")
            streams.append({
                "title": "%s · %s" % (quality, _server_name(embed_url)),
                "type": "direct",
                "provider": "LaMovie",
                "url": resolved["url"],
                "quality": quality,
                "languages": ["es"],
                "headers": resolved.get("headers") or {},
            })
        except Exception as exc:
            _log(context, "[LaMovie] embed failed: %s" % exc)
    _log(context, "[LaMovie] %d streams found" % len(streams))
    return streams
