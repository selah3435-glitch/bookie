#!/usr/bin/env python3
"""Bookie v1.3 - DraftKings odds scanner with team-strength models."""

import json
import math
import os
import random
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

# CONFIG
API_KEY = os.getenv("ODDS_API_KEY")
BOOKMAKER = "draftkings"
REGIONS = "us"
CACHE_FILE = "bookie_cache.json"
# Major US sports keys for The Odds API (no single "all" endpoint)
SPORT_KEYS = [
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "basketball_nba",
    "basketball_ncaab",
    "baseball_mlb",
    "icehockey_nhl",
    "mma_mixed_martial_arts",
    "soccer_usa_mls",
]
EDGE_THRESHOLD = 3.0  # minimum edge % to surface a bet

# ESPN standings paths (free public API — sportsipy is broken vs modern sports-reference)
ESPN_SPORT_MAP = {
    "nfl": "football/nfl",
    "americanfootball_nfl": "football/nfl",
    "nba": "basketball/nba",
    "basketball_nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "baseball_mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "icehockey_nhl": "hockey/nhl",
    "mls": "soccer/usa.1",
    "soccer_usa_mls": "soccer/usa.1",
}

# In-memory: sport_path -> {team_name_lower: win_pct}
_STRENGTH_CACHE: Dict[str, Dict[str, float]] = {}

# Friendly labels for the UI
SPORT_LABELS = {
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "basketball_nba": "NBA",
    "basketball_ncaab": "NCAAB",
    "baseball_mlb": "MLB",
    "icehockey_nhl": "NHL",
    "mma_mixed_martial_arts": "MMA",
    "soccer_usa_mls": "MLS",
}


def load_cache() -> Dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache: Dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass


def fetch_dk_odds(
    api_key: Optional[str] = None,
    sport_keys: Optional[List[str]] = None,
    quiet: bool = False,
) -> tuple[List[Dict], Optional[str]]:
    """Fetch DraftKings odds for configured sports from The Odds API.

    Returns (events, last_remaining_requests_header).
    """
    key = api_key or API_KEY or os.getenv("ODDS_API_KEY")
    if not key:
        if not quiet:
            print("Set ODDS_API_KEY!")
        return [], None

    keys = sport_keys or SPORT_KEYS
    all_events: List[Dict] = []
    remaining: Optional[str] = None
    params_base = {
        "apiKey": key,
        "regions": REGIONS,
        "oddsFormat": "american",
        "bookmakers": BOOKMAKER,
        "markets": "h2h,spreads,totals",
    }

    for sport_key in keys:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        try:
            resp = requests.get(url, params=params_base, timeout=20)
            if resp.status_code == 404:
                continue  # sport off-season or invalid
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                all_events.extend(data)
                if not quiet:
                    print(f"  {sport_key}: {len(data)} events")
            remaining = resp.headers.get("x-requests-remaining", remaining)
            if remaining is not None and not quiet:
                print(f"  API requests remaining: {remaining}")
        except requests.RequestException as e:
            if not quiet:
                print(f"  Odds fetch failed for {sport_key}: {e}")
            continue

    if not quiet:
        print(f"OK {len(all_events)} DraftKings events loaded.")
    return all_events, remaining


def implied_prob(odds: int) -> float:
    """American odds -> implied probability (no vig removal)."""
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def _resolve_espn_path(sport: str) -> Optional[str]:
    sport_l = (sport or "").lower().replace(" ", "_")
    for key, path in ESPN_SPORT_MAP.items():
        if key in sport_l or sport_l in key:
            # avoid ncaaf matching nfl via "football"
            if "ncaaf" in sport_l or "ncaab" in sport_l:
                return None
            return path
    # title-style keys from Odds API (e.g. "NFL", "MLB")
    for key, path in ESPN_SPORT_MAP.items():
        if key == sport_l or key.upper() == sport.upper():
            return path
    short = {
        "nfl": "football/nfl",
        "nba": "basketball/nba",
        "mlb": "baseball/mlb",
        "nhl": "hockey/nhl",
        "mls": "soccer/usa.1",
    }
    return short.get(sport_l)


def _parse_espn_standings(data: dict) -> Dict[str, float]:
    """Flatten ESPN standings tree into team_name_lower -> win_pct."""
    out: Dict[str, float] = {}

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        for entry in (node.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            names = [
                team.get("displayName"),
                team.get("name"),
                team.get("shortDisplayName"),
                team.get("abbreviation"),
                team.get("nickname"),
            ]
            stats = {s.get("name"): s for s in (entry.get("stats") or [])}
            pct = None
            if "winPercent" in stats:
                try:
                    pct = float(stats["winPercent"].get("value")
                                or stats["winPercent"].get("displayValue")
                                or 0)
                except (TypeError, ValueError):
                    pct = None
            if pct is None or pct == 0:
                try:
                    w = float((stats.get("wins") or {}).get("value") or 0)
                    l = float((stats.get("losses") or {}).get("value") or 0)
                    if w + l > 0:
                        pct = w / (w + l)
                except (TypeError, ValueError):
                    pct = None
            if pct is None:
                continue
            for n in names:
                if n:
                    out[str(n).lower()] = pct
        for child in node.get("children") or []:
            walk(child)

    walk(data)
    return out


def _load_espn_strengths(espn_path: str) -> Dict[str, float]:
    """Load standings once per sport; fall back to prior seasons if current is empty/0-0."""
    if espn_path in _STRENGTH_CACHE:
        return _STRENGTH_CACHE[espn_path]

    year = datetime.now().year
    seasons_to_try = [None, year, year - 1, year - 2]  # None = ESPN default
    best: Dict[str, float] = {}

    for season in seasons_to_try:
        url = f"https://site.api.espn.com/apis/v2/sports/{espn_path}/standings"
        params = {} if season is None else {"season": season}
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            parsed = _parse_espn_standings(resp.json())
            # Prefer tables where teams actually have games
            if parsed and any(v > 0 for v in parsed.values()):
                best = parsed
                label = season if season is not None else "current"
                print(f"  ESPN {espn_path} standings loaded ({label}): {len(parsed)} names")
                break
            if parsed and not best:
                best = parsed
        except requests.RequestException as e:
            print(f"  ESPN standings failed for {espn_path} season={season}: {e}")
            continue

    _STRENGTH_CACHE[espn_path] = best
    if not best:
        print(f"  No ESPN strength data for {espn_path}")
    return best


def get_team_strength(team_name: str, sport: str) -> Optional[float]:
    """Estimate win rate from ESPN standings. Returns None if unknown."""
    espn_path = _resolve_espn_path(sport)
    if not espn_path:
        return None

    table = _load_espn_strengths(espn_path)
    if not table:
        return None

    t = (team_name or "").lower().strip()
    if not t:
        return None

    # Exact / substring match
    if t in table:
        return table[t]
    for name, pct in table.items():
        if t in name or name in t:
            return pct

    # Token overlap (e.g. "Los Angeles Lakers" vs "Lakers")
    tokens = [w for w in t.replace("-", " ").split() if len(w) > 2]
    best_name, best_score = None, 0
    for name, pct in table.items():
        score = sum(1 for w in tokens if w in name)
        if score > best_score:
            best_score, best_name = score, name
    if best_name and best_score >= 1:
        return table[best_name]
    return None


def poisson_win_prob(lambda_home: float, lambda_away: float, sims: int = 800) -> float:
    """Monte Carlo rough home win probability for low-scoring sports."""
    home_wins = 0
    for _ in range(sims):
        home_g = sum(1 for _ in range(25) if random.random() < (lambda_home / 12))
        away_g = sum(1 for _ in range(25) if random.random() < (lambda_away / 12))
        if home_g > away_g or (home_g == away_g and random.random() > 0.4):
            home_wins += 1
    return home_wins / float(sims)


def est_true_prob(event: Dict, outcome: str, market: str) -> Optional[float]:
    """Estimate true win probability; cache by event/outcome/market.

    Returns None when team strength is unknown so we don't invent 50/50 edges
    on huge underdogs (the previous blind default).
    """
    outcome_l = (outcome or "").lower().strip()
    # Draws / pushes have no team-strength model yet (check before cache)
    if outcome_l in ("draw", "tie", "push") or "draw" in outcome_l:
        return None

    cache = load_cache()
    key = f"{event.get('id') or event.get('key')}_{outcome}_{market}"
    if key in cache:
        return cache[key]

    sport = event.get("sport_key") or event.get("sport_title") or ""
    sport_l = sport.lower()
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    home_str = get_team_strength(home, sport)
    away_str = get_team_strength(away, sport)

    # No real model data → do not claim an edge
    if home_str is None or away_str is None:
        return None

    is_soccer = any(s in sport_l for s in ("soccer", "mls", "usa.1")) and "nfl" not in sport_l
    is_home_side = home.lower() in outcome_l or outcome_l == "home"
    is_away_side = away.lower() in outcome_l or outcome_l == "away"

    if is_soccer:
        lambda_h = home_str * 1.4 + 0.9
        lambda_a = away_str + 0.7
        p_home = poisson_win_prob(lambda_h, lambda_a)
        if is_home_side:
            true_p = p_home
        elif is_away_side:
            true_p = 1 - p_home
        else:
            return None
    else:
        # Elo-style logistic on strength differential
        diff = (home_str - away_str) * 250
        p_home = 1 / (1 + math.pow(10, -diff / 400))
        if is_away_side:
            true_p = 1 - p_home
        elif is_home_side:
            true_p = p_home
        else:
            # totals / props without clear side — skip (no model)
            return None

    true_p = max(0.05, min(0.95, true_p))
    cache[key] = round(true_p, 4)
    save_cache(cache)
    return true_p


def scan_value_bets(
    odds_data: List[Dict],
    edge_threshold: Optional[float] = None,
) -> pd.DataFrame:
    threshold = EDGE_THRESHOLD if edge_threshold is None else edge_threshold
    bets = []
    for ev in odds_data:
        sport = ev.get("sport_title", "N/A")
        home = ev.get("home_team", "N/A")
        away = ev.get("away_team", "N/A")
        time_utc = ev.get("commence_time", "TBD") or "TBD"

        for bm in ev.get("bookmakers", []):
            if bm.get("key") != BOOKMAKER:
                continue
            for mkt in bm.get("markets", []):
                mkey = (mkt.get("key") or "").upper()
                for out in mkt.get("outcomes", []):
                    name = out.get("name", "")
                    odds_val = out.get("price")
                    point = out.get("point")
                    if odds_val is None:
                        continue
                    try:
                        odds_int = int(odds_val)
                    except (TypeError, ValueError):
                        continue

                    imp = implied_prob(odds_int)
                    true_p = est_true_prob(ev, name, mkt.get("key", ""))
                    if true_p is None:
                        continue
                    edge = (true_p - imp) * 100

                    if edge > threshold:
                        point_str = f" {point}" if point is not None else ""
                        bet_str = f"{name}{point_str} ({odds_int})".strip()
                        bets.append(
                            {
                                "Sport": sport,
                                "Matchup": f"{home} vs {away}",
                                "Time_UTC": time_utc[:16],
                                "Market": mkey,
                                "Bet_On": bet_str,
                                "DK_Odds": odds_int,
                                "Implied_%": round(imp * 100, 1),
                                "Est_True_%": round(true_p * 100, 1),
                                "Edge_%": round(edge, 2),
                            }
                        )

    df = pd.DataFrame(bets)
    if df.empty:
        return df
    return df.sort_values(by=["Edge_%", "Est_True_%"], ascending=False)


def explain_bets(df: pd.DataFrame) -> List[Dict]:
    return [
        {
            **row.to_dict(),
            "Why_Its_Good": (
                f"Model edge: est. true win chance {row['Est_True_%']}% vs DK implied "
                f"{row['Implied_%']}%. ESPN standings + model. Lines move — verify before betting."
            ),
        }
        for _, row in df.head(10).iterrows()
    ]


def main() -> None:
    print(" Bookie v1.3 - DraftKings + ESPN Strength Models\n")
    print(f"[{datetime.now()}] Bookie v1.3 with ESPN strength models running...")
    data, _remaining = fetch_dk_odds()
    if not data:
        return

    df = scan_value_bets(data)
    if df.empty:
        print("No strong value bets right now.")
        return

    top = explain_bets(df)
    print("\n BOOKIE TOP 10 DRAFTKINGS PLAYS \n")
    for i, b in enumerate(top, 1):
        print(f"{i}. {b['Sport']} | {b['Matchup']} @ {b['Time_UTC']}")
        print(f"   {b['Market']}: {b['Bet_On']}")
        print(
            f"   Edge +{b['Edge_%']}% | Imp {b['Implied_%']}% | True {b['Est_True_%']}%"
        )
        print(f"   Why: {b['Why_Its_Good']}\n")

    out_path = f"bookie_report_{datetime.now().date()}.csv"
    df.head(10).to_csv(out_path, index=False)
    print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()
