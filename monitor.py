#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITE_URL = os.getenv("SITE_URL", "https://tanta.galaxy-cinema.com/")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TIMEOUT = int(os.getenv("TIMEOUT", "30"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GalaxyTantaWhatsOnMonitor/2.0)"
}

STOP_HEADINGS = {
    "our partners",
    "get in touch",
    "about galaxy cinemas",
    "legal",
    "follow us",
}


def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def heading_key(s: str) -> str:
    # Galaxy currently renders the heading as “What’s on” (curly apostrophe),
    # while older versions used "What's on".
    return normalize(s).lower().replace("’", "'")


def fetch_whats_on():
    r = requests.get(SITE_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    heading = None
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if heading_key(tag.get_text(" ", strip=True)) == "what's on":
            heading = tag
            break

    if heading is None:
        raise RuntimeError("Could not find the 'What's on' section")

    titles = []
    # The current page has the movie titles as h3 elements after the
    # “What’s on” heading and before “Our Partners”.
    for tag in heading.find_all_next(["h1", "h2", "h3", "h4", "h5", "h6"]):
        title = normalize(tag.get_text(" ", strip=True))
        key = heading_key(title)
        if key in STOP_HEADINGS:
            break
        if key == "what's on":
            continue
        # The movie titles in this section are h3s. Ignore other heading
        # levels so footer/navigation headings cannot become movie titles.
        if tag.name != "h3":
            continue
        if title and title not in titles:
            titles.append(title)

    if not titles:
        raise RuntimeError("Found 'What's on' but extracted no movie titles")

    return titles


def load_state():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(titles):
    STATE_FILE.write_text(
        json.dumps({"site": SITE_URL, "titles": titles}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        api,
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def check(force_report=False):
    current = fetch_whats_on()
    previous = load_state()

    if previous is None:
        save_state(current)
        if force_report or os.getenv("SEND_INITIAL_SNAPSHOT", "false").lower() == "true":
            send_telegram(
                "🎬 Galaxy Tanta monitor is live.\n\n"
                "Current What's On:\n"
                + "\n".join(f"• {x}" for x in current)
                + f"\n\n{SITE_URL}"
            )
        print("Initialized baseline with", len(current), "titles.")
        return {"added": current if force_report else [], "removed": [], "current": current, "initialized": True}

    old = previous.get("titles", [])
    old_set = set(old)
    current_set = set(current)

    added = [x for x in current if x not in old_set]
    removed = [x for x in old if x not in current_set]

    save_state(current)

    if added:
        msg = "🎬 Galaxy Tanta — NEW in What's On\n\n" + "\n".join(f"🆕 {x}" for x in added)
        if removed:
            msg += "\n\nRemoved since last check:\n" + "\n".join(f"• {x}" for x in removed)
        msg += f"\n\n{SITE_URL}"
        send_telegram(msg)
        print("NEW:", added)
    else:
        print("No new movies.")
        if removed:
            print("Removed:", removed)

    if force_report:
        report = (
            "🎬 Galaxy Tanta — manual check\n\n"
            "Current What's On:\n"
            + "\n".join(f"• {x}" for x in current)
        )
        if added:
            report += "\n\n🆕 New since last check:\n" + "\n".join(f"• {x}" for x in added)
        if removed:
            report += "\n\n❌ Removed since last check:\n" + "\n".join(f"• {x}" for x in removed)
        report += f"\n\n{SITE_URL}"
        send_telegram(report)

    return {"added": added, "removed": removed, "current": current, "initialized": False}


def main():
    force_report = "--report" in sys.argv
    check(force_report=force_report)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
