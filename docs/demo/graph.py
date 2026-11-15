"""Graph part of the demo video: open the --cypher link in a real browser and record it.

    python graph.py LINK OUT_DIR

Signs in to Neo4j Browser, runs the query the link put in the editor, shows the graph full
screen, and records it. Writes OUT_DIR/graph.webm and OUT_DIR/graph-start.txt: the second
at which the full-screen graph appears, so the sign-in can be cut away.
"""

import sys
import time
from contextlib import suppress
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

PASSWORD = "dnsinv-local"  # the documented local default from docker-compose.yml
SIZE = {"width": 1920, "height": 1080}


def main(link: str, out: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(out), record_video_size=SIZE
        )
        page = context.new_page()
        started = time.monotonic()
        page.goto(link)

        password = page.locator("input[type=password]")
        password.fill(PASSWORD)
        password.press("Enter")
        # Run only once connected: a query run earlier fails with "No active connection".
        page.get_by_text("Connected to").first.wait_for(timeout=30_000)
        # A first-run tips popup opens a moment after connecting and covers the page; wait
        # for it and close it, or the Run click below never reaches the button.
        with suppress(PlaywrightTimeout):
            page.get_by_role("button", name="Dismiss").click(timeout=8_000)
        page.wait_for_timeout(500)

        graph = page.get_by_test_id("result-view-graphViz").first
        page.get_by_test_id("run-button").click()
        try:
            graph.wait_for(timeout=20_000)
        except PlaywrightTimeout:
            # Seen intermittently on a database created seconds earlier: the tips popup
            # opened late and swallowed the click. Close it again and run once more.
            with suppress(PlaywrightTimeout):
                page.get_by_role("button", name="Dismiss").click(timeout=3_000)
            page.get_by_test_id("run-button").click()
            try:
                graph.wait_for(timeout=40_000)
            except PlaywrightTimeout:
                page.screenshot(path=str(out / "graph-error.png"))
                raise
        page.wait_for_timeout(1_500)
        page.get_by_label("Enter full screen").first.click()
        page.get_by_label("Toggle graph details panel").first.click()
        page.wait_for_timeout(800)
        page.get_by_label("Zoom to fit").first.click()
        page.wait_for_timeout(600)
        shown_at = time.monotonic() - started

        page.get_by_label("Zoom in").first.click()
        page.wait_for_timeout(7_000)  # the graph settles into its final layout on camera

        video = page.video
        context.close()
        browser.close()
        assert video is not None
        Path(video.path()).rename(out / "graph.webm")
        (out / "graph-start.txt").write_text(f"{shown_at:.2f}\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
