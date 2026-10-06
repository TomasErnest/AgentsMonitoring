"""Plan usage limits per vendor — how much of the rolling 5-hour / weekly window is spent and
when it resets. Shown in the agents table's Usage column.

Sources (all optional; a missing one just leaves the column empty):

* **anthropic** — Claude Code hands its status line a JSON with ``rate_limits``. The status line
  script saves that JSON to ``usage/claude-status.json`` in the agentsmon state dir (see README,
  "Usage column"); we read the last saved copy. It is as fresh as the last status line refresh.
* **openai** — Codex (and anything else signed in with the same ChatGPT plan, e.g. Hermes via
  ``openai-codex``) shares one limit. We ask ChatGPT's usage endpoint live with Codex's own
  token; if that fails, we fall back to the last ``rate_limits`` snapshot Codex wrote into its
  session rollout log.

Every value comes back as ``{"windows": [{"label", "used_pct", "resets_at"}], "as_of", "source"}``.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

CACHE_SECONDS = 60
_cache: dict[str, tuple[float, dict | None]] = {}


def _state_dir() -> Path:
    return Path(os.environ.get("AGENTSMON_STATE") or Path.home() / ".local/state/agentsmon")


def _window(label: str, pct, resets_at) -> dict | None:
    if pct is None:
        return None
    return {"label": label, "used_pct": round(float(pct)),
            "resets_at": int(resets_at) if resets_at else None}


def _label(minutes: int) -> str:
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    return f"{minutes // 60}h" if minutes and minutes % 60 == 0 else f"{minutes}m"


def claude() -> dict | None:
    p = _state_dir() / "usage" / "claude-status.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        as_of = int(p.stat().st_mtime)
    except (OSError, ValueError):
        return None
    rl = d.get("rate_limits") or {}
    wins = [w for w in (_window("5h", (rl.get("five_hour") or {}).get("used_percentage"),
                                (rl.get("five_hour") or {}).get("resets_at")),
                        _window("7d", (rl.get("seven_day") or {}).get("used_percentage"),
                                (rl.get("seven_day") or {}).get("resets_at"))) if w]
    return {"windows": wins, "as_of": as_of, "source": "Claude Code status line"} if wins else None


def _codex_live() -> dict | None:
    try:
        tok = json.loads((Path.home() / ".codex/auth.json").read_text(encoding="utf-8"))["tokens"]
        req = urllib.request.Request(
            "https://chatgpt.com/backend-api/wham/usage",
            headers={"Authorization": "Bearer " + tok["access_token"],
                     "ChatGPT-Account-Id": tok.get("account_id", ""),
                     "User-Agent": "codex_cli_rs"})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.load(r)
    except Exception:
        return None
    rl = d.get("rate_limit") or {}
    wins = []
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key) or {}
        win = _window(_label(int(w.get("limit_window_seconds") or 0) // 60), w.get("used_percent"),
                      w.get("reset_at"))
        if win:
            wins.append(win)
    if not wins:
        return None
    return {"windows": wins, "as_of": int(time.time()), "source": "ChatGPT usage (live)",
            "limit_reached": bool(rl.get("limit_reached"))}


def _codex_rollout() -> dict | None:
    """Last ``rate_limits`` snapshot from the newest Codex session log (read from the tail)."""
    root = Path.home() / ".codex/sessions"
    try:
        newest = max(root.rglob("rollout-*.jsonl"), key=lambda f: f.stat().st_mtime)
    except (OSError, ValueError):
        return None
    try:
        with open(newest, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - 2_000_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"rate_limits"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        rl = (ev.get("payload") or {}).get("rate_limits") or {}
        wins = []
        for key in ("primary", "secondary"):
            w = rl.get(key) or {}
            win = _window(_label(int(w.get("window_minutes") or 0)), w.get("used_percent"),
                          w.get("resets_at"))
            if win:
                wins.append(win)
        if wins:
            from datetime import datetime
            try:
                as_of = int(datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00")).timestamp())
            except (KeyError, ValueError):
                as_of = int(newest.stat().st_mtime)
            return {"windows": wins, "as_of": as_of, "source": "Codex session log"}
    return None


def codex() -> dict | None:
    return _codex_live() or _codex_rollout()


_BY_VENDOR = {"anthropic": claude, "openai": codex}


def for_vendor(vendor: str | None) -> dict | None:
    fn = _BY_VENDOR.get(vendor or "")
    if not fn:
        return None
    hit = _cache.get(vendor)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _cache[vendor] = (time.time(), val)
    return val
