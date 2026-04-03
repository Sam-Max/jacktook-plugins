import re
import unicodedata

import requests


TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
BASE_URL = "https://proyectox.yoyatengoabuela.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TMDB_LANGUAGES = (
    ("es-MX", "Latino"),
    ("en-US", "English"),
)
BLOCKED_OKRU_IDS = {"332656282246", "1683045747235"}
OKRU_QUALITY_MAP = {
    "full": "1080p",
    "hd": "720p",
    "sd": "480p",
    "low": "360p",
    "lowest": "240p",
}
SEARCH_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*",
    "Connection": "keep-alive",
    "Referer": BASE_URL + "/",
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _normalize(value):
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.lower().strip()


def _tmdb_metadata(tmdb_id, media_type, context):
    session = requests.Session()
    search_terms = []
    title = None
    original_title = None
    year = ""
    for language, label in TMDB_LANGUAGES:
        try:
            response = session.get(
                "https://api.themoviedb.org/3/{}/{}".format(media_type, tmdb_id),
                params={"api_key": TMDB_API_KEY, "language": language},
                headers={"User-Agent": USER_AGENT},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            localized_title = data.get("title") if media_type == "movie" else data.get("name")
            localized_original_title = (
                data.get("original_title") if media_type == "movie" else data.get("original_name")
            )
            if not localized_title:
                continue
            if not title:
                title = localized_title
            if not original_title and localized_original_title:
                original_title = localized_original_title
            year = year or (data.get("release_date") or "")[:4]

            for candidate in (localized_title, localized_original_title):
                candidate = str(candidate or "").strip()
                if candidate and candidate not in search_terms:
                    search_terms.append(candidate)
            _log(context, "[Zoowomaniacos] TMDB {}: {}".format(label, localized_title))
        except Exception as exc:
            _log(context, "[Zoowomaniacos] TMDB {} failed: {}".format(label, exc))
    if not title:
        return None
    return {
        "title": title,
        "original_title": original_title,
        "year": year,
        "search_terms": search_terms,
    }


def _search_zoowomaniacos(query, context):
    try:
        response = requests.post(
            BASE_URL + "/alternativo3/server.php",
            data={
                "start": "0",
                "length": "10",
                "metodo": "ObtenerListaTotal",
                "search[value]": query,
                "searchPanes[a3][0]": "",
                "searchPanes[a4][0]": "",
                "searchPanes[a5][0]": "",
                "searchPanes[a6][0]": "",
            },
            headers=SEARCH_HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        return (response.json() or {}).get("data") or []
    except Exception as exc:
        _log(context, "[Zoowomaniacos] search failed: {}".format(exc))
        return []


def _pick_best_match(results, metadata):
    if not results:
        return None
    if len(results) == 1:
        return results[0]

    normalized_title = _normalize(metadata.get("title"))
    normalized_original = _normalize(metadata.get("original_title"))
    year = metadata.get("year")
    scored = []
    for item in results:
        candidate_title = _normalize((item.get("a2") or "").split("-")[0].strip())
        score = 0.0
        if candidate_title == normalized_title or candidate_title == normalized_original:
            score += 3.0
        elif candidate_title and (
            candidate_title in normalized_title or normalized_title in candidate_title
        ):
            score += 1.5
        if year and item.get("a4") == year:
            score += 1.0
        scored.append((score, item))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored[0][1] if scored else None


def _player_sources(item_id, context):
    try:
        response = requests.get(
            BASE_URL + "/testplayer.php",
            params={"id": item_id},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "Referer": BASE_URL + "/",
            },
            timeout=8,
        )
        response.raise_for_status()
        html = response.text
        all_urls = list(dict.fromkeys(re.findall(r'src="(https?://[^"]+)"', html)))
        okru_urls = []
        archive_urls = []
        for url in all_urls:
            if "ok.ru/videoembed/" in url:
                video_id = url.rstrip("/").split("/")[-1]
                if video_id not in BLOCKED_OKRU_IDS:
                    okru_urls.append(url)
            elif "archive.org" in url and url.endswith((".mp4", ".mkv", ".avi")):
                archive_urls.append(url)
        return {"okru": okru_urls, "archive": archive_urls}
    except Exception as exc:
        _log(context, "[Zoowomaniacos] player fetch failed: {}".format(exc))
        return {"okru": [], "archive": []}


def _resolve_okru(embed_url, context):
    try:
        response = requests.get(
            embed_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "Referer": "https://ok.ru/",
            },
            timeout=10,
        )
        response.raise_for_status()
        html = (
            response.text.replace(r"\&quot;", '"')
            .replace(r"\u0026", "&")
            .replace("\\", "")
        )
        if any(
            token in html
            for token in (
                "copyrightsRestricted",
                "COPYRIGHTS_RESTRICTED",
                "LIMITED_ACCESS",
                "notFound",
            )
        ):
            return None
        matches = re.findall(r'"name":"([^"]+)","url":"([^"]+)"', html)
        options = []
        for stream_type, url in matches:
            stream_type = stream_type.lower()
            if "mobile" in stream_type or not url.startswith("http"):
                continue
            options.append((stream_type, url))
        if not options:
            return None

        order = ["full", "hd", "sd", "low", "lowest"]
        options.sort(key=lambda item: order.index(item[0]) if item[0] in order else 99)
        stream_type, url = options[0]
        return {
            "url": url,
            "quality": OKRU_QUALITY_MAP.get(stream_type, stream_type.upper()),
            "headers": {"User-Agent": USER_AGENT, "Referer": "https://ok.ru/"},
        }
    except Exception as exc:
        _log(context, "[Zoowomaniacos] okru resolve failed: {}".format(exc))
        return None


def get_streams(context):
    media_type = context.get("media_type")
    tmdb_id = (context.get("ids") or {}).get("tmdb_id")
    if media_type != "movie" or not tmdb_id:
        return []

    _log(context, "[Zoowomaniacos] Searching TMDB {}".format(tmdb_id))
    metadata = _tmdb_metadata(tmdb_id, media_type, context)
    if not metadata:
        return []

    search_terms = []
    for candidate in metadata.get("search_terms") or []:
        if candidate and candidate not in search_terms:
            search_terms.append(candidate)
    query = str(context.get("query") or "").strip()
    if query and query not in search_terms:
        search_terms.append(query)

    selected = None
    for term in search_terms:
        results = _search_zoowomaniacos(term, context)
        if results:
            selected = _pick_best_match(results, metadata)
            if selected:
                break
    if not selected:
        _log(context, "[Zoowomaniacos] no match found")
        return []

    _log(
        context,
        "[Zoowomaniacos] matched {} ({})".format(selected.get("a2") or "unknown", selected.get("a4") or ""),
    )
    sources = _player_sources(selected.get("a1"), context)
    streams = []

    for embed_url in sources.get("okru") or []:
        resolved = _resolve_okru(embed_url, context)
        if not resolved:
            continue
        streams.append(
            {
                "title": "{} · OkRu".format(resolved.get("quality") or "1080p"),
                "type": "direct",
                "provider": "Zoowomaniacos",
                "url": resolved["url"],
                "quality": resolved.get("quality") or "1080p",
                "languages": ["es"],
                "headers": resolved.get("headers") or {},
            }
        )

    for url in sources.get("archive") or []:
        streams.append(
            {
                "title": "SD · Archive.org",
                "type": "direct",
                "provider": "Zoowomaniacos",
                "url": url,
                "quality": "SD",
                "languages": ["es"],
                "headers": {"User-Agent": USER_AGENT},
            }
        )

    _log(context, "[Zoowomaniacos] {} streams found".format(len(streams)))
    return streams
