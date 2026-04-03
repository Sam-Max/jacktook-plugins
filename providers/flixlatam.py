import base64
import json
import re
import unicodedata
from urllib.parse import quote

import requests

from _extractors_streamflix import extract_video


BASE_URL = "https://flixlatam.com"
TMDB_API_KEY = "439c478a771f35c05022f9feabcca01c"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
JSON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*",
    "Referer": BASE_URL + "/",
}
HTML_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL + "/",
}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _normalize(value):
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip().lower()


def _tmdb_metadata(tmdb_id, media_type, context):
    if not tmdb_id:
        return None
    labels = (("es-MX", "Latino"), ("en-US", "English"))
    terms = []
    year = ""
    for language, label in labels:
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
            year = year or (data.get("release_date") or data.get("first_air_date") or "")[:4]
            for candidate in (title, original_title):
                candidate = str(candidate or "").strip()
                if candidate and candidate not in terms:
                    terms.append(candidate)
            if title:
                _log(context, "[FlixLatam] TMDB %s: %s" % (label, title))
        except Exception as exc:
            _log(context, "[FlixLatam] TMDB %s failed: %s" % (label, exc))
    return {"terms": terms, "year": year} if terms else None


def _search(query, context):
    response = requests.get(
        "%s/search?s=%s" % (BASE_URL, quote(query)),
        headers=HTML_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    html = response.text
    items = []
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>.*?<h3>(.*?)</h3>', html, re.S):
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        items.append({"href": href, "title": clean_title})
    _log(context, "[FlixLatam] search returned %d items for %r" % (len(items), query))
    return items


def _pick_show(items, query, media_type, metadata, context):
    normalized_terms = [_normalize(query)]
    if metadata:
        for candidate in metadata.get("terms") or []:
            normalized = _normalize(candidate)
            if normalized and normalized not in normalized_terms:
                normalized_terms.append(normalized)
    year = str((metadata or {}).get("year") or "")
    media_token = "/serie/" if media_type == "tv" else "/pelicula/"
    candidates = []
    for item in items:
        href = item.get("href", "")
        if media_token not in href and not (media_type == "tv" and "/anime/" in href):
            continue
        title = _normalize(item.get("title"))
        score = 0
        for normalized_query in normalized_terms:
            if title == normalized_query:
                score += 4
            elif title and (title in normalized_query or normalized_query in title):
                score += 2
        if year and year in href:
            score += 1
        if re.search(r"\bgoat\b", title) and any(term == "goat" for term in normalized_terms):
            score += 1
        candidates.append((score, item))
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    selected = candidates[0][1] if candidates else None
    if candidates:
        _log(context, "[FlixLatam] top matches: %s" % ", ".join(["%s (%s)" % (entry[1].get("title"), entry[0]) for entry in candidates[:3]]))
    _log(context, "[FlixLatam] selected show: %s" % (selected.get("href") if selected else "none"))
    return selected


def _show_page(href):
    if href.startswith("http"):
        return href
    return BASE_URL.rstrip("/") + "/" + href.strip("/") + "/"


def _episode_page(show_url, season, episode, html, context):
    script_match = re.search(r"const seasonsJson = (\{[\s\S]*?\});", html)
    if not script_match:
        return None
    try:
        seasons = json.loads(script_match.group(1))
    except Exception:
        return None
    entries = seasons.get(str(season)) or []
    for item in entries:
        if str(item.get("episode")) == str(episode):
            return "%sseason/%s/episode/%s" % (show_url.rstrip("/") + "/", season, episode)
    _log(context, "[FlixLatam] episode S%sE%s not found in seasonsJson" % (season, episode))
    return None


def _decode_server_url(data_server):
    try:
        return base64.b64decode(data_server).decode("utf-8")
    except Exception:
        return None


def _player_page_from_token(token):
    encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8").strip()
    response = requests.get("%s/player/%s" % (BASE_URL, encoded), headers=HTML_HEADERS, timeout=12)
    response.raise_for_status()
    script_match = re.search(r"(https?://[^\s'\"]+)", response.text)
    return script_match.group(1) if script_match else None


def _extract_servers_from_embed(embed_url, context):
    response = requests.get(embed_url, headers={**HTML_HEADERS, "Referer": BASE_URL + "/"}, timeout=12)
    response.raise_for_status()
    html = response.text
    servers = []

    script_match = re.search(r"dataLink\s*=\s*(\[[\s\S]*?\]);", html)
    if script_match:
        try:
            payload = json.loads(script_match.group(1))
            for item in payload:
                language = item.get("video_language") or "LAT"
                for embed in item.get("sortedEmbeds") or []:
                    server_name = embed.get("servername") or "Server"
                    if server_name.lower() == "download":
                        continue
                    encrypted_link = embed.get("link") or ""
                    parts = encrypted_link.split(".")
                    if len(parts) == 3:
                        try:
                            payload_part = parts[1]
                            padding = len(payload_part) % 4
                            if padding:
                                payload_part += "=" * (4 - padding)
                            link_data = json.loads(base64.b64decode(payload_part).decode("utf-8"))
                            final_link = link_data.get("link")
                            if final_link:
                                servers.append({"url": final_link, "name": "%s [%s]" % (server_name, language), "referer": embed_url})
                        except Exception:
                            continue
        except Exception as exc:
            _log(context, "[FlixLatam] dataLink parse failed: %s" % exc)

    for onclick, server_name in re.findall(r"go_to_playerVast\(\s*'([^']+)'[^\)]*\).*?<span>([^<]+)</span>", html, re.S):
        if "download" in server_name.lower() or "1fichier" in server_name.lower():
            continue
        servers.append({"url": onclick, "name": server_name.strip(), "referer": embed_url})

    iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"', html)
    if iframe_match:
        iframe_url = iframe_match.group(1)
        host = re.sub(r"^https?://([^/]+)/?.*$", r"\1", iframe_url).replace("www.", "")
        servers.append({"url": iframe_url, "name": host.split(".")[0].title(), "referer": embed_url})

    deduped = []
    seen = set()
    for server in servers:
        if server["url"] in seen:
            continue
        seen.add(server["url"])
        deduped.append(server)
    return deduped


def _get_servers(page_url, context):
    response = requests.get(page_url, headers=HTML_HEADERS, timeout=12)
    response.raise_for_status()
    html = response.text
    iframe_urls = re.findall(r'<iframe[^>]+src="([^"]+)"', html)
    servers = []
    for iframe_url in iframe_urls:
        try:
            servers.extend(_extract_servers_from_embed(iframe_url, context))
        except Exception as exc:
            _log(context, "[FlixLatam] iframe processing failed: %s" % exc)
    _log(context, "[FlixLatam] extracted %d server candidates" % len(servers))
    if servers:
        _log(context, "[FlixLatam] server candidates: %s" % ", ".join(["%s -> %s" % (server.get("name"), server.get("url")) for server in servers[:8]]))
    return servers, html


def get_streams(context):
    media_type = context.get("media_type")
    query = str(context.get("query") or "").strip()
    season = context.get("season")
    episode = context.get("episode")
    if media_type not in ("movie", "tv") or not query:
        return []

    metadata = _tmdb_metadata((context.get("ids") or {}).get("tmdb_id"), media_type, context)
    items = _search(query, context)
    selected = _pick_show(items, query, media_type, metadata, context)
    if not selected:
        return []
    show_url = _show_page(selected["href"])
    page_url = show_url
    page_response = requests.get(show_url, headers=HTML_HEADERS, timeout=12)
    page_response.raise_for_status()
    page_html = page_response.text

    if media_type == "tv" and season and episode:
        episode_url = _episode_page(show_url, season, episode, page_html, context)
        if not episode_url:
            return []
        page_url = episode_url

    servers, _ = _get_servers(page_url, context)
    results = []
    seen_urls = set()
    for server in servers:
        try:
            resolved = extract_video(server["url"], server_name=server.get("name"), referer=server.get("referer") or page_url)
            if not resolved or not resolved.get("url") or resolved["url"] in seen_urls:
                continue
            seen_urls.add(resolved["url"])
            results.append(
                {
                    "title": "%s · %s" % (resolved.get("quality") or "HD", server.get("name") or "FlixLatam"),
                    "type": "direct",
                    "provider": "FlixLatam",
                    "url": resolved["url"],
                    "quality": resolved.get("quality") or "HD",
                    "languages": ["es"],
                    "headers": resolved.get("headers") or {},
                }
            )
        except Exception as exc:
            _log(context, "[FlixLatam] server resolve failed (%s): %s" % (server.get("name"), exc))
    _log(context, "[FlixLatam] %d streams found" % len(results))
    return results
