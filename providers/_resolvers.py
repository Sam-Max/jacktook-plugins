import re

import requests


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def resolve_vidsonic(embed_url, timeout=10):
    response = requests.get(
        embed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://vidsonic.net/",
        },
        timeout=timeout,
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
