import re
from urllib.parse import quote

import requests

from _extractors_streamflix import extract_video


BASE_URL = "https://www.cinecalidad.ec"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HTML_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
}


def _log(context, message):
    logger = context.get("log")
    if callable(logger):
        logger(message)


def _search(query, media_type, context):
    url = "%s/page/1?s=%s" % (BASE_URL, quote(query))
    response = requests.get(url, headers=HTML_HEADERS, timeout=12)
    response.raise_for_status()
    html = response.text
    items = []
    for article in re.findall(r'<article class="item[^"]*"[\s\S]*?</article>', html):
        href_match = re.search(r'<a href="([^"]+)"', article)
        title_match = re.search(r'<img[^>]+alt="([^"]+)"', article)
        if not href_match or not title_match:
            continue
        href = href_match.group(1)
        if media_type == "movie" and "/ver-pelicula/" not in href:
            continue
        if media_type == "tv" and "/ver-serie/" not in href:
            continue
        items.append({"href": href, "title": title_match.group(1).strip()})
    _log(context, "[CineCalidadSF] search returned %d items for %r" % (len(items), query))
    return items


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _pick_item(items, query, context):
    normalized_query = _normalize(query)
    scored = []
    for item in items:
        title = _normalize(item.get("title"))
        score = 0
        if title == normalized_query:
            score += 3
        elif normalized_query in title or title in normalized_query:
            score += 1
        scored.append((score, item))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    selected = scored[0][1] if scored else None
    _log(context, "[CineCalidadSF] selected item: %s" % (selected.get("href") if selected else "none"))
    return selected


def _episode_url(show_url, season, episode, context):
    response = requests.get(show_url, headers=HTML_HEADERS, timeout=12)
    response.raise_for_status()
    html = response.text
    for block in re.findall(r'<div class="mark-1">([\s\S]*?)</div>\s*</div>', html):
        numerando = re.search(r'<div class="numerando">([^<]+)</div>', block)
        link = re.search(r'<a href="([^"]+)"', block)
        if not numerando or not link:
            continue
        text = numerando.group(1)
        match = re.search(r"S(\d+)-E(\d+)", text)
        if match and int(match.group(1)) == int(season) and int(match.group(2)) == int(episode):
            return link.group(1)
    _log(context, "[CineCalidadSF] episode S%sE%s not found" % (season, episode))
    return None


def _get_servers(page_url, context):
    response = requests.get(page_url, headers=HTML_HEADERS, timeout=12)
    response.raise_for_status()
    html = response.text
    servers = []
    for option in re.findall(r'<li[^>]+data-option="([^"]+)"[^>]*>([\s\S]*?)</li>', html):
        server_url, label_html = option
        label = re.sub(r"<[^>]+>", "", label_html).strip()
        if "trailer" in label.lower():
            continue
        servers.append({"url": server_url, "name": label or "CineCalidad", "referer": page_url})
    _log(context, "[CineCalidadSF] extracted %d server candidates" % len(servers))
    return servers


def get_streams(context):
    media_type = context.get("media_type")
    query = str(context.get("query") or "").strip()
    season = context.get("season")
    episode = context.get("episode")
    if media_type not in ("movie", "tv") or not query:
        return []

    items = _search(query, media_type, context)
    selected = _pick_item(items, query, context)
    if not selected:
        return []

    page_url = selected["href"]
    if media_type == "tv" and season and episode:
        page_url = _episode_url(page_url, season, episode, context)
        if not page_url:
            return []

    servers = _get_servers(page_url, context)
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
                    "title": "%s · %s" % (resolved.get("quality") or "HD", server.get("name") or "CineCalidad"),
                    "type": "direct",
                    "provider": "CineCalidad",
                    "url": resolved["url"],
                    "quality": resolved.get("quality") or "HD",
                    "languages": ["es"],
                    "headers": resolved.get("headers") or {},
                }
            )
        except Exception as exc:
            _log(context, "[CineCalidadSF] server resolve failed (%s): %s" % (server.get("name"), exc))
    _log(context, "[CineCalidadSF] %d streams found" % len(results))
    return results
