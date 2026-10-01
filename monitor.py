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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"
}

STOP_HEADINGS = {
    "our partners",
    "get in touch",
    "about galaxy cinemas",
    "legal",
    "follow us",
}

# elCinema Theater IDs for the Mansoura branches
# 3101077 = Galaxy Elgezeera Plaza Cinema (Mansoura M4aya)
# 3101269 = Galaxy Mall of University Cinema
ELCINEMA_BRANCHES = [
    ("Mansoura M4aya", "3101077"),
    ("Mansoura university mall", "3101269"),
]

# We keep the Facebook URL just to append it to the bottom of the Telegram message
FB_LINK_FOR_MESSAGE = "https://www.facebook.com/share/p/1GaEPBkyp9/"

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
# Mansoura branches via elCinema (The Optimal Solution)
# --------------------------------------------------------------------------
def fetch_mansoura_movies():
    """
    Scrapes the official elCinema.com pages for the Mansoura Galaxy branches.
    This bypasses Facebook completely and natively grabs English movie titles.
    """
    results = {}
    error = None
    
    for branch_name, theater_id in ELCINEMA_BRANCHES:
        # Using /en/ forces elCinema to load the English titles 
        url = f"https://elcinema.com/en/theater/{theater_id}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            
            movies = []
            # elCinema links movies using the /work/ID/ pattern
            for a in soup.find_all("a", href=True):
                if re.search(r'/work/\d+', a['href']):
                    title = normalize(a.get_text(" ", strip=True))
                    # Skip empty titles (images) or interface buttons
                    if title and title not in movies and not any(skip in title.lower() for skip in ['read more', 'cast', 'crew', 'photos', 'more']):
                        movies.append(title)
                        
            results[branch_name] = movies
        except Exception as e:
            print(f"Failed to fetch {branch_name} from elCinema: {e}", file=sys.stderr)
            error = f"Failed fetching {branch_name}"
            
    return results, error

# --------------------------------------------------------------------------
# Message Generation & State Management
# --------------------------------------------------------------------------
def generate_telegram_message(tanta_movies, mansoura_results, mansoura_error):
    msg = ["🎬 Galaxy monitor is live.\n"]
    
    # 1. Tanta Output
    msg.append("Tanta:")
    if tanta_movies:
        for m in tanta_movies:
            msg.append(f"• {m}")
    else:
        msg.append("No movies listed.")
    msg.append(f"\n{SITE_URL}\n")
    
    # 2. Mansoura Output
    if mansoura_error and not any(mansoura_results.values()):
        msg.append(f"⚠️ Couldn't fetch Mansoura movies this time.\n")
        msg.append(f"Reason: {mansoura_error}\n")
        msg.append(FB_LINK_FOR_MESSAGE)
    else:
        for branch_name, _ in ELCINEMA_BRANCHES:
            msg.append(f"{branch_name}:")
            movies = mansoura_results.get(branch_name, [])
            if movies:
                for m in movies:
                    msg.append(f"• {m}")
            else:
                msg.append("List here")
            msg.append("") # Empty line between branches
            
        msg.append(FB_LINK_FOR_MESSAGE)
        
        # Hardcoding the Friday note to maintain your exact requested format
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

    # 2. Fetch Mansoura Data
    mansoura_results = {b[0]: [] for b in ELCINEMA_BRANCHES}
    mansoura_error = None
    try:
        mansoura_results, mansoura_error = fetch_mansoura_movies()
    except Exception as e:
        mansoura_error = str(e)

    # 3. Check State for Updates
    current_state = {
        "tanta": tanta_movies,
        "mansoura_results": mansoura_results
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
    message = generate_telegram_message(tanta_movies, mansoura_results, mansoura_error)
    send_telegram(message)
    
    # 5. Save new state
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(current_state, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
