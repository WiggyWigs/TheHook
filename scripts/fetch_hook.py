#!/usr/bin/env python3
"""
Pull NFL + NCAAF spreads and betting splits (bet % and money %) from Action
Network and build the data behind index.html.

Run by .github/workflows/pull.yml. No API key needed - this reads the same
public JSON Action Network's own public-betting page loads. It is
unofficial, so if they change the shape the script fails loudly rather than
writing a half-empty file.

Files written (all under data/):
  live.json                  this week's games (what the page reads)
  weeks/<week_id>/<date>.json  raw snapshot per run day
  kickoff_lines.json         each game's last pre-kickoff numbers, written once
  tuesday_lines.json         DraftKings at the first run on each game's Tuesday, written once
  record.json                every game with splits once it kicks off, graded when final

week_id is the Monday that closes a game's week, from kickoff in Eastern time.
The page shows one week at a time and turns over on Tuesday.
"""
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

LEAGUES = {"nfl": "NFL", "ncaaf": "NCAAF"}
API = "https://api.actionnetwork.com/web/v2/scoreboard/{sport}"
PUBLIC_API = "https://api.actionnetwork.com/web/v2/scoreboard/publicbetting/{sport}"

SPLITS_BOOK = "15"  # Action Network "Consensus" - carries their bet/money %
LINE_BOOK = "68"    # DraftKings
EDGE_THRESHOLD = 15  # page's default Sharp Money threshold (money % minus bet %)

EASTERN = ZoneInfo("America/New_York")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json",
}

FROZEN_FIELDS = ("line", "home_bets", "home_money", "away_bets", "away_money")
RECORD_FIELDS = ("league", "season", "an_week", "week_id", "start_time", "home", "away",
                 "home_abbr", "away_abbr", "tue_line", *FROZEN_FIELDS, "frozen_at", "frozen_for")


def get_json(url):
    try:
        with urlopen(Request(url, headers=HEADERS), timeout=30) as res:
            return json.load(res)
    except (HTTPError, URLError) as e:
        sys.exit(f"Request failed for {url}: {e}")


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def parse_time(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def week_id_for(start_time):
    """Monday on or after the Eastern date of this time."""
    day = parse_time(start_time).astimezone(EASTERN).date()
    return (day + timedelta(days=(7 - day.weekday()) % 7)).isoformat()


def tuesday_of(week_id):
    return (datetime.fromisoformat(week_id) - timedelta(days=6)).date().isoformat()


def key_of(g):
    return f"{g['league']}:{g['id']}"


def spread_outcomes(game, book):
    return game.get("markets", {}).get(book, {}).get("event", {}).get("spread", [])


def pct(outcome, kind):
    return ((outcome.get("bet_info") or {}).get(kind) or {}).get("percent")


def parse_game(game, league):
    teams = {t["id"]: t for t in game.get("teams", [])}
    home = teams.get(game["home_team_id"], {})
    away = teams.get(game["away_team_id"], {})

    line = None
    for book in (LINE_BOOK, SPLITS_BOOK):
        line = next((o["value"] for o in spread_outcomes(game, book)
                     if o.get("side") == "home" and o.get("value") is not None), None)
        if line is not None:
            break

    splits = {}
    for book in (SPLITS_BOOK, LINE_BOOK):
        for o in spread_outcomes(game, book):
            b, m = pct(o, "tickets"), pct(o, "money")
            if o.get("side") in ("home", "away") and (b or m):
                splits[o["side"]] = {"bets": b, "money": m}
        if len(splits) == 2:
            break
    has_splits = len(splits) == 2

    box = game.get("boxscore") or {}
    return {
        "id": game["id"],
        "league": league,
        "season": game.get("season"),
        "an_week": game.get("week"),
        "week_id": week_id_for(game["start_time"]),
        "start_time": game["start_time"],
        "status": game.get("status"),
        "home": home.get("full_name"),
        "away": away.get("full_name"),
        "home_abbr": home.get("abbr"),
        "away_abbr": away.get("abbr"),
        "line": line,  # home team's spread, DraftKings
        "home_bets": splits["home"]["bets"] if has_splits else None,
        "home_money": splits["home"]["money"] if has_splits else None,
        "away_bets": splits["away"]["bets"] if has_splits else None,
        "away_money": splits["away"]["money"] if has_splits else None,
        "home_score": box.get("total_home_points"),
        "away_score": box.get("total_away_points"),
    }


# --- Tuesday line --------------------------------------------------------------

def record_tuesday_lines(games, now, store):
    """On a game's Tuesday (Eastern), keep DraftKings' line from the first run
    that sees it. Later Tuesday runs don't move it."""
    today = now.astimezone(EASTERN).date().isoformat()
    added = 0
    for g in games:
        if tuesday_of(g["week_id"]) == today and key_of(g) not in store and g["line"] is not None:
            store[key_of(g)] = {"week_id": g["week_id"], "line": g["line"],
                                "taken_at": now.isoformat(timespec="seconds"), "away": g["away"], "home": g["home"]}
            added += 1
    if added:
        print(f"Tuesday line taken for {added} games")


def tuesday_line(g, store, snapshots):
    """The stored Tuesday line, else this week's Tuesday snapshot."""
    if key_of(g) in store:
        return store[key_of(g)]["line"]
    snap = snapshots.setdefault(g["week_id"], load_json(DATA / "weeks" / g["week_id"] / f"{tuesday_of(g['week_id'])}.json", {}))
    return next((x.get("line") for x in snap.get("games", []) if key_of(x) == key_of(g)), None)


# --- One week at a time ----------------------------------------------------------

def carry_over_week(games, week_id, prev):
    """Action Network drops a week's college games from its feed once they're
    played, while the page still covers them until Tuesday. Put back any game
    of the week that's missing from the feed, from the latest pull that had it."""
    have = {key_of(g) for g in games}
    latest = {}
    for snap in [load_json(f, {}) for f in sorted((DATA / "weeks" / week_id).glob("*.json"))] + [prev]:
        for g in snap.get("games", []):
            if g.get("week_id") == week_id:
                latest[key_of(g)] = g  # later sources win
    return [copy.deepcopy(g) for k, g in latest.items() if k not in have]


# --- Freeze at kickoff -------------------------------------------------------------

def freeze_started_games(games, now, store, prev):
    """Once a game kicks off, Action Network's feed switches to live in-game
    lines and splits. Keep each started game's last pre-kickoff numbers
    instead: the stored ones, else the previous pull if it was before kickoff
    (or already frozen), else the latest of this week's snapshots taken before
    kickoff. Scores and status stay live - grading needs them."""
    prev_at = parse_time(prev["pulled_at"]) if prev.get("pulled_at") else None
    prev_games = {key_of(g): g for g in prev.get("games", [])}
    snapshots = {}
    for g in games:
        kickoff = parse_time(g["start_time"])
        if kickoff > now and g["status"] == "scheduled":
            continue
        key = key_of(g)
        src, src_at = None, None
        p = prev_games.get(key)
        if key in store:
            src, src_at = store[key], store[key]["frozen_at"]
        elif p and p.get("frozen_at"):
            src, src_at = p, p["frozen_at"]
        elif p and prev_at and prev_at < kickoff:
            src, src_at = p, prev["pulled_at"]
        else:
            wk = g["week_id"]
            if wk not in snapshots:
                snapshots[wk] = [load_json(f, {}) for f in sorted((DATA / "weeks" / wk).glob("*.json"))]
            for snap in snapshots[wk]:
                hit = next((x for x in snap.get("games", []) if key_of(x) == key), None)
                # an entry already frozen in that snapshot holds numbers from its frozen_at pull
                at = (hit or {}).get("frozen_at") or snap.get("pulled_at")
                if hit and at and parse_time(at) < kickoff and (src_at is None or parse_time(at) > parse_time(src_at)):
                    src, src_at = hit, at
        for f in FROZEN_FIELDS:
            g[f] = src.get(f) if src else None  # no pre-kickoff numbers: blank, never live ones
        g["frozen_at"] = src_at
        g["frozen_for"] = "kickoff"
        if src and key not in store:
            store[key] = {**{f: g[f] for f in FROZEN_FIELDS}, "frozen_at": src_at, "frozen_for": "kickoff",
                          "away": g["away"], "home": g["home"], "start_time": g["start_time"]}


# --- Season record -------------------------------------------------------------------

def update_record(record, games):
    """Every game with splits, once frozen at kickoff. Written once, never
    changed; only scores are filled in later. The page works out M/S/T at the
    chosen dropdowns and grades them at the line at kickoff."""
    for g in games:
        if not g.get("frozen_at") or g.get("home_money") is None or g.get("line") is None:
            continue
        key = key_of(g)
        if key not in record:
            record[key] = {**{f: g.get(f) for f in RECORD_FIELDS},
                           "home_score": None, "away_score": None, "final": False}
        elif record[key].get("tue_line") is None and g.get("tue_line") is not None:
            record[key]["tue_line"] = g["tue_line"]


def fetch_finals(games, record, now):
    """Final scores: from this pull, plus a results lookup for any week with
    a started record game that isn't final yet."""
    finals = {key_of(g): g for g in games if g["status"] == "complete" and g["home_score"] is not None}
    pending = {(r["league"], r["an_week"]) for k, r in record.items()
               if not r["final"] and k not in finals and parse_time(r["start_time"]) <= now}
    for league, an_week in pending:
        sport = next(s for s, l in LEAGUES.items() if l == league)
        for raw in get_json(API.format(sport=sport) + f"?bookIds={LINE_BOOK}&periods=event&week={an_week}").get("games", []):
            g = parse_game(raw, league)
            if g["status"] == "complete" and g["home_score"] is not None:
                finals[key_of(g)] = g
    return finals


# --- Main ------------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date().isoformat()

    feed = []
    for sport, league in LEAGUES.items():
        data = get_json(PUBLIC_API.format(sport=sport) + f"?bookIds={SPLITS_BOOK},{LINE_BOOK}&periods=event")
        if "games" not in data:
            sys.exit(f"Unexpected response shape for {sport}: keys={list(data)}")
        feed += [parse_game(g, league) for g in data["games"]]
    if not feed:
        sys.exit("No games returned - refusing to overwrite data with an empty pull.")
    if not any(g["home_money"] is not None for g in feed):
        sys.exit("Games returned but none carry bet/money % - Action Network may have changed or paywalled it.")

    # Tuesday line from the raw feed, before anything is frozen
    tue_path = DATA / "tuesday_lines.json"
    tue_store = load_json(tue_path, {})
    record_tuesday_lines(feed, now, tue_store)
    save_json(tue_path, dict(sorted(tue_store.items())))

    # This week only (it turns over on Tuesday), plus its games the feed has dropped
    week = week_id_for(now.isoformat())
    prev = load_json(DATA / "live.json", {})
    games = [copy.deepcopy(g) for g in feed if g["week_id"] == week]
    games += carry_over_week(feed, week, prev)

    kick_path = DATA / "kickoff_lines.json"
    kick_store = load_json(kick_path, {})
    freeze_started_games(games, now, kick_store, prev)
    save_json(kick_path, dict(sorted(kick_store.items())))

    # Raw snapshot of what the feed returned, per week
    by_week = {}
    for g in feed:
        by_week.setdefault(g["week_id"], []).append(g)
    for wk, wk_games in by_week.items():
        save_json(DATA / "weeks" / wk / f"{today}.json", {"pulled_at": now.isoformat(timespec="seconds"), "games": wk_games})

    snapshots = {}
    for g in games:
        g["tue_line"] = tuesday_line(g, tue_store, snapshots)

    rec_path = DATA / "record.json"
    record = load_json(rec_path, {})
    update_record(record, games)
    finals = fetch_finals(feed, record, now)
    for key, r in record.items():
        fin = finals.get(key)
        if not r["final"] and fin:
            r.update(home_score=fin["home_score"], away_score=fin["away_score"], final=True)
    save_json(rec_path, dict(sorted(record.items())))

    for g in games:
        fin = finals.get(key_of(g))
        if fin:
            g.update(home_score=fin["home_score"], away_score=fin["away_score"], status="complete")
    save_json(DATA / "live.json", {
        "pulled_at": now.isoformat(timespec="seconds"),
        "edge_threshold": EDGE_THRESHOLD,
        "games": sorted(games, key=lambda g: g["start_time"]),
    })

    graded = sum(1 for r in record.values() if r["final"])
    print(f"Week {week}: {len(games)} games, {sum(1 for g in games if g['home_money'] is not None)} with splits, "
          f"{sum(1 for g in games if g['tue_line'] is not None)} with a Tuesday line; "
          f"record {len(record)} games ({graded} final)")


if __name__ == "__main__":
    main()
