"""Write the handoff page with a stand-in iframe, for a screenshot.

    uv run --project runtimes/browser python runtimes/browser/contrib/render_handoff_page.py <output>
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_tools_browser.handoff import render_handoff_page


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: render_handoff_page.py <output>", file=sys.stderr)
        raise SystemExit(2)
    page = render_handoff_page(
        "preview",
        9,
        9,
        "127.0.0.1",
        "Sign in to the example service",
        iframe_src="about:blank",
    )
    Path(sys.argv[1]).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
