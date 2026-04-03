import re

import requests


TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
BASE_URL = "https://xupalace.org"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html", "Referer": BASE_URL + "/"}
LANG_LABELS = {0: "Latino", 1: "Español", 2: "Subtitulado"}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _imdb_id(tmdb_id, media_type, context):
    if str(tmdb_id).startswith("tt"):
        return tmdb_id
    try:
        response = requests.get(
            "https://api.themoviedb.org/3/{}/{}/external_ids".format(media_type, tmdb_id),
            params={"api_key": TMDB_API_KEY},
            headers={"User-Agent": USER_AGENT},
            timeout=5,
        )
        response.raise_for_status()
        return (response.json() or {}).get("imdb_id")
    except Exception as exc:
        _log(context, "[XuPalace] imdb lookup failed: %s" % exc)
        return None


def _fetch_embeds(imdb_id, media_type, season, episode, context):
    path = "/video/%s/" % imdb_id if media_type == "movie" else "/video/%s-%sx%s/" % (imdb_id, int(season), str(episode).zfill(2))
    url = BASE_URL + path
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
    response.raise_for_status()
    grouped = {}
    for embed_url, lang_code in re.findall(r"go_to_playerVast\(['\"](https?://[^'\"\s]+)['\"][^)]*\).*?data-lang=['\"](\d+)['\"]", response.text, re.S):
        grouped.setdefault(int(lang_code), [])
        if embed_url not in grouped[int(lang_code)]:
            grouped[int(lang_code)].append(embed_url)
    if not grouped:
        grouped[0] = list(dict.fromkeys(re.findall(r"go_to_playerVast\(['\"](https?://[^'\"]+)['\"]", response.text)))
    return grouped


def _extract_file(url):
    response = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"}, timeout=10)
    response.raise_for_status()
    text = response.text
    file_match = re.search(r'file\s*:\s*"([^"]+)"', text)
    if file_match:
        return {"url": file_match.group(1), "headers": {"User-Agent": USER_AGENT, "Referer": url}}
    hls_match = re.search(r'https?://[^"\']+\.m3u8[^"\']*', text)
    if hls_match:
        return {"url": hls_match.group(0), "headers": {"User-Agent": USER_AGENT, "Referer": url}}
    return None


def _server_label(url):
    lowered = str(url or "").lower()
    if any(token in lowered for token in ("streamwish", "hlswish", "vibuxer", "hglink")):
        return "StreamWish"
    if "voe.sx" in lowered:
        return "VOE"
    if any(token in lowered for token in ("vidhide", "filelions", "dintezuvio")):
        return "VidHide"
    if "filemoon" in lowered:
        return "Filemoon"
    return "Online"


def get_streams(context):
    tmdb_id = (context.get("ids") or {}).get("tmdb_id")
    media_type = context.get("media_type")
    season = context.get("season")
    episode = context.get("episode")
    if not tmdb_id or media_type not in ("movie", "tv"):
        return []
    imdb_id = _imdb_id(tmdb_id, media_type, context)
    if not imdb_id:
        return []
    embeds_by_lang = _fetch_embeds(imdb_id, media_type, season, episode, context)
    streams = []
    for lang_code in (0, 1, 2):
        for embed_url in embeds_by_lang.get(lang_code, []):
            try:
                resolved = _extract_file(embed_url)
                if not resolved:
                    continue
                streams.append({
                    "title": "1080p · %s · %s" % (LANG_LABELS.get(lang_code, "Latino"), _server_label(embed_url)),
                    "type": "direct",
                    "provider": "XuPalace",
                    "url": resolved["url"],
                    "quality": "1080p",
                    "languages": ["es"],
                    "headers": resolved.get("headers") or {},
                })
            except Exception as exc:
                _log(context, "[XuPalace] embed failed: %s" % exc)
        if streams:
            break
    _log(context, "[XuPalace] %d streams found" % len(streams))
    return streams
