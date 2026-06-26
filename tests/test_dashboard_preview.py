"""B-34: dashboard page previews must not show raw Markdown syntax."""

from not_dot_net.frontend.dashboard import _preview_line


def test_strips_bold_and_links():
    content = "# Title\n\nSee **important** notice at [our site](https://example.com)."
    assert _preview_line(content) == "See important notice at our site."


def test_strips_italic_and_inline_code():
    content = "Use _the_ `run()` helper and *now*."
    assert _preview_line(content) == "Use the run() helper and now."


def test_skips_heading_and_blank_lines():
    content = "## Heading\n\n\nFirst real line."
    assert _preview_line(content) == "First real line."


def test_empty_and_heading_only():
    assert _preview_line("") == ""
    assert _preview_line("# only a heading") == ""
