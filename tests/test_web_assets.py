"""Text-level guards on the frontend assets.

There is no JS test harness in this project (no package.json, no npm) and this
does not invent one. These are cheap string assertions that catch the failure
modes the copy layer actually has: a data-copy key with no entry in copy.js, a
mum key with no analyst fallback, and fx.js losing the CSS it depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture(scope="module")
def copy_js() -> str:
    return (WEB / "copy.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (WEB / "styles.css").read_text(encoding="utf-8")


def _persona_block(copy_js: str, name: str) -> str:
    """The source text of one persona object literal inside COPY."""
    start = copy_js.index(f"\n    {name}: {{")
    depth = 0
    for i in range(copy_js.index("{", start), len(copy_js)):
        if copy_js[i] == "{":
            depth += 1
        elif copy_js[i] == "}":
            depth -= 1
            if depth == 0:
                return copy_js[start : i + 1]
    raise AssertionError(f"unterminated persona block: {name}")


def _keys(block: str) -> set[str]:
    return set(re.findall(r'"([a-zA-Z][\w.]*)":', block))


def test_every_data_copy_key_exists_in_copy_js(index_html, copy_js):
    """A typo'd data-copy key would silently leave the authored text in place."""
    used = set(re.findall(r'data-copy(?:-aria|-placeholder)?="([^"]+)"', index_html))
    assert used, "no data-copy attributes found - did the markup change?"
    defined = _keys(_persona_block(copy_js, "analyst"))
    assert used <= defined, f"data-copy keys missing from copy.js: {sorted(used - defined)}"


def test_mum_keys_all_have_an_analyst_fallback(copy_js):
    """Mum falls back to analyst, so a mum-only key would resolve to undefined."""
    analyst = _keys(_persona_block(copy_js, "analyst"))
    mum = _keys(_persona_block(copy_js, "mum"))
    assert mum, "mum persona has no keys"
    assert mum <= analyst, f"mum keys with no analyst fallback: {sorted(mum - analyst)}"


def test_copy_js_builds_markup_only_through_the_escaping_helper(copy_js):
    """Crude XSS canary: copy.js must not hand-roll innerHTML or raw concat."""
    assert "innerHTML" not in copy_js
    # The only markup in copy.js is inside h`` tagged templates.
    for match in re.findall(r"<strong[^`]*", copy_js):
        assert "${" not in match or "h`" in copy_js


def test_scripts_are_each_loaded_once(index_html):
    """demo.js used to be included twice, which re-declared its top-level const."""
    for asset in ("copy.js", "app.js", "demo.js", "strategist.js"):
        assert index_html.count(f'src="/static/{asset}"') == 1, asset
    # fx.js is an ES module reached through a shim, not a plain script tag.
    assert index_html.count('type="module"') == 1
    assert 'src="/static/fx.js"' not in index_html
    assert 'import * as fx from "/static/fx.js"' in index_html


def test_element_ids_are_unique(index_html):
    """upload-label was duplicated, so only the first input was ever read."""
    ids = re.findall(r'\sid="([^"]+)"', index_html)
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate element ids: {sorted(duplicates)}"


def test_stylesheet_provides_what_fx_js_creates(styles_css):
    """fx.js builds these nodes; without styling they are invisible or break layout."""
    for selector in ("#fx-layer", "#money-cursor", ".fun-cursor"):
        assert selector in styles_css, selector
    assert "position: fixed" in styles_css


def test_mum_palette_outranks_the_prefers_dark_block(styles_css):
    """:root[data-mode] is needed to beat :root:not([data-theme="light"])."""
    assert ':root[data-mode="mum"]' in styles_css
    assert ':root[data-theme="dark"][data-mode="mum"]' in styles_css
    # The mum blocks must come after the media query they need to override.
    assert styles_css.index(":root:not([data-theme=") < styles_css.index(':root[data-mode="mum"]')


def test_mum_mode_keeps_the_colourblind_safe_pairing(styles_css):
    """The file's own note forbids returning --good to green. Keep it honest."""
    block = styles_css[styles_css.index(':root[data-mode="mum"]') :]
    block = block[: block.index("}")]
    good = re.search(r"--good:\s*(#[0-9a-fA-F]{6})", block)
    assert good, "mum palette does not set --good"
    red, green, blue = (int(good.group(1)[i : i + 2], 16) for i in (1, 3, 5))
    assert blue > red, f"--good {good.group(1)} looks green/red, not blue/teal"
