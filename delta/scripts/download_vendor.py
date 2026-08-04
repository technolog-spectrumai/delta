#!/usr/bin/env python3
"""
Download third-party JS/CSS vendor assets from CDNs → core/static/vendor/.
Update all Django HTML templates to use {% static 'vendor/...' %} instead of CDN URLs.

Usage:
    python faros/scripts/download_vendor.py               # download + patch templates
    python faros/scripts/download_vendor.py --download-only
    python faros/scripts/download_vendor.py --templates-only
"""

import argparse
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _toto_package_dir():
    """The toto package dir assets belong to: TOTO_SRC checkout, the legacy
    monorepo location, or the installed package (wheel or editable install —
    the Docker build writes into site-packages at build time)."""
    toto_src = os.environ.get("TOTO_SRC")
    if toto_src:
        # Suite checkout: core lives in one package's namespace portion.
        for portion in sorted(Path(toto_src).glob("packages/*/src/toto/core")):
            return portion.parent
        if (Path(toto_src) / "toto").is_dir():          # pre-split checkout
            return Path(toto_src).resolve() / "toto"
    elif (ROOT / "vendor" / "toto_libs" / "packages").is_dir():
        # The vendored subtree (absent in the Docker image, where the installed
        # package below is the target).
        for portion in sorted((ROOT / "vendor" / "toto_libs").glob("packages/*/src/toto/core")):
            return portion.parent
    legacy = ROOT / "toto" / "toto"
    if legacy.is_dir():
        return legacy
    try:
        import toto.core
    except ImportError:
        sys.exit(
            "download_vendor: the toto suite is not importable and no checkout "
            "was found — install it (toto_libs/scripts/install_toto.sh) or set TOTO_SRC."
        )
    return Path(toto.core.__file__).resolve().parent.parent


# VENDOR_DIR can be overridden via TOTO_VENDOR_DIR; by default it lands in the
# core/static/vendor dir of whatever toto tree is in use (checkout or install).
VENDOR_DIR = (
    Path(os.environ["TOTO_VENDOR_DIR"])
    if os.environ.get("TOTO_VENDOR_DIR")
    else _toto_package_dir() / "core" / "static" / "vendor"
)
TEMPLATES_ROOT = _toto_package_dir()

# Labels of assets that failed to download — a non-empty list makes the run exit
# non-zero so the Docker build (or reset.py) fails loudly instead of shipping an
# image with broken/missing vendored static.
_FAILED: list[str] = []

# Skip media/ uploads — those are user content, not app templates
SKIP_DIRS = {"media", ".git", "__pycache__", "node_modules"}


# ─────────────────────────────────────────────────────────────────────────────
# Canonical asset downloads: (url, vendor-relative-path)
# ─────────────────────────────────────────────────────────────────────────────
DOWNLOADS: list[tuple[str, str]] = [
    # Font Awesome 6.7.2 (webfonts fetched separately)
    ("https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css",
     "fontawesome/6.7.2/css/all.min.css"),
    ("https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/sprites/solid.svg",
     "fontawesome/6.7.2/sprites/solid.svg"),

    # Alpine.js 3.14.8
    ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js",
     "alpinejs/alpine.min.js"),

    # Chart.js 4.4.2
    ("https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js",
     "chartjs/chart.umd.min.js"),

    # Cytoscape 3.29.2
    ("https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js",
     "cytoscape/cytoscape.min.js"),

    # Ace Editor 1.36.4
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.js",               "ace/ace.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.min.js",           "ace/ace.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-python.min.js",   "ace/mode-python.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-latex.min.js",    "ace/mode-latex.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-bibtex.min.js",   "ace/mode-bibtex.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-xml.min.js",      "ace/mode-xml.min.js"),
    # mode-html is a self-contained bundle (embeds css/javascript/xml highlighters).
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-html.min.js",     "ace/mode-html.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/theme-textmate.min.js","ace/theme-textmate.min.js"),
    ("https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/theme-twilight.min.js","ace/theme-twilight.min.js"),

    # KaTeX 0.16.11 — formulas in memo slides and cyprian documents. The CSS
    # references its fonts relatively; those are pulled by _fetch_katex_fonts()
    # below, the same way FontAwesome's webfonts are. (toto.quizzes carries its
    # OWN katex copy under quizzes/vendor/ — that one ships in the wheel and is
    # not this. Both matter: WeasyPrint renders formula markup for the PDF
    # export, and without fonts on disk a formula is a row of empty boxes.)
    ("https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css",       "katex/katex.min.css"),
    ("https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js",        "katex/katex.min.js"),

    # QR Code
    ("https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js",
     "qrcodejs/qrcode.min.js"),

    # Paper.js 0.12.17
    ("https://cdnjs.cloudflare.com/ajax/libs/paper.js/0.12.17/paper-full.min.js",
     "paperjs/paper-full.min.js"),

    # HTMX 1.9.2
    ("https://unpkg.com/htmx.org@1.9.2",
     "htmx/htmx.min.js"),

    # Leaflet 1.9.4
    ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "leaflet/leaflet.css"),
    ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",  "leaflet/leaflet.js"),
    # Leaflet marker images
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",    "leaflet/images/marker-icon.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png", "leaflet/images/marker-icon-2x.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",  "leaflet/images/marker-shadow.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/layers.png",         "leaflet/images/layers.png"),
    ("https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png",      "leaflet/images/layers-2x.png"),

    # FullCalendar 6.1.9 — v6 bundles its CSS into the JS (no standalone .css).
    ("https://cdn.jsdelivr.net/npm/fullcalendar@6.1.9/index.global.min.js",
     "fullcalendar/fullcalendar.js"),

    # HLS.js 1.6.1 (replaces @latest)
    ("https://cdn.jsdelivr.net/npm/hls.js@1.6.1/dist/hls.min.js",
     "hlsjs/hls.min.js"),

    # Mermaid 10.9.3
    ("https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js",
     "mermaid/mermaid.min.js"),
    ("https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.esm.min.mjs",
     "mermaid/mermaid.esm.min.mjs"),

    # Reveal.js 5.1.0
    ("https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.css",        "reveal/reveal.css"),
    ("https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.js",         "reveal/reveal.js"),
    ("https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/theme/black.css",   "reveal/theme-black.css"),
    ("https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/theme/white.css",   "reveal/theme-white.css"),

    # Vega 5 + Vega-Lite 5
    ("https://cdn.jsdelivr.net/npm/vega@5/build/vega.min.js",               "vega/vega.min.js"),
    ("https://cdn.jsdelivr.net/npm/vega-lite@5/build/vega-lite.min.js",     "vega-lite/vega-lite.min.js"),

    # Univer 0.25.1 (Apache-2.0) — the Primula spreadsheet grid (teachers-only
    # on delta). The presets UMD reads react/react-dom/rxjs off the global
    # object (the header of the bundle documents the React contract); echarts is
    # not referenced by the sheets-core preset, so it is not vendored. Load
    # order in primula/edit.html: react → react-dom → rxjs → presets →
    # sheets-core → locale.
    ("https://unpkg.com/react@18.3.1/umd/react.production.min.js",          "univer/react.production.min.js"),
    ("https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js",  "univer/react-dom.production.min.js"),
    ("https://unpkg.com/rxjs@7.8.1/dist/bundles/rxjs.umd.min.js",           "univer/rxjs.umd.min.js"),
    ("https://unpkg.com/@univerjs/presets@0.25.1/lib/umd/index.js",         "univer/univer-presets.js"),
    ("https://unpkg.com/@univerjs/preset-sheets-core@0.25.1/lib/umd/index.js",
     "univer/preset-sheets-core.js"),
    ("https://unpkg.com/@univerjs/preset-sheets-core@0.25.1/lib/umd/locales/en-US.js",
     "univer/preset-sheets-core.en-US.js"),
    ("https://unpkg.com/@univerjs/preset-sheets-core@0.25.1/lib/index.css",
     "univer/preset-sheets-core.css"),
    # Apache-2.0 attribution rides with the vendored bundle.
    ("https://unpkg.com/@univerjs/presets@0.25.1/LICENSE",                  "univer/LICENSE"),
]


# ─────────────────────────────────────────────────────────────────────────────
# CDN URL → vendor-relative path (every variant found in templates)
# ─────────────────────────────────────────────────────────────────────────────
URL_TO_STATIC: dict[str, str] = {
    # Font Awesome
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css":
        "vendor/fontawesome/6.7.2/css/all.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css":
        "vendor/fontawesome/6.7.2/css/all.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/sprites/solid.svg":
        "vendor/fontawesome/6.7.2/sprites/solid.svg",

    # Alpine.js
    "https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js": "vendor/alpinejs/alpine.min.js",
    "https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js":             "vendor/alpinejs/alpine.min.js",

    "https://unpkg.com/alpinejs@3.x.x":                              "vendor/alpinejs/alpine.min.js",
    "https://unpkg.com/alpinejs":                                     "vendor/alpinejs/alpine.min.js",

    # Chart.js
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js": "vendor/chartjs/chart.umd.min.js",
    "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js":     "vendor/chartjs/chart.umd.min.js",
    "https://cdn.jsdelivr.net/npm/chart.js":                              "vendor/chartjs/chart.umd.min.js",

    # Cytoscape
    "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js": "vendor/cytoscape/cytoscape.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js": "vendor/cytoscape/cytoscape.min.js",
    "https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js":      "vendor/cytoscape/cytoscape.min.js",
    "https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js":                 "vendor/cytoscape/cytoscape.min.js",

    # Ace Editor
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.3/ace.js":               "vendor/ace/ace.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.js":               "vendor/ace/ace.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.min.js":           "vendor/ace/ace.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.3/mode-python.min.js":   "vendor/ace/mode-python.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-python.min.js":   "vendor/ace/mode-python.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.3/mode-latex.min.js":    "vendor/ace/mode-latex.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-latex.min.js":    "vendor/ace/mode-latex.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-bibtex.min.js":   "vendor/ace/mode-bibtex.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-xml.min.js":       "vendor/ace/mode-xml.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/mode-html.min.js":      "vendor/ace/mode-html.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.3/theme-textmate.min.js":"vendor/ace/theme-textmate.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/theme-textmate.min.js":"vendor/ace/theme-textmate.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.3/theme-twilight.min.js":"vendor/ace/theme-twilight.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/theme-twilight.min.js":"vendor/ace/theme-twilight.min.js",

    # QR Code
    "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js": "vendor/qrcodejs/qrcode.min.js",

    # Paper.js
    "https://cdnjs.cloudflare.com/ajax/libs/paper.js/0.12.17/paper-full.min.js": "vendor/paperjs/paper-full.min.js",

    # HTMX
    "https://unpkg.com/htmx.org@1.9.2": "vendor/htmx/htmx.min.js",

    # Leaflet
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css": "vendor/leaflet/leaflet.css",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js":  "vendor/leaflet/leaflet.js",

    # FullCalendar (v6 bundles CSS into the JS)
    "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.9/index.global.min.js":  "vendor/fullcalendar/fullcalendar.js",

    # HLS.js
    "https://cdn.jsdelivr.net/npm/hls.js@latest": "vendor/hlsjs/hls.min.js",

    # Mermaid
    "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js":      "vendor/mermaid/mermaid.min.js",
    "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs": "vendor/mermaid/mermaid.esm.min.mjs",

    # Reveal.js
    "https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.css":       "vendor/reveal/reveal.css",
    "https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.js":        "vendor/reveal/reveal.js",
    "https://cdn.jsdelivr.net/npm/reveal.js/dist/theme/black.css":  "vendor/reveal/theme-black.css",
    "https://cdn.jsdelivr.net/npm/reveal.js/dist/theme/white.css":  "vendor/reveal/theme-white.css",

    # Vega
    "https://cdn.jsdelivr.net/npm/vega@5":      "vendor/vega/vega.min.js",
    "https://cdn.jsdelivr.net/npm/vega-lite@5": "vendor/vega-lite/vega-lite.min.js",

    # These stay on CDN — skipped intentionally:
    #   https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js  (MathJax: dynamic loader, can't self-host easily)
    #   https://cdn.tailwindcss.com                             (build-time CDN, not a static file)
    #   https://{s}.basemaps.cartocdn.com/...                   (map tile service, not a static asset)
}


def _strip_sourcemap_comment(data: bytes, suffix: str) -> bytes:
    """Remove a trailing `//# sourceMappingURL=…` (JS) / `/*# … */` (CSS) comment.

    The .map files are not vendored, and whitenoise's CompressedManifestStaticFilesStorage
    *hard-fails* collectstatic when a referenced source map can't be found (e.g. the
    CDN's chart.umd.min.js trails `//# sourceMappingURL=chart.umd.js.map`). The regex is
    line-anchored (?m:^) so it only strips the real end-of-file comment and never an
    occurrence embedded in code — paper.js, for instance, builds a data-URI sourcemap at
    runtime via a mid-line string literal, which must be left intact.
    """
    if suffix in (".js", ".mjs"):
        data = re.sub(rb"(?m)^//# sourceMappingURL=.*\r?$", b"", data)
    elif suffix == ".css":
        data = re.sub(rb"(?m)^/\*# sourceMappingURL=.*\*/\s*\r?$", b"", data)
    return data


def _fetch(url: str, dest: Path, label: str) -> bool:
    """Download url → dest. Skip if already exists. Returns True if downloaded."""
    if dest.exists():
        print(f"  ✓ skip  {label}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ fetch {label} ...", end="", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "toto-vendor-downloader/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        data = _strip_sourcemap_comment(data, dest.suffix)
        dest.write_bytes(data)
        print(f" {len(data):,} bytes")
        return True
    except Exception as exc:
        print(f" FAILED: {exc}")
        _FAILED.append(label)
        return False


def _fetch_font_awesome_webfonts() -> None:
    """Parse downloaded FA CSS and download all referenced webfonts."""
    css_path = VENDOR_DIR / "fontawesome" / "6.7.2" / "css" / "all.min.css"
    if not css_path.exists():
        print("  ⚠ FA CSS not found, skipping webfonts")
        return
    css = css_path.read_text(encoding="utf-8")
    webfonts = re.findall(r'url\(\.\./webfonts/([^)\'\"]+\.(?:woff2|woff|ttf|eot|svg))\)', css)
    webfonts = list(dict.fromkeys(webfonts))  # deduplicate, preserve order
    base_url = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/webfonts/"
    for filename in webfonts:
        _fetch(
            base_url + filename,
            VENDOR_DIR / "fontawesome" / "6.7.2" / "webfonts" / filename,
            f"fontawesome/webfonts/{filename}",
        )


def _fetch_katex_fonts() -> None:
    """Parse the downloaded KaTeX CSS and pull every font it references.

    Same shape as the FontAwesome fetcher above.
    """
    css_path = VENDOR_DIR / "katex" / "katex.min.css"
    if not css_path.exists():
        print("  ⚠ KaTeX CSS not found, skipping fonts")
        return
    css = css_path.read_text(encoding="utf-8")
    fonts = re.findall(r"url\(fonts/([^)'\"]+\.(?:woff2|woff|ttf))\)", css)
    fonts = list(dict.fromkeys(fonts))
    base_url = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/fonts/"
    for filename in fonts:
        _fetch(base_url + filename,
               VENDOR_DIR / "katex" / "fonts" / filename,
               f"katex/fonts/{filename}")


def download_all() -> None:
    print("\n📦 Downloading vendor assets...")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for url, rel_path in DOWNLOADS:
        _fetch(url, VENDOR_DIR / rel_path, rel_path)
    _fetch_font_awesome_webfonts()
    _fetch_katex_fonts()
    if _FAILED:
        print(f"\n❌ {len(_FAILED)} vendor asset(s) failed to download:")
        for label in _FAILED:
            print(f"     - {label}")
        sys.exit(1)


def _iter_templates():
    """Yield all .html template files under toto/toto, skipping media and generated dirs."""
    for path in TEMPLATES_ROOT.rglob("*.html"):
        parts = set(path.relative_to(TEMPLATES_ROOT).parts)
        if parts & SKIP_DIRS:
            continue
        yield path


def _replace_cdn_urls(content: str) -> tuple[str, int]:
    """Replace all known CDN URLs with {% static '...' %}. Returns (new_content, count)."""
    count = 0
    # Sort longest first so more-specific URLs match before shorter prefixes
    for cdn_url, static_path in sorted(URL_TO_STATIC.items(), key=lambda x: -len(x[0])):
        if cdn_url in content:
            content = content.replace(cdn_url, "{{% static '{path}' %}}".format(path=static_path))
            count += 1
    return content, count


def _strip_security_attrs(content: str) -> str:
    """Remove integrity/crossorigin/referrerpolicy from tags that now use vendor static paths."""
    def _clean_tag(m: re.Match) -> str:
        tag = m.group(0)
        tag = re.sub(r'\s+integrity="[^"]*"', "", tag)
        tag = re.sub(r"\s+integrity='[^']*'", "", tag)
        tag = re.sub(r'\s+crossorigin(?:="[^"]*")?', "", tag)
        tag = re.sub(r"\s+crossorigin(?:='[^']*')?", "", tag)
        tag = re.sub(r'\s+referrerpolicy="[^"]*"', "", tag)
        tag = re.sub(r"\s+referrerpolicy='[^']*'", "", tag)
        return tag

    return re.sub(
        r"<(?:script|link)\b[^>]*\{%\s*static\s*['\"]vendor/[^>]*>",
        _clean_tag,
        content,
        flags=re.DOTALL,
    )


def _ensure_load_static(content: str) -> str:
    """Add {% load static %} if the template now uses {% static %} but doesn't load it."""
    if "{% static " not in content:
        return content
    if "{% load static %}" in content or "{% load static%}" in content:
        return content

    # After {% extends %} if present, otherwise at the top
    extends_match = re.search(r'\{%[-\s]*extends\s+[\'"][^\'"]*[\'"]\s*[-\s]*%\}', content)
    if extends_match:
        insert_at = extends_match.end()
        return content[:insert_at] + "\n{% load static %}" + content[insert_at:]

    # Before first {% load %} or at the very top
    load_match = re.search(r'\{%-?\s*load\s+', content)
    if load_match:
        insert_at = load_match.start()
        return content[:insert_at] + "{% load static %}\n" + content[insert_at:]

    return "{% load static %}\n" + content


def update_templates() -> None:
    print("\n✏️  Patching templates...")
    patched = 0
    for tmpl in sorted(_iter_templates()):
        original = tmpl.read_text(encoding="utf-8")
        content, replacements = _replace_cdn_urls(original)
        if replacements == 0:
            continue
        content = _strip_security_attrs(content)
        content = _ensure_load_static(content)
        if content != original:
            tmpl.write_text(content, encoding="utf-8")
            rel = tmpl.relative_to(ROOT)
            print(f"  patched {rel}  ({replacements} URL{'s' if replacements != 1 else ''})")
            patched += 1
    print(f"  → {patched} template(s) updated")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download-only",  action="store_true", help="Download assets only, skip template patching")
    parser.add_argument("--templates-only", action="store_true", help="Patch templates only, skip downloading")
    args = parser.parse_args()

    if not args.templates_only:
        download_all()
    if not args.download_only:
        update_templates()
    print("\n✅ Done.")


if __name__ == "__main__":
    main()
