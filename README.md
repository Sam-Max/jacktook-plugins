# Jacktook Plugins

Python plugin repository for Jacktook.

## Current plugins

- `cinecalidad`: movie provider for CineCalidad.
- `pelisgo`: movie and TV provider backed by PelisGO direct download API responses.
- `lamovie`: movie and TV provider backed by LaMovie player endpoints.
- `fuegocine`: movie and TV provider backed by FuegoCine Blogger entries and external links.
- `seriesmetro`: movie and TV provider backed by SeriesMetro and Fastream.
- `xupalace`: movie and TV provider backed by XuPalace direct embed pages.
- `zoowomaniacos`: movie-only provider ported from the Nuvio Latino providers set.

## Repository manifest

When published to GitHub, use the raw `manifest.json` URL in Jacktook Settings > Plugins.

Example:

```text
https://raw.githubusercontent.com/<user>/jacktook-plugins/main/manifest.json
```

## Plugin contract

Each provider exposes:

```python
def get_streams(context):
    return []
```

`context` includes:

- `query`
- `ids`
- `mode`
- `media_type`
- `season`
- `episode`
- `settings`
- `log`

Each result must include `title` and one of `url` or `infoHash`.
