# Jacktook Plugins

Streamflix-based Python plugin repository for Jacktook.

## Current plugins

- `cinecalidad_sf`: movie and TV provider adapted from Streamflix's `CineCalidadProvider`.
- `flixlatam`: movie and TV provider adapted from Streamflix's `FlixLatamProvider`.

## Current direction

This repository is being rebuilt around the Streamflix provider/extractor model rather than the earlier Nuvio-style quick ports.

The first implementation is `flixlatam`, backed by a shared extractor layer for hosts such as:

- `goodstream`
- `streamwish` / `hlswish`
- `voe`
- `vidsonic`
- `ok.ru`

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
