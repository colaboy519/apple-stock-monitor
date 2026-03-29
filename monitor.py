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
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────

CONFIG = {
    "country": "sg",
    "location": "238857",  # Orchard Road postal code
    "poll_interval": 120,  # seconds

    # Telegram (set via env vars or edit here)
    "telegram_bot_token": os.environ.get("APPLE_MONITOR_TG_TOKEN", ""),
    "telegram_chat_id": os.environ.get("APPLE_MONITOR_TG_CHAT", ""),

    # SKUs to monitor for in-store pickup (standard retail configs only)
    "skus": {
        # Mac Mini M4 Pro — base config (only retail SKU in pickup system)
        "MCX44ZP/A": "Mac Mini M4 Pro 24GB/512GB",
        # Mac Studio M4 Max — standard configs
        "MU963ZP/A": "Mac Studio M4 Max 14c/32c 36GB/512GB (base)",
        "MHQH4ZP/A": "Mac Studio M4 Max 16c/40c 64GB/1TB",
        # Mac Studio M3 Ultra
        "MU973ZP/A": "Mac Studio M3 Ultra 96GB",
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

    # Pages to monitor for changes (new product launches)
    "watch_pages": [
        {
            "url": "https://www.apple.com/sg/shop/buy-mac/mac-mini",
            "label": "Mac Mini Buy Page",
        },
        {
            "url": "https://www.apple.com/sg/shop/buy-mac/mac-studio",
            "label": "Mac Studio Buy Page",
        },
        {
            "url": "https://www.apple.com/sg/mac-mini/",
            "label": "Mac Mini Product Page",
        },
        {
            "url": "https://www.apple.com/sg/mac-studio/",
            "label": "Mac Studio Product Page",
        },
    ],
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


def notify(title: str, message: str):
    log(f"  🔔 {title}: {message}")
    macos_notify(title, message)
    telegram_send(f"*{title}*\n{message}")


# ── Pickup Availability Check ─────────────────────────────────────────

def check_pickup():
    parts_query = "&".join(
        f"parts.{i}={sku.replace('/', '%2F')}"
        for i, sku in enumerate(CONFIG["skus"].keys())
    )
    url = (
        f"https://www.apple.com/{CONFIG['country']}/shop/retail/pickup-message"
        f"?pl=true&{parts_query}&location={CONFIG['location']}"
    )

    raw = _curl_fetch(url)
    if not raw:
        log("  Pickup API returned empty response")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log("  Pickup API returned non-JSON response")
        return

    stores = data.get("body", {}).get("stores", [])
    any_available = False

    for store in stores:
        store_name = store.get("storeName", "Unknown")
        parts = store.get("partsAvailability", {})

        for sku, info in parts.items():
            display = info.get("pickupDisplay", "")
            quote_text = info.get("pickupSearchQuote", "Unknown")
            label = CONFIG["skus"].get(sku, sku)

            if display == "available":
                any_available = True
                notify(
                    "Apple Stock Available!",
                    f"{label} available at {store_name}!\nSKU: {sku}"
                )
            else:
                log(f"  {store_name}: {label} — {quote_text}")

    if not any_available:
        log("  No pickup availability at any Singapore store")


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
            if current_estimate != prev_estimate:
                notify(
                    "CTO Delivery Changed!",
                    f"{label}\n"
                    f"Was: {prev_estimate}\n"
                    f"Now: {current_estimate}\n"
                    f"Date: {current_date}"
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

    parts_query = "&".join(
        f"parts.{i}={sku.replace('/', '%2F')}"
        for i, sku in enumerate(skus.keys())
    )
    url = (
        f"https://www.apple.com/{CONFIG['country']}/shop/delivery-message"
        f"?{parts_query}"
    )

    raw = _curl_fetch(url)
    if not raw:
        log("  Delivery API returned empty response")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log("  Delivery API returned non-JSON response")
        return

    delivery_msg = data.get("body", {}).get("content", {}).get("deliveryMessage", {})

    for sku, label in skus.items():
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
        now = datetime.now().isoformat()
        current_state = {"estimate": estimate, "date": date, "type": msg_type, "checked": now}

        if state_file.exists():
            try:
                previous = json.loads(state_file.read_text())
            except (json.JSONDecodeError, KeyError):
                previous = {}
            prev_estimate = previous.get("estimate", "")
            if estimate != prev_estimate:
                notify(
                    "Delivery Estimate Changed!",
                    f"{label}\nWas: {prev_estimate}\nNow: {estimate}\nDate: {date}"
                )
            else:
                log(f"  {label}: {msg_type} {estimate} ({date})")
        else:
            log(f"  {label}: baseline — {msg_type} {estimate} ({date})")

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


# ── Main ──────────────────────────────────────────────────────────────

def run_check():
    log("── Checking pickup availability ──")
    check_pickup()
    log("── Checking delivery estimates (retail SKUs) ──")
    check_sku_delivery()
    log("── Checking delivery estimates (CTO configs) ──")
    check_cto_delivery()
    log("── Checking page changes ──")
    check_page_changes()
    log("── Done ──")


def main():
    parser = argparse.ArgumentParser(description="Apple Store Stock Monitor (Singapore)")
    parser.add_argument("--loop", nargs="?", const=CONFIG["poll_interval"], type=int,
                        help="Poll continuously (optional: interval in seconds, default 120)")
    args = parser.parse_args()

    tg_status = "configured" if CONFIG["telegram_bot_token"] else "not set (use APPLE_MONITOR_TG_TOKEN)"
    log("Apple Stock Monitor — Singapore")
    log(f"  Monitoring {len(CONFIG['skus'])} SKUs + {len(CONFIG['watch_pages'])} pages")
    log(f"  Telegram: {tg_status}")

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
