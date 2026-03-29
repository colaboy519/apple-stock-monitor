#!/bin/bash
# Wrapper that loads Telegram credentials from macOS Keychain
export APPLE_MONITOR_TG_TOKEN
APPLE_MONITOR_TG_TOKEN="$(security find-generic-password -a apple-stock-monitor -s APPLE_MONITOR_TG_TOKEN -w 2>/dev/null)"
export APPLE_MONITOR_TG_CHAT
APPLE_MONITOR_TG_CHAT="$(security find-generic-password -a apple-stock-monitor -s APPLE_MONITOR_TG_CHAT -w 2>/dev/null)"
exec /usr/bin/python3 /Users/zhonglin/dev/tools/apple-stock-monitor/monitor.py "$@"
