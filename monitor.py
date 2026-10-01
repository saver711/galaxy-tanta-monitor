#!/usr/bin/env python3
import html as html_lib
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITE_URL = os.getenv("SITE_URL", "https://tanta.galaxy-cinema.com/")
FB_POST_URL = os.getenv("FB_POST_URL", "https://www.facebook.com/share/p/1GaEPBkyp9")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TIMEOUT = int(os.getenv("TIMEOUT", "30"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GalaxyTantaWhatsOnMonitor/2.0)"
}

# Facebook serves the post text (Open Graph tags / embedded JSON) to link-preview
# crawlers, so we try that user agent first and a normal browser second.
FB_USER_AGENTS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

STOP_HEADINGS = {
    "our partners",
    "get in touch",
    "about galaxy cinemas",
    "legal",
    "follow us",
}

# Branch label shown in Telegram -> keywords found in the 📍 line of the post.
BRANCHES = [
    ("Mansoura M4aya", ("الجزيرة", "جزيرة", "المشاية")),
    ("Mansoura university mall", ("الجامعة", "الأولمبية", "الاولمبية")),
]

# Optional Arabic -> English title overrides (add more any time).
TITLE_EN = {
    "محمود التاني": "Mahmoud Eltany",
    "ريد فلاج": "Red Flag",
    "شيش دوو": "Shish Dou",
}


def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def heading_key(s: str) -> str:
    # Galaxy currently renders the heading as “What’s on” (curly apostrophe),
    # while older versions used "What's on".
    return normalize(s).lower().replace("’", "'")


# --------------------------------------------------------------------------
# Tanta website
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Facebook post (Mansoura branches)
# --------------------------------------------------------------------------
def extract_post_text(page_html: str) -> str:
    """Pull the post text out of a Facebook page (OG tags or embedded JSON)."""
    candidates = []

    soup = BeautifulSoup(page_html, "html.parser")
    for attrs in (
        {"property": "og:description"},
        {"name": "twitter:description"},
        {"name": "description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidates.append(tag["content"])

    # Full text embedded in the page's JSON blobs (not truncated like OG).
    for m in re.finditer(r'"message":\{"text":"((?:[^"\\]|\\.)*)"', page_html):
        try:
            candidates.append(json.loads(f'"{m.group(1)}"'))
        except ValueError:
            pass

    candidates = [html_lib.unescape(c) for c in candidates]
    candidates = [c for c in candidates if "🎞" in c or "📍" in c]
    if not candidates:
        raise RuntimeError("Facebook page did not contain the post text")
    return max(candidates, key=len)


def fetch_facebook_text() -> str:
    last_error = None
    for ua in FB_USER_AGENTS:
        try:
            r = requests.get(
                FB_POST_URL,
                headers={"User-Agent": ua, "Accept-Language": "ar,en;q=0.8"},
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            r.raise_for_status()
            r.encoding = "utf-8"
            return extract_post_text(r.text)
        except Exception as e:  # try the next user agent
            last_error = e
    raise RuntimeError(f"Could not read the Facebook post: {last_error}")


def clean_title(raw: str) -> str:
    t = raw.lstrip("\ufe0f").strip()
    t = t.split("\n")[0]
    t = re.split(r"\(", t)[0]  # drop showtimes in parentheses
    t = normalize(t).strip(":-– ")
    t = re.sub(r"\s+:", ":", t)
    fmt = ""
    m = re.search(r"\s*\b(2D|3D)\s*$", t, flags=re.I)
    if m:
        fmt = f" ({m.group(1).upper()})"
        t = t[: m.start()].strip()
    t = TITLE_EN.get(t, t)
    if t.isascii() and t.isupper():
        t = t.title()
    return t + fmt


def branch_label(location_line: str) -> str:
    for label, keywords in BRANCHES:
        if any(k in location_line for k in keywords):
            return label
    return normalize(location_line.lstrip("📍\ufe0f ").split("\n")[0])


def friday_note(text: str):
    m = re.search(r"(?:و)?الجمعة[^✨\n]*", text)
    if not m:
        return None
    line = normalize(m.group(0))
    t = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(ظهر|مساء|عصر|صباح)", line)
    if t:
        hour, minute, period = int(t.group(1)), t.group(2) or "00", t.group(3)
        suffix = "AM" if period == "صباح" else "PM"
        return f"Friday: showtimes start from {hour}:{minute} {suffix}"
    return line


def parse_facebook_post(text: str):
    branches = {}
    current = None
    for chunk in re.split(r"(?=📍|🎞)", text):
        chunk = chunk.strip()
        if chunk.startswith("📍"):
            current = branch_label(chunk)
            branches.setdefault(current, [])
        elif chunk.startswith("🎞") and current is not None:
            title = clean_title(chunk[1:])
            if title and title not in branches[current]:
                branches[current].append(title)

    branches = {k: v for k, v in branches.items() if v}
    if not branches:
        raise RuntimeError("Found the Facebook post but could not parse any movies")
    return {"branches": branches, "friday": friday_note(text)}


def fetch_facebook():
    return parse_facebook_post(fetch_facebook_text())


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def load_state():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(tanta, fb):
    data = {
        "site": SITE_URL,
        "titles": tanta,  # kept as "titles" so older state files still load
        "facebook": fb,
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
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


def bullets(items):
    return [f"• {x}" for x in items]


def build_listing(header, tanta, fb, fb_error=None):
    lines = [header, "", "Tanta:", *bullets(tanta), "", SITE_URL]

    if fb:
        for label, titles in fb["branches"].items():
            lines += ["", f"{label}:", *bullets(titles)]
        if fb.get("friday"):
            lines += ["", f"📌 {fb['friday']}"]
        lines += ["", FB_POST_URL]
    elif fb_error:
        lines += ["", "⚠️ Couldn't read the Mansoura Facebook post this time.", "", FB_POST_URL]

    return "\n".join(lines)


def build_changes(changes):
    """changes: {section: (added, removed)} -> list of lines."""
    lines = []
    for section, (added, removed) in changes.items():
        if not added and not removed:
            continue
        lines += ["", f"{section}:"]
        lines += [f"🆕 {x}" for x in added]
        lines += [f"❌ {x}" for x in removed]
    return lines


def diff(old, current):
    old_set, cur_set = set(old), set(current)
    return (
        [x for x in current if x not in old_set],
        [x for x in old if x not in cur_set],
    )


# --------------------------------------------------------------------------
# Main check
# --------------------------------------------------------------------------
def check(force_report=False):
    send_initial = force_report or os.getenv("SEND_INITIAL_SNAPSHOT", "false").lower() == "true"

    tanta = fetch_whats_on()

    fb, fb_error = None, None
    try:
        fb = fetch_facebook()
    except Exception as e:
        fb_error = str(e)
        print(f"WARNING: Facebook check failed: {e}", file=sys.stderr)

    previous = load_state()
    prev_tanta = previous.get("titles") if previous else None
    prev_fb = (previous or {}).get("facebook")  # {"branches": {...}, "friday": ...}

    # Keep the last known Facebook data if this run couldn't read the post.
    state_fb = fb if fb is not None else prev_fb

    # Figure out what changed. A source with no previous data is a baseline,
    # not a "new movies" alert.
    changes = {}
    if prev_tanta is not None:
        changes["Tanta"] = diff(prev_tanta, tanta)

    if fb is not None and prev_fb is not None:
        prev_branches = prev_fb.get("branches", {})
        for label, titles in fb["branches"].items():
            if label in prev_branches:
                changes[label] = diff(prev_branches[label], titles)
        for label, titles in prev_branches.items():
            if label not in fb["branches"]:
                changes[label] = diff(titles, [])

    save_state(tanta, state_fb)

    first_run = previous is None
    fb_baseline = fb is not None and prev_fb is None

    has_changes = any(a or r for a, r in changes.values())
    any_added = any(a for a, _ in changes.values())

    # 1) Baseline / "live" message
    if (first_run or fb_baseline) and send_initial and not force_report:
        send_telegram(build_listing("🎬 Galaxy monitor is live.", tanta, fb, fb_error))
        print("Initialized baseline.")

    # 2) New-movie alert
    if any_added:
        msg = "🎬 Galaxy — NEW in What's On"
        msg += "\n".join([""] + build_changes(changes))
        msg += f"\n\n{SITE_URL}"
        if fb is not None:
            msg += f"\n{FB_POST_URL}"
        send_telegram(msg)
        print("Changes:", changes)
    elif has_changes:
        print("Removed only:", changes)
    else:
        print("No new movies.")

    # 3) Manual /check report
    if force_report:
        report = build_listing("🎬 Galaxy monitor — manual check", tanta, fb, fb_error)
        extra = build_changes(changes)
        if extra:
            report += "\n\nChanges since last check:" + "\n".join(extra)
        send_telegram(report)

    return {
        "changes": changes,
        "tanta": tanta,
        "facebook": fb,
        "initialized": first_run,
    }


def main():
    force_report = "--report" in sys.argv
    check(force_report=force_report)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
