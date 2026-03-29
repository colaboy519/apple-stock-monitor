#!/usr/bin/env python3
"""Apple Store Stock Monitor for Singapore.

Monitors:
1. In-store pickup availability for specific SKUs
2. Apple product page changes (detects new model launches)
3. Sends Telegram + macOS notifications when changes detected

Usage:
    python3 monitor.py              # Run once
    python3 monitor.py --loop       # Poll every 2 minutes
    python3 monitor.py --loop 60    # Poll every 60 seconds
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

SGT = timezone(timedelta(hours=8))

# ── Configuration ──────────────────────────────────────────────────────

CONFIG = {
    "country": "sg",
    "location": "238857",  # Orchard Road postal code
    "poll_interval": 120,  # seconds

    # Telegram (set via env vars or edit here)
    "telegram_bot_token": os.environ.get("APPLE_MONITOR_TG_TOKEN", ""),
    "telegram_chat_id": os.environ.get("APPLE_MONITOR_TG_CHAT", ""),

    # SKUs to monitor — 64GB+ models only (Mac Mini, Mac Studio, MacBook Pro)
    "skus": {
        # ── Mac Studio (2025) — 64GB is a standard retail SKU ──
        "MHQH4ZP/A": "Mac Studio M4 Max 64GB/1TB",
        # ── Mac Studio M3 Ultra 96GB ──
        "MU973ZP/A": "Mac Studio M3 Ultra 96GB",
        # ── MacBook Pro 16" M5 Max 48GB (closest retail to 64GB) ──
        "MGE94ZP/A": "MBP 16\" M5 Max 48GB/2TB Silver",
        "MGEE4ZP/A": "MBP 16\" M5 Max 48GB/2TB Black",
    },

    # CTO configs to monitor delivery estimates (no in-store pickup)
    # Format: product code + sorted option codes → delivery-message API
    "cto_configs": {
        "mac_mini_m4pro_14c_64gb_1tb_1gbe": {
            "label": "Mac Mini M4 Pro 14c/20c 64GB/1TB/1GbE",
            "product": "MAC_MINI_2024_ROC_U",
            "options": "065-CJQ6,065-CK4M,065-CK0Q,065-CGYX,ZP065-CJXQ,065-CH3P,065-CGYF,065-CH3T",
            "base_part": "Z1JV",  # CTO base part number
        },
        "mac_mini_m4pro_14c_64gb_1tb_10gbe": {
            "label": "Mac Mini M4 Pro 14c/20c 64GB/1TB/10GbE",
            "product": "MAC_MINI_2024_ROC_U",
            "options": "065-CJQ6,065-CK4M,065-CK0Q,065-CGYY,ZP065-CJXQ,065-CH3P,065-CGYF,065-CH3T",
            "base_part": "Z1JV",
        },
        "mac_mini_m4pro_12c_64gb_512gb_1gbe": {
            "label": "Mac Mini M4 Pro 12c/16c 64GB/512GB/1GbE",
            "product": "MAC_MINI_2024_ROC_U",
            "options": "065-CJQ6,065-CK4M,065-CK0L,065-CGYX,ZP065-CJXQ,065-CH3P,065-CGYD,065-CH3T",
            "base_part": "Z1JV",
        },
        "mac_studio_m4max_16c_64gb_512gb": {
            "label": "Mac Studio M4 Max 16c/40c 64GB/512GB (CTO)",
            "product": "MAC_STUDIO_2025_ROC_BB",
            "options": "065-CGWP,065-CGXJ,065-CKT9,065-CGXH,ZP065-CKTJ,065-CGXT,065-CGWJ,065-CGXW",
            "base_part": "Z1CD",
        },
    },

    # Pages to monitor for changes (new product launches / refreshes)
    "watch_pages": [
        {"url": "https://www.apple.com/sg/shop/buy-mac/mac-mini", "label": "Mac Mini Buy"},
        {"url": "https://www.apple.com/sg/shop/buy-mac/mac-studio", "label": "Mac Studio Buy"},
        {"url": "https://www.apple.com/sg/shop/buy-mac/macbook-pro", "label": "MacBook Pro Buy"},
        {"url": "https://www.apple.com/sg/mac-mini/", "label": "Mac Mini Product"},
        {"url": "https://www.apple.com/sg/mac-studio/", "label": "Mac Studio Product"},
        {"url": "https://www.apple.com/sg/macbook-pro/", "label": "MacBook Pro Product"},
    ],

    # Only notify if delivery is within this many days
    "notify_within_days": 30,
}

STATE_DIR = Path(__file__).parent / ".state"
STATE_DIR.mkdir(exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────────

ALLOWED_HOSTS = frozenset([
    "www.apple.com",
    "api.telegram.org",
])


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def _curl_fetch(url: str, timeout: int = 15, post_data: Optional[str] = None) -> str:
    """Fetch a URL using curl subprocess. Only allows HTTPS to allowlisted hosts."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        log(f"  Blocked fetch to disallowed URL: {url}")
        return ""
    cmd = ["curl", "-sf", "--max-time", str(timeout), "-L",
           "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"]
    if post_data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", post_data]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"  Fetch error for {url} (curl exit {result.returncode})")
        return ""
    return result.stdout


def macos_notify(title: str, message: str):
    subprocess.run([
        "osascript", "-e",
        f'display notification "{message}" with title "{title}" sound name "Glass"'
    ], capture_output=True)


def telegram_send(text: str):
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    result = _curl_fetch(url, post_data=payload)
    if not result:
        log("  Telegram send failed")


def _is_within_days(date_str: str, days: int) -> bool:
    """Check if a delivery date string falls within N days from now.

    Handles formats like 'Tue 31/03/2026', '07/05/2026 – 14/05/2026',
    and '27/07/2026 – 11/08/2026'. Uses the earliest date found.
    """
    if not date_str:
        return False
    # Extract all DD/MM/YYYY patterns
    dates = re.findall(r'(\d{2}/\d{2}/\d{4})', date_str)
    if not dates:
        # If no parseable date, assume it might be "today" style text
        return True
    try:
        earliest = min(datetime.strptime(d, "%d/%m/%Y").replace(tzinfo=SGT) for d in dates)
        now = datetime.now(SGT)
        return (earliest - now).days <= days
    except ValueError:
        return True  # Can't parse — err on side of notifying


def notify(title: str, message: str):
    log(f"  🔔 {title}: {message}")
    macos_notify(title, message)
    telegram_send(f"*{title}*\n{message}")


# ── Pickup Availability Check ─────────────────────────────────────────

def check_pickup():
    """Check in-store pickup. Only alerts when status CHANGES to available."""
    all_skus = list(CONFIG["skus"].keys())
    pickup_state_file = STATE_DIR / "pickup_state.json"

    prev_state = {}
    if pickup_state_file.exists():
        try:
            prev_state = json.loads(pickup_state_file.read_text())
        except (json.JSONDecodeError, KeyError):
            pass

    current_state = {}

    parts_query = "&".join(
        f"parts.{i}={sku.replace('/', '%2F')}"
        for i, sku in enumerate(all_skus)
    )
    url = (
        f"https://www.apple.com/{CONFIG['country']}/shop/retail/pickup-message"
        f"?pl=true&{parts_query}&location={CONFIG['location']}"
    )

    raw = _curl_fetch(url)
    if not raw:
        log("  Pickup API returned empty")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log("  Pickup API non-JSON")
        return

    stores = data.get("body", {}).get("stores", [])
    for store in stores:
        store_name = store.get("storeName", "Unknown")
        store_id = store.get("storeNumber", "?")
        parts = store.get("partsAvailability", {})

        for sku, info in parts.items():
            display = info.get("pickupDisplay", "")
            pickup_quote = info.get("pickupSearchQuote", "")
            label = CONFIG["skus"].get(sku, sku)
            state_key = f"{sku}@{store_id}"
            current_state[state_key] = display

            was_available = prev_state.get(state_key) == "available"
            is_available = display == "available"

            if is_available and not was_available:
                notify(
                    "64GB Mac Available for Pickup!",
                    f"*Model:* {label}\n"
                    f"*Store:* Apple {store_name}\n"
                    f"*When:* {pickup_quote}\n"
                    f"*SKU:* {sku}"
                )
            elif is_available:
                log(f"  {store_name}: {label} — still available")
            else:
                log(f"  {store_name}: {label} — unavailable")

    pickup_state_file.write_text(json.dumps(current_state, indent=2))


# ── CTO Delivery Estimate Check ──────────────────────────────────

def check_cto_delivery():
    """Monitor delivery estimates for CTO configurations.

    CTO (configure-to-order) products can't be picked up in-store.
    But we can track delivery estimate changes — a shorter estimate
    often signals new production batches or restocked components.
    """
    cto_configs = CONFIG.get("cto_configs", {})
    if not cto_configs:
        return

    for config_id, cfg in cto_configs.items():
        product = cfg["product"]
        options = cfg["options"]
        label = cfg["label"]
        state_file = STATE_DIR / f"cto_{config_id}.json"

        url = (
            f"https://www.apple.com/{CONFIG['country']}/shop/delivery-message"
            f"?parts.0={product}&option.0={options}"
        )

        raw = _curl_fetch(url)
        if not raw:
            log(f"  {label}: API returned empty")
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log(f"  {label}: non-JSON response")
            continue

        # Parse delivery info from the response
        delivery_msg = data.get("body", {}).get("content", {}).get("deliveryMessage", {})

        # The key is a concatenation of product + sorted option codes
        current_estimate = None
        current_date = None
        msg_type = None
        for key, val in delivery_msg.items():
            if isinstance(val, dict) and "regular" in val:
                regular = val["regular"]
                msg_type = regular.get("messageType", "")
                opts = regular.get("deliveryOptionMessages", [])
                if opts:
                    current_estimate = opts[0].get("displayName", "")
                delivery_opts = regular.get("deliveryOptions", [])
                if delivery_opts:
                    current_date = delivery_opts[0].get("date", "")
                break

        if not current_estimate:
            log(f"  {label}: could not parse delivery estimate")
            continue

        # Compare with previous state
        now = datetime.now().isoformat()
        current_state = {
            "estimate": current_estimate,
            "date": current_date,
            "type": msg_type,
            "checked": now,
        }

        if state_file.exists():
            try:
                previous = json.loads(state_file.read_text())
            except (json.JSONDecodeError, KeyError):
                previous = {}

            prev_estimate = previous.get("estimate", "")
            if current_estimate != prev_estimate and _is_within_days(current_date, CONFIG["notify_within_days"]):
                notify(
                    "64GB Mac CTO Delivery Update!",
                    f"*Model:* {label}\n"
                    f"*Delivery:* {current_estimate}\n"
                    f"*Date:* {current_date}\n"
                    f"*Previous:* {prev_estimate}"
                )
            else:
                log(f"  {label}: {msg_type} {current_estimate} ({current_date})")
        else:
            log(f"  {label}: baseline — {msg_type} {current_estimate} ({current_date})")

        state_file.write_text(json.dumps(current_state, indent=2))


# ── Standard SKU Delivery Check ──────────────────────────────────

def check_sku_delivery():
    """Check delivery estimates for standard retail SKUs via delivery-message API."""
    skus = CONFIG.get("skus", {})
    if not skus:
        return

    all_skus = list(skus.keys())
    batch_size = 10

    for batch_start in range(0, len(all_skus), batch_size):
        batch = all_skus[batch_start:batch_start + batch_size]
        parts_query = "&".join(
            f"parts.{i}={sku.replace('/', '%2F')}"
            for i, sku in enumerate(batch)
        )
        url = (
            f"https://www.apple.com/{CONFIG['country']}/shop/delivery-message"
            f"?{parts_query}"
        )

        raw = _curl_fetch(url)
        if not raw:
            log(f"  Delivery API empty (batch {batch_start // batch_size + 1})")
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log(f"  Delivery API non-JSON (batch {batch_start // batch_size + 1})")
            continue

        delivery_msg = data.get("body", {}).get("content", {}).get("deliveryMessage", {})

        for sku in batch:
            label = skus[sku]
            info = delivery_msg.get(sku, {})
            regular = info.get("regular", {})
            if not regular:
                log(f"  {label}: no delivery info")
                continue

            msg_type = regular.get("messageType", "")
            opts = regular.get("deliveryOptionMessages", [])
            estimate = opts[0].get("displayName", "") if opts else "unknown"
            delivery_opts = regular.get("deliveryOptions", [])
            date = delivery_opts[0].get("date", "") if delivery_opts else "unknown"

            state_file = STATE_DIR / f"delivery_{sku.replace('/', '_')}.json"
            now = datetime.now(SGT).isoformat()
            current_state = {"estimate": estimate, "date": date, "type": msg_type, "checked": now}

            if state_file.exists():
                try:
                    previous = json.loads(state_file.read_text())
                except (json.JSONDecodeError, KeyError):
                    previous = {}
                prev_estimate = previous.get("estimate", "")
                if estimate != prev_estimate and _is_within_days(date, CONFIG["notify_within_days"]):
                    notify(
                        "64GB Mac Delivery Update!",
                        f"*Model:* {label}\n"
                        f"*Delivery:* {estimate}\n"
                        f"*Date:* {date}\n"
                        f"*Previous:* {prev_estimate}"
                    )
                else:
                    log(f"  {label}: {estimate} ({date})")
            else:
                log(f"  {label}: baseline — {estimate} ({date})")

            state_file.write_text(json.dumps(current_state, indent=2))


# ── Page Change Detection ─────────────────────────────────────────────

def check_page_changes():
    for page in CONFIG["watch_pages"]:
        url = page["url"]
        label = page["label"]
        state_file = STATE_DIR / f"page_{hashlib.md5(url.encode()).hexdigest()}.txt"

        content = _curl_fetch(url)
        if not content:
            continue

        # Strip dynamic content, keep meaningful text for change detection
        cleaned = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
        cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', cleaned)
        text = re.sub(r'\s+', ' ', text).strip()
        current_hash = hashlib.sha256(text[:50000].encode()).hexdigest()

        if state_file.exists():
            previous_hash = state_file.read_text().strip()
            if current_hash != previous_hash:
                new_skus = re.findall(r'[A-Z]{2,5}[0-9]{2,5}[A-Z]{0,3}/A', content)
                sku_info = f" (SKUs found: {', '.join(set(new_skus))})" if new_skus else ""
                notify(
                    "Apple Page Changed!",
                    f"{label} has been updated!{sku_info}\n{url}"
                )
                state_file.write_text(current_hash)
            else:
                log(f"  {label}: no changes")
        else:
            state_file.write_text(current_hash)
            log(f"  {label}: baseline saved")


# ── Health Report ─────────────────────────────────────────────────────

def build_health_report() -> str:
    """Build a health status message from current state files."""
    now = datetime.now(SGT)
    lines = [f"*Apple Stock Monitor*", f"_{now.strftime('%Y-%m-%d %H:%M SGT')}_", ""]

    # Run counter
    counter_file = STATE_DIR / "run_counter.json"
    counter = {}
    if counter_file.exists():
        try:
            counter = json.loads(counter_file.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    today_key = now.strftime("%Y-%m-%d")
    runs_today = counter.get(today_key, 0)
    lines.append(f"Checks today: *{runs_today}*")
    lines.append("")

    # High-RAM models (priority)
    lines.append("*High-RAM Models (★):*")
    for sku, label in CONFIG["skus"].items():
        if "★" not in label:
            continue
        sf = STATE_DIR / f"delivery_{sku.replace('/', '_')}.json"
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
                lines.append(f"  {label}: {data.get('estimate', '?')}")
            except (json.JSONDecodeError, KeyError):
                lines.append(f"  {label}: no data")
        else:
            lines.append(f"  {label}: no data")

    lines.append("")

    # CTO delivery estimates
    lines.append("*CTO Delivery (64GB):*")
    for config_id, cfg in CONFIG.get("cto_configs", {}).items():
        sf = STATE_DIR / f"cto_{config_id}.json"
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
                lines.append(f"  {cfg['label']}: {data.get('estimate', '?')}")
            except (json.JSONDecodeError, KeyError):
                lines.append(f"  {cfg['label']}: no data")
        else:
            lines.append(f"  {cfg['label']}: no data")

    lines.append("")

    lines.append("")
    total_skus = len(CONFIG["skus"])
    lines.append(f"*Total SKUs tracked:* {total_skus}")

    # Page change status
    page_count = sum(1 for p in CONFIG["watch_pages"]
                     if (STATE_DIR / f"page_{hashlib.md5(p['url'].encode()).hexdigest()}.txt").exists())
    lines.append(f"*Pages monitored:* {page_count}/{len(CONFIG['watch_pages'])}")

    return "\n".join(lines)


def increment_run_counter():
    """Track how many checks happen per day."""
    counter_file = STATE_DIR / "run_counter.json"
    counter = {}
    if counter_file.exists():
        try:
            counter = json.loads(counter_file.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    today_key = datetime.now(SGT).strftime("%Y-%m-%d")
    counter[today_key] = counter.get(today_key, 0) + 1
    # Keep only last 7 days
    cutoff = (datetime.now(SGT) - timedelta(days=7)).strftime("%Y-%m-%d")
    counter = {k: v for k, v in counter.items() if k >= cutoff}
    counter_file.write_text(json.dumps(counter, indent=2))


# ── Telegram Command Handler ─────────────────────────────────────────

def check_telegram_commands():
    """Poll for Telegram /health commands and respond."""
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        return

    offset_file = STATE_DIR / "tg_update_offset.txt"
    offset = 0
    if offset_file.exists():
        try:
            offset = int(offset_file.read_text().strip())
        except ValueError:
            pass

    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=0"
    raw = _curl_fetch(url, timeout=5)
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    results = data.get("result", [])
    for update in results:
        update_id = update.get("update_id", 0)
        msg = update.get("message", {})
        text = msg.get("text", "")
        msg_chat_id = str(msg.get("chat", {}).get("id", ""))

        # Only respond to our authorized chat
        if msg_chat_id != chat_id:
            offset = update_id + 1
            continue

        if text.startswith("/health") or text.startswith("/status"):
            log("  Telegram: /health command received")
            report = build_health_report()
            telegram_send(report)
        elif text.startswith("/help"):
            telegram_send(
                "*Commands:*\n"
                "/health — Current status and delivery estimates\n"
                "/help — Show this message"
            )

        offset = update_id + 1

    if results:
        offset_file.write_text(str(offset))


# ── Daily Summary ────────────────────────────────────────────────────

def check_daily_summary():
    """Send a daily summary at 9 AM SGT."""
    now = datetime.now(SGT)
    summary_file = STATE_DIR / "last_daily_summary.txt"

    today_key = now.strftime("%Y-%m-%d")
    if summary_file.exists() and summary_file.read_text().strip() == today_key:
        return  # Already sent today

    # Send at 9 AM SGT (hour 9). Since we run every 5 min, check window 9:00-9:09
    if now.hour != 9 or now.minute >= 10:
        return

    log("  Sending daily summary")
    report = build_health_report()
    telegram_send(f"*Daily Summary*\n\n{report}")
    summary_file.write_text(today_key)


# ── Main ──────────────────────────────────────────────────────────────

def run_check():
    increment_run_counter()
    check_telegram_commands()
    log("── Checking pickup availability ──")
    check_pickup()
    log("── Checking delivery estimates (retail SKUs) ──")
    check_sku_delivery()
    log("── Checking delivery estimates (CTO configs) ──")
    check_cto_delivery()
    log("── Checking page changes ──")
    check_page_changes()
    check_daily_summary()
    log("── Done ──")


def main():
    parser = argparse.ArgumentParser(description="Apple Store Stock Monitor (Singapore)")
    parser.add_argument("--loop", nargs="?", const=CONFIG["poll_interval"], type=int,
                        help="Poll continuously (optional: interval in seconds, default 120)")
    parser.add_argument("--health", action="store_true",
                        help="Send health report to Telegram and exit")
    args = parser.parse_args()

    tg_status = "configured" if CONFIG["telegram_bot_token"] else "not set (use APPLE_MONITOR_TG_TOKEN)"
    log("Apple Stock Monitor — Singapore")
    log(f"  Monitoring {len(CONFIG['skus'])} SKUs + {len(CONFIG['watch_pages'])} pages")
    log(f"  Telegram: {tg_status}")

    if args.health:
        report = build_health_report()
        telegram_send(report)
        print(report.replace("*", "").replace("_", ""))
        return

    if args.loop:
        log(f"  Polling every {args.loop}s (Ctrl+C to stop)")
        while True:
            try:
                run_check()
                time.sleep(args.loop)
            except KeyboardInterrupt:
                log("Stopped.")
                break
    else:
        run_check()


if __name__ == "__main__":
    main()
