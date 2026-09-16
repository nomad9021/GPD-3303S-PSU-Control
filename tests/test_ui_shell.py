"""Structural checks on the application shell.

These are static assertions about the markup and stylesheet rather than
rendering tests, but they pin the mistakes that actually broke the layout: a
class name colliding with an existing one, and shell elements going missing.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "gpd3303s" / "web"
HTML = (WEB / "index.html").read_text("utf-8")
CSS = (WEB / "style.css").read_text("utf-8")
JS = (WEB / "app.js").read_text("utf-8")


@pytest.mark.parametrize(
    "selector",
    ['class="appbar"', 'class="workspace"', 'class="sidebar"',
     'class="instrument"', 'class="views"', 'class="statusbar"'],
)
def test_shell_regions_exist(selector):
    assert selector in HTML


def test_shell_classes_are_not_reused_by_a_component():
    """`.toolbar` was already the Monitor view's control bar.

    Reusing it for the window header let the later rule's margin push the
    header out of its grid row, so each shell class gets exactly one rule.
    """
    for name in ("appbar", "workspace", "sidebar", "instrument", "views", "statusbar"):
        blocks = re.findall(rf"^\.{name} \{{", CSS, re.MULTILINE)
        assert len(blocks) == 1, f".{name} has {len(blocks)} top-level rules"


def test_the_page_itself_does_not_scroll():
    """An app window scrolls its content, not the document."""
    assert re.search(r"html,\s*body\s*\{[^}]*overflow:\s*hidden", CSS)


def test_shell_is_a_three_row_grid():
    app_rule = re.search(r"^\.app \{(.*?)\}", CSS, re.MULTILINE | re.DOTALL).group(1)
    assert "grid-template-rows: var(--toolbar-h) 1fr var(--statusbar-h)" in app_rule
    for var in ("--toolbar-h", "--statusbar-h", "--sidebar-w"):
        assert re.search(rf"^\s*{var}:", CSS, re.MULTILINE), f"{var} is never defined"


def test_only_the_view_region_scrolls():
    views = re.search(r"^\.views \{(.*?)\}", CSS, re.MULTILINE | re.DOTALL).group(1)
    assert "overflow-y: auto" in views


def test_a_short_or_narrow_window_scrolls_as_one_column():
    """Pinning the instrument panel only works when there is room below it."""
    assert "@media (max-width: 880px), (max-height: 640px)" in CSS


def test_sidebar_navigates_every_view():
    tabs = set(re.findall(r'<button role="tab" data-tab="(\w+)"', HTML))
    panels = set(re.findall(r'id="panel-(\w+)"', HTML))
    assert tabs == panels, f"nav {tabs} does not match views {panels}"


def test_javascript_drives_the_sidebar_not_the_old_tab_strip():
    assert '.sidebar button[data-tab]' in JS
    assert '$$(".tabs button")' not in JS


@pytest.mark.parametrize(
    "element_id",
    ["status-connection", "status-output", "status-modes", "status-power",
     "status-recording", "footer-version"],
)
def test_status_bar_cells_exist(element_id):
    assert f'id="{element_id}"' in HTML
    assert element_id in JS, f"{element_id} is never updated"


def test_detect_button_is_wired_both_ways():
    assert 'id="detect-btn"' in HTML
    assert "/api/detect" in JS
