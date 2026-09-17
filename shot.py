"""
Screenshot a page, and report anything the browser complained about.

WHY THIS EXISTS
===============
On 2026-09-15 five bugs in the FPL engine were found only by rendering the
page: none of them were visible in the code and none were caught by a test.
The same thing happened here on 2026-09-17, twice. A stale template cache
made the columns stack and every primary button render in Bootstrap blue, and
both looked exactly like CSS bugs until the page was measured rather than
looked at.

So this prints the console errors as well as saving the image, because a
chart that silently fails to draw logs to a console nobody is reading.

    python shot.py http://127.0.0.1:8000/ out.png
    python shot.py http://127.0.0.1:8000/ out.png 390 --full   # phone width

NOT a runtime dependency, and deliberately not in requirements.txt: it would
install a browser on Railway for no reason. Install it when you need it:

    venv/Scripts/python -m pip install playwright
    venv/Scripts/python -m playwright install chromium
"""
import sys

from playwright.sync_api import sync_playwright


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    url, out = sys.argv[1], sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1280
    full = "--full" in sys.argv

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900},
                                device_scale_factor=2)
        noise = []
        page.on("pageerror", lambda e: noise.append(f"pageerror: {e}"))
        page.on("console",
                lambda m: noise.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)

        page.goto(url, wait_until="networkidle")
        page.screenshot(path=out, full_page=full)
        print(f"saved {out}")

        # The whole point. A page that looks fine and logs an error is a page
        # with a bug somebody has not noticed yet.
        for line in noise:
            print("CONSOLE", line)
        if not noise:
            print("console clean")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
