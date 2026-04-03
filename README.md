# Jacktook Plugins

Python plugin repository for Jacktook.

## Current plugins

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
