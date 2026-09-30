"""Fetch pinned browser assets; runtime requires no CDN or Node build."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

DEST = Path(__file__).resolve().parent.parent / "race/static/vendor"
ASSETS = {
    "tabulator.min.js": "https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/js/tabulator.min.js",
    "tabulator.min.css": "https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/css/tabulator.min.css",
    "TABULATOR-LICENSE": "https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/LICENSE",
    "plotly.min.js": "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.1.0/plotly.min.js",
    "PLOTLY-LICENSE": "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.1.0/LICENSE",
}

if __name__ == "__main__":
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for filename, url in ASSETS.items():
        with urlopen(url, timeout=60) as response:
            content = response.read()
        (DEST / filename).write_bytes(content)
        manifest[filename] = {"url": url, "sha256": hashlib.sha256(content).hexdigest()}
        print(f"{filename}: {len(content):,} bytes")
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
