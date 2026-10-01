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

# 1. facebookexternalhit: Tricks FB into serving the link-preview HTML containing the text
# 2. Normal browser: Fallback to parse the embedded JSON payload
FB_USER_AGENTS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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

def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s

def heading_key(s: str) -> str:
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
    for tag in heading.find_all_next(["h1", "h2", "h3", "h4", "h5", "h6"]):
        title = normalize(tag.get_text(" ", strip=True))
        key = heading_key(title)
        if key in STOP_HEADINGS:
            break
        if key == "what's on":
            continue
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
def fetch_facebook_text():
    """
    Attempts to fetch raw text from Facebook using multiple fallbacks.
    """
    for ua in FB_USER_AGENTS:
        try:
            headers = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
            r = requests.get(FB_POST_URL, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            html_content = r.text

            # Method 1: Look for OpenGraph description tag
            soup = BeautifulSoup(html_content, "html.parser")
            meta_desc = soup.find("meta", property="og:description")
            if meta_desc and meta_desc.get("content"):
                text = html_lib.unescape(meta_desc["content"])
                if "📍" in text or "🎞" in text:
                    return text

            # Method 2: Regex extract the JSON embedded message payload
            match = re.search(r'"message":\s*\{\s*"text":\s*"((?:[^"\\]|\\.)*?📍(?:[^"\\]|\\.)*?)"\s*\}', html_content)
            if match:
                raw_text = match.group(1)
                # Parse the JSON string to safely handle unicode and \n escapes
                parsed_text = json.loads(f'"{raw_text}"')
                return parsed_text
                
        except Exception as e:
            print(f"Failed extracting FB with UA {ua}: {e}", file=sys.stderr)
            continue
            
    raise RuntimeError("Could not extract post content from Facebook HTML due to anti-bot protection.")

def parse_facebook_text(text):
    results = {b[0]: [] for b in BRANCHES}
    current_branch = None
    friday_note = ""
    
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Check for branch location header
        if "📍" in line:
            current_branch = None
            for branch_name, keywords in BRANCHES:
                if any(kw in line for kw in keywords):
                    current_branch = branch_name
                    break
            continue
            
        # Extract movie name if inside a valid branch section
        if "🎞" in line and current_branch:
            # Remove emojis and clean up whitespace
            movie = re.sub(r'🎞️?', '', line).strip()
            if movie and movie not in results[current_branch]:
                results[current_branch].append(movie)
                
        # Attempt to capture the dynamic Friday note
        if "الجمعة" in line or "والجمعة" in line:
            friday_note = line

    return results, friday_note

# --------------------------------------------------------------------------
# Message Generation & State Management
# --------------------------------------------------------------------------
def generate_telegram_message(tanta_movies, fb_results, fb_note, fb_error):
    msg = ["🎬 Galaxy monitor is live.\n"]
    
    # 1. Tanta Output
    msg.append("Tanta:")
    if tanta_movies:
        for m in tanta_movies:
            msg.append(f"• {m}")
    else:
        msg.append("No movies listed.")
    msg.append(f"\n{SITE_URL}\n")
    
    # 2. Facebook Output (Mansoura)
    if fb_error:
        msg.append(f"⚠️ Couldn't read the Mansoura Facebook post this time.\n")
        msg.append(f"{FB_POST_URL}")
    else:
        for branch_name, _ in BRANCHES:
            msg.append(f"{branch_name}:")
            movies = fb_results.get(branch_name, [])
            if movies:
                for m in movies:
                    msg.append(f"• {m}")
            else:
                msg.append("No movies listed or failed to parse.")
            msg.append("") # Empty line between branches
            
        msg.append(f"{FB_POST_URL}")
        
        # Friday Note injection
        if fb_note:
            msg.append(fb_note)
        else:
            msg.append("والجمعة المواعيد تبدأ من الساعة 2:00 ظهرًا✨")
            
    return "\n".join(msg)

def send_telegram(text):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print(text)
        print("\n(Telegram credentials missing, printed to stdout instead)", file=sys.stderr)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram message: {e}", file=sys.stderr)

def main():
    # 1. Fetch Tanta Data
    tanta_movies = []
    try:
        tanta_movies = fetch_whats_on()
    except Exception as e:
        print(f"Error fetching Tanta: {e}", file=sys.stderr)

    # 2. Fetch Facebook Data
    fb_results = {b[0]: [] for b in BRANCHES}
    fb_note = ""
    fb_error = None
    try:
        fb_text = fetch_facebook_text()
        fb_results, fb_note = parse_facebook_text(fb_text)
    except Exception as e:
        fb_error = str(e)

    # 3. Check State for Updates
    current_state = {
        "tanta": tanta_movies,
        "fb_results": fb_results
    }
    
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                last_state = json.load(f)
                if current_state == last_state:
                    print("No changes detected in movies. Exiting.", file=sys.stderr)
                    sys.exit(0)
        except Exception:
            pass # Proceed if state file is corrupt

    # 4. Generate and send Message
    message = generate_telegram_message(tanta_movies, fb_results, fb_note, fb_error)
    send_telegram(message)
    
    # 5. Save new state
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(current_state, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
