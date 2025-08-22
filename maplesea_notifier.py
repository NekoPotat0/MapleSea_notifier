# file: maplesea_notifier.py
# Purpose: Poll MapleSEA website sections and post new items to a Discord channel via webhook.

import json, re
from pathlib import Path
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# ====== CONFIGURE THIS ======
# replace the WEBHOOK_URL line with this:
import os
WEBHOOK_URL = os.environ.get("MAPLESEA_WEBHOOK_URL", "")
if not WEBHOOK_URL:
    raise RuntimeError("Missing MAPLESEA_WEBHOOK_URL env var")
CHECK_PAGES = {
    "Updates": "https://www.maplesea.com/updates",
    "Notices": "https://www.maplesea.com/notices",
    "Announcements": "https://www.maplesea.com/announcements",
}
STATE_FILE = Path(".state/seen_maplesea.json")  # stores URLs already posted
USER_AGENT = {"User-Agent": "Mozilla/5.0 (MapleSEA Patch Monitor)"}
TIMEOUT = 30  # seconds
# ============================

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def extract_items(section_name, list_url):
    """Return a list of {section,title,url,date_hint} from a listing page."""
    r = requests.get(list_url, headers=USER_AGENT, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Match any of the known "view" URLs
    anchors = soup.select(
        "a[href*='/updates/view/'], a[href*='/notices/view/'], a[href*='/announcements/view/']"
    )

    items = []
    for a in anchors:
        href = a.get("href", "").strip()
        title = a.get_text(" ", strip=True)
        if not href or not title:
            continue

        # Convert relative to absolute
        if href.startswith("/"):
            href = "https://www.maplesea.com" + href

        # Try to pick up a nearby date text (best-effort; site formats change sometimes)
        date_hint = ""
        parent = a.find_parent()
        if parent:
            txt = parent.get_text(" ", strip=True)
            m = re.search(r"\[(\d{2}\.\d{2})\]|\b(\d+\s+days?\s+ago)\b|\b(\d{4}-\d{2}-\d{2})\b", txt)
            if m:
                date_hint = m.group(0)

        items.append({
            "section": section_name,
            "title": title,
            "url": href,
            "date_hint": date_hint
        })

    # Deduplicate by URL
    seen_urls, deduped = set(), []
    for it in items:
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        deduped.append(it)
    return deduped

def send_to_discord(item):
    """Send a Discord embed via webhook."""
    embed = {
        "title": f"{item['section']}: {item['title']}",
        "url": item["url"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "MapleSEA Web Monitor"},
    }
    if item.get("date_hint"):
        embed["description"] = f"Detected on page: {item['date_hint']}"

    payload = {"embeds": [embed]}
    r = requests.post(WEBHOOK_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

def run_once():
    state = load_state()
    already = set(state.get("seen", []))
    new_found = []

    for section, url in CHECK_PAGES.items():
        try:
            for it in extract_items(section, url):
                if it["url"] not in already:
                    new_found.append(it)
        except Exception as e:
            print(f"[WARN] Could not read {section} ({url}): {e}")

    # Post new items in the order we found them
    for it in new_found:
        try:
            send_to_discord(it)
            already.add(it["url"])
            print(f"[OK] Posted: {it['section']} — {it['title']}")
        except Exception as e:
            print(f"[WARN] Failed to post {it['url']}: {e}")

    save_state({"seen": list(already)})

if __name__ == "__main__":
    run_once()
    # If you prefer to loop continuously instead of using a scheduler,
    # you can replace the two lines above with a loop that sleeps:
    #
    # import time
    # while True:
    #     run_once()
    #     time.sleep(15 * 60)   # check every 15 minutes

