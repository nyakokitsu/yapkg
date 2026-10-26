# yapkg

YAM package archives live in the `packages` branch. Open a pull request and add either:

- `packages/<id>/<version>.yap` — generic build;
- `packages/<id>/<version>/<target>.yap` — build for a specific speaker model.

Each archive declares supported speakers in `plugin.toml`:

```toml
[compatibility]
models = ["yandexplum"]
```

For a target-specific path, `<target>` must be included in `compatibility.models`. CI validates archives and publishes a format-3 static index to the `registry` branch. YAM selects the newest compatible build for the connected speaker; a generic build is used only when no model-specific build matches.
