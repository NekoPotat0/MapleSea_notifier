# file: maplesea_notifier.py
# MapleSEA -> Discord notifier (GitHub Actions friendly)
# - Scrapes Updates/Notices/Announcements
# - Persists "seen" URLs in .state/seen_maplesea.json (committed by workflow)
# - Handles Discord 429 rate limits
# - Caps backfill per run to avoid bursts

import os, re, json, time
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------- Config ----------
WEBHOOK_URL = os.environ.get("MAPLESEA_WEBHOOK_URL", "")
if not WEBHOOK_URL:
    raise RuntimeError("Missing MAPLESEA_WEBHOOK_URL env var")

CHECK_PAGES = {
    "Updates": "https://www.maplesea.com/updates",
    "News": "https://www.maplesea.com/news",
    "Notices": "https://www.maplesea.com/notices",
    "Events": "https://www.maplesea.com/events",
    "Announcements": "https://www.maplesea.com/announcements",
}

# store in a hidden folder the workflow will commit
STATE_FILE = Path(".state/seen_maplesea.json")
USER_AGENT = {"User-Agent": "MapleSEA Monitor via GitHub Actions/1.0"}
TIMEOUT = 30  # seconds

# backfill guard: post at most N new items per section per run
BACKFILL_CAP_PER_SECTION = 15
# polite spacing between posts (seconds)
POST_SPACING = 0.5
RECENCY_DAYS = 7  # e.g., 14 to only post last 14 days
# ---------------------------


# ----- state helpers -----
def ensure_state_file():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps({"seen": []}, indent=2), encoding="utf-8")

def load_state():
    ensure_state_file()
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": []}

def save_state(state):
    ensure_state_file()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
# -------------------------


def extract_items(section_name: str, list_url: str):
    """Return list[{section,title,url,date_hint}] scraped from a listing page."""
    r = requests.get(list_url, headers=USER_AGENT, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    anchors = soup.select(
    "a[href*='/announcements/view/'], "
    "a[href*='/news/view/'], "
    "a[href*='/events/view/'], "
    "a[href*='/notices/view/'], "
    "a[href*='/updates/view/'], "
    "a[href*='/newnameauction/view/']"
)


    items = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        title = a.get_text(" ", strip=True)
        if not href or not title:
            continue
        if href.startswith("/"):
            href = "https://www.maplesea.com" + href

        # try to extract a date-ish hint near the link (best-effort)
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

    # dedupe by url
    seen_urls, deduped = set(), []
    for it in items:
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        deduped.append(it)
    return deduped


def send_to_discord(item, max_retries=5):
    """Post one embed, handling Discord 429 rate limit."""
    embed = {
        "title": f"[{item['section']}] {item['title']}",
        "url": item["url"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "#maple-web-notices • MapleSEA Web Monitor"},
    }
    if item.get("date_hint"):
        embed["description"] = f"Detected on page: {item['date_hint']}"

    payload = {"embeds": [embed]}

    for attempt in range(max_retries):
        r = requests.post(WEBHOOK_URL, json=payload, timeout=TIMEOUT)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After") or r.headers.get("retry-after") or "2"
            try:
                sleep_s = float(retry_after)
            except ValueError:
                sleep_s = 2.0
            time.sleep(sleep_s)
            continue
        r.raise_for_status()
        time.sleep(POST_SPACING)
        return
    raise RuntimeError("Discord rate limit: failed after retries")


def run_once():
    state = load_state()
    already = set(state.get("seen", []))
    new_found_by_section = {}

    # collect new items per section
    for section, url in CHECK_PAGES.items():
        try:
            section_items = [it for it in extract_items(section, url) if it["url"] not in already]
            new_found_by_section[section] = section_items
        except Exception as e:
            print(f"[WARN] Could not read {section} ({url}): {e}")

    posted = 0

    # cap backfill and politely post
    for section, items in new_found_by_section.items():
        if not items:
            continue

        to_post = items
        to_skip = []

        for it in to_post:
            try:
                send_to_discord(it)
                already.add(it["url"])
                posted += 1
                print(f"[OK] Posted: {it['section']} — {it['title']}")
            except Exception as e:
                print(f"[WARN] Failed to post {it['url']}: {e}")

        # mark the rest as seen silently (avoid future backfill floods)
        for it in to_skip:
            already.add(it["url"])

    save_state({"seen": sorted(list(already))})
    print(f"[INFO] Done. Newly posted: {posted}. Total seen: {len(already)}")


if __name__ == "__main__":
    run_once()




