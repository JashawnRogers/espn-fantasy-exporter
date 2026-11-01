#!/usr/bin/env python3
"""Export an ESPN fantasy basketball league as Java-friendly JSON.

The snapshot includes league rules, team resources, rosters, available players,
player schedules and stat splits, current matchup data, and recent activity.
Private leagues require ESPN_S2 and ESPN_SWID environment variables.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from requests.exceptions import RequestException

from espn_api.basketball import League
from espn_api.basketball.constant import POSITION_MAP, STATS_MAP, TRANSACTION_TYPES
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

JSON_SCHEMA_VERSION = 2
PLAYER_DETAIL_BATCH_SIZE = 40

STAT_DISPLAY_NAMES = {
    "PTS": "points",
    "BLK": "blocks",
    "STL": "steals",
    "AST": "assists",
    "OREB": "offensive_rebounds",
    "DREB": "defensive_rebounds",
    "REB": "rebounds",
    "EJ": "ejections",
    "FF": "flagrant_fouls",
    "PF": "personal_fouls",
    "TF": "technical_fouls",
    "TO": "turnovers",
    "DQ": "disqualifications",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
    "3PM": "three_pointers_made",
    "3PA": "three_pointers_attempted",
    "FG%": "field_goal_percentage",
    "FT%": "free_throw_percentage",
    "3PT%": "three_point_percentage",
    "AFG%": "adjusted_field_goal_percentage",
    "FGMI": "field_goals_missed",
    "FTMI": "free_throws_missed",
    "3PMI": "three_pointers_missed",
    "APG": "assists_per_game",
    "BPG": "blocks_per_game",
    "MPG": "minutes_per_game",
    "PPG": "points_per_game",
    "RPG": "rebounds_per_game",
    "SPG": "steals_per_game",
    "TOPG": "turnovers_per_game",
    "3PG": "three_pointers_made_per_game",
    "PPM": "points_per_minute",
    "A/TO": "assist_to_turnover_ratio",
    "STR": "streak",
    "DD": "double_doubles",
    "TD": "triple_doubles",
    "QD": "quadruple_doubles",
    "MIN": "minutes",
    "GS": "games_started",
    "GP": "games_played",
    "TW": "team_wins",
    "FTR": "free_throw_rate",
}


def optional_scalar(value):
    """Normalize the library's empty-list sentinel into a JSON null."""
    return None if value in ([], {}) else value


def utc_iso(value):
    """Return a date/datetime as an ISO string that Jackson can parse."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def epoch_millis_iso(value):
    """Convert an ESPN millisecond timestamp to UTC while preserving null."""
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def stat_code(stat_id) -> str:
    return STATS_MAP.get(str(stat_id), f"UNKNOWN_STAT_{stat_id}")


def stat_name(stat_id) -> str:
    code = stat_code(stat_id)
    return STAT_DISPLAY_NAMES.get(code, code.lower())


def scoring_rules_json(settings) -> dict:
    """Turn ESPN stat IDs into stable codes and readable names."""
    raw = getattr(settings, "_raw_scoring_settings", {}) or {}
    items = []

    for item in raw.get("scoringItems", []):
        item_stat_id = item.get("statId")
        items.append(
            {
                "stat_id": item_stat_id,
                "stat_code": stat_code(item_stat_id),
                "stat_name": stat_name(item_stat_id),
                "fantasy_point_value": item.get("points"),
                "is_reverse": item.get("isReverseItem", False),
            }
        )

    return {
        "type": raw.get("scoringType", getattr(settings, "scoring_type", None)),
        "allow_out_of_position_scoring": raw.get("allowOutOfPositionScoring"),
        "items": items,
    }


def named_position_counts(values) -> list[dict]:
    """Convert ESPN position-id dictionaries into named JSON entries."""
    result = []
    for raw_id, count in (values or {}).items():
        try:
            position_id = int(raw_id)
        except (TypeError, ValueError):
            position_id = raw_id
        name = POSITION_MAP.get(position_id, f"POSITION_{position_id}")
        result.append(
            {
                "position_id": position_id,
                "position": name or "UNASSIGNED",
                "count_or_limit": count,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            not isinstance(item["position_id"], int),
            item["position_id"],
        ),
    )


def league_rules_json(settings, raw_settings: dict) -> dict:
    roster = raw_settings.get("rosterSettings", {})
    schedule = raw_settings.get("scheduleSettings", {})

    return {
        "scoring": scoring_rules_json(settings),
        "roster": {
            "lineup_slots": named_position_counts(roster.get("lineupSlotCounts")),
            "position_limits": named_position_counts(roster.get("positionLimits")),
            "is_bench_unlimited": roster.get("isBenchUnlimited"),
            "lineup_lock_time_type": roster.get("lineupLocktimeType"),
        },
        "acquisitions": {
            "uses_faab": getattr(settings, "faab", False),
            "starting_budget": getattr(settings, "acquisition_budget", 0),
            "season_limit": getattr(settings, "acquisition_limit", None),
            "matchup_limit": getattr(settings, "matchup_acquisition_limit", None),
            "limit_applies_per_scoring_period": getattr(
                settings, "matchup_limit_per_scoring_period", None
            ),
            "minimum_bid": getattr(settings, "minimum_bid", 0),
            "waiver_process_days": list(
                getattr(settings, "waiver_process_days", []) or []
            ),
            "waiver_process_hour": getattr(settings, "waiver_process_hour", None),
        },
        "trades": {
            "deadline_epoch_ms": getattr(settings, "trade_deadline", None),
            "deadline": epoch_millis_iso(getattr(settings, "trade_deadline", None)),
            "revision_hours": getattr(settings, "trade_revision_hours", None),
            "veto_votes_required": getattr(settings, "veto_votes_required", None),
        },
        "season": {
            "regular_season_matchup_count": getattr(settings, "reg_season_count", None),
            "playoff_team_count": getattr(settings, "playoff_team_count", None),
            "playoff_matchup_period_length": getattr(
                settings, "playoff_matchup_period_length", None
            ),
            "matchup_periods": schedule.get(
                "matchupPeriods", getattr(settings, "matchup_periods", None)
            ),
            "keeper_count": getattr(settings, "keeper_count", None),
            "tie_rule": getattr(settings, "tie_rule", None),
            "playoff_tie_rule": getattr(settings, "playoff_tie_rule", None),
        },
    }


def player_schedule_json(player) -> list[dict]:
    games = []
    for scoring_period, game in (getattr(player, "schedule", {}) or {}).items():
        try:
            period = int(scoring_period)
        except (TypeError, ValueError):
            period = scoring_period
        games.append(
            {
                "scoring_period": period,
                "opponent": game.get("team"),
                "starts_at": utc_iso(game.get("date")),
            }
        )
    return sorted(games, key=lambda game: str(game["scoring_period"]))


def stat_splits_json(stats: dict, include_game_logs: bool = False) -> dict:
    """Preserve season, projection, recent, and game-level stat splits."""
    result = {}
    for split_name, split in (stats or {}).items():
        if not isinstance(split, dict):
            continue
        if str(split_name).isdigit() and not include_game_logs:
            continue
        result[str(split_name)] = {
            "fantasy_points_total": split.get("applied_total"),
            "fantasy_points_average": split.get("applied_avg"),
            "opponent": split.get("team"),
            "date": utc_iso(split.get("date")),
            "totals": split.get("total") or {},
            "averages": split.get("avg") or {},
        }
    return result


def stat_pair(totals: dict, averages: dict, key: str) -> dict:
    return {"total": totals.get(key), "average": averages.get(key)}


def player_json(
    player,
    year: int,
    detailed_player=None,
    fantasy_status: str = None,
    fantasy_team_id: int = None,
    news=None,
    include_game_logs: bool = False,
) -> dict:
    """Convert one Player into a stable Java-facing representation."""
    detail = detailed_player or player
    combined_stats = dict(getattr(player, "stats", {}) or {})
    combined_stats.update(getattr(detail, "stats", {}) or {})
    season_stats = combined_stats.get(f"{year}_total", {})
    totals = season_stats.get("total") or {}
    averages = season_stats.get("avg") or {}
    expected_return_date = getattr(detail, "expected_return_date", None) or getattr(
        player, "expected_return_date", None
    )
    player_news = news
    if player_news is None:
        possible_news = getattr(detail, "news", [])
        player_news = possible_news if isinstance(possible_news, list) else []

    return {
        "id": getattr(player, "playerId", None),
        "name": optional_scalar(getattr(player, "name", None)),
        "fantasy_status": fantasy_status,
        "fantasy_team_id": fantasy_team_id,
        "nba_team": optional_scalar(getattr(detail, "proTeam", None)),
        "primary_position": optional_scalar(getattr(detail, "position", None)),
        "lineup_slot": optional_scalar(getattr(player, "lineupSlot", None)),
        "eligible_slots": list(getattr(detail, "eligibleSlots", []) or []),
        "acquisition_type": optional_scalar(getattr(player, "acquisitionType", None)),
        "injury": {
            "status": optional_scalar(getattr(detail, "injuryStatus", None)),
            "injured": bool(getattr(detail, "injured", False)),
            "expected_return_date": utc_iso(expected_return_date),
        },
        "positional_rank": optional_scalar(getattr(detail, "posRank", None)),
        "fantasy_points": {
            "season_total": getattr(detail, "total_points", 0),
            "season_average": getattr(detail, "avg_points", 0),
            "projected_season_total": getattr(detail, "projected_total_points", 0),
            "projected_season_average": getattr(detail, "projected_avg_points", 0),
        },
        "season_stats": {
            "games_played": totals.get("GP"),
            "points": stat_pair(totals, averages, "PTS"),
            "rebounds": stat_pair(totals, averages, "REB"),
            "assists": stat_pair(totals, averages, "AST"),
            "steals": stat_pair(totals, averages, "STL"),
            "blocks": stat_pair(totals, averages, "BLK"),
            "three_pointers_made": stat_pair(totals, averages, "3PM"),
            "turnovers": stat_pair(totals, averages, "TO"),
            "minutes": stat_pair(totals, averages, "MIN"),
            "field_goals": {
                "made": stat_pair(totals, averages, "FGM"),
                "attempted": stat_pair(totals, averages, "FGA"),
                "percentage": averages.get("FG%"),
            },
            "free_throws": {
                "made": stat_pair(totals, averages, "FTM"),
                "attempted": stat_pair(totals, averages, "FTA"),
                "percentage": averages.get("FT%"),
            },
        },
        "stat_splits": stat_splits_json(combined_stats, include_game_logs),
        "schedule": player_schedule_json(detail),
        "news": player_news,
    }


def team_reference(team):
    if not team:
        return None
    if isinstance(team, int):
        return {"id": team, "name": None}
    return {
        "id": getattr(team, "team_id", None),
        "name": getattr(team, "team_name", None),
    }


def matchup_json(matchup) -> dict:
    return {
        "winner": getattr(matchup, "winner", None),
        "home_team": team_reference(getattr(matchup, "home_team", None)),
        "away_team": team_reference(getattr(matchup, "away_team", None)),
        "home_final_score": getattr(matchup, "home_final_score", None),
        "away_final_score": getattr(matchup, "away_final_score", None),
        "home_live_score": getattr(matchup, "home_team_live_score", None),
        "away_live_score": getattr(matchup, "away_team_live_score", None),
        "home_categories": getattr(matchup, "home_team_cats", None),
        "away_categories": getattr(matchup, "away_team_cats", None),
    }


def box_player_json(player) -> dict:
    return {
        "id": getattr(player, "playerId", None),
        "name": optional_scalar(getattr(player, "name", None)),
        "nba_team": optional_scalar(getattr(player, "proTeam", None)),
        "slot": optional_scalar(getattr(player, "slot_position", None)),
        "nba_opponent": optional_scalar(getattr(player, "pro_opponent", None)),
        "game_played_percent": getattr(player, "game_played", None),
        "fantasy_points": getattr(player, "points", None),
        "fantasy_points_breakdown": getattr(player, "points_breakdown", {}) or {},
        "injury_status": optional_scalar(getattr(player, "injuryStatus", None)),
    }


def box_score_json(box_score) -> dict:
    result = {
        "winner": getattr(box_score, "winner", None),
        "scoring_period": getattr(box_score, "scoring_period", None),
        "home_team": team_reference(getattr(box_score, "home_team", None)),
        "away_team": team_reference(getattr(box_score, "away_team", None)),
        "home_lineup": [
            box_player_json(player)
            for player in (getattr(box_score, "home_lineup", []) or [])
        ],
        "away_lineup": [
            box_player_json(player)
            for player in (getattr(box_score, "away_lineup", []) or [])
        ],
    }

    if hasattr(box_score, "home_score"):
        result["points"] = {
            "home_score": box_score.home_score,
            "away_score": box_score.away_score,
            "home_projected": box_score.home_projected,
            "away_projected": box_score.away_projected,
        }
    if hasattr(box_score, "home_stats"):
        result["categories"] = {
            "home_wins": box_score.home_wins,
            "home_ties": box_score.home_ties,
            "home_losses": box_score.home_losses,
            "away_wins": box_score.away_wins,
            "away_ties": box_score.away_ties,
            "away_losses": box_score.away_losses,
            "home_stats": box_score.home_stats,
            "away_stats": box_score.away_stats,
        }
    return result


def transaction_json(transaction) -> dict:
    timestamp = getattr(transaction, "date", None)
    return {
        "id": getattr(transaction, "id", None),
        "related_transaction_id": getattr(transaction, "related_transaction_id", None),
        "team": team_reference(getattr(transaction, "team", None)),
        "team_id": getattr(transaction, "team_id", None),
        "type": getattr(transaction, "type", None),
        "status": getattr(transaction, "status", None),
        "scoring_period": getattr(transaction, "scoring_period", None),
        "timestamp_epoch_ms": timestamp,
        "timestamp": epoch_millis_iso(timestamp),
        "bid_amount": getattr(transaction, "bid_amount", None),
        "items": [
            {
                "type": getattr(item, "type", None),
                "player_id": getattr(item, "playerId", None),
                "player_name": getattr(item, "player", None),
                "from_team_id": getattr(item, "from_team_id", None),
                "to_team_id": getattr(item, "to_team_id", None),
            }
            for item in (getattr(transaction, "items", []) or [])
        ],
    }


def activity_json(activity) -> dict:
    return {
        "timestamp_epoch_ms": getattr(activity, "date", None),
        "timestamp": epoch_millis_iso(getattr(activity, "date", None)),
        "actions": [
            {
                "team": team_reference(team),
                "action": action,
                "player_name": player,
                "lineup_position": position or None,
            }
            for team, action, player, position in (
                getattr(activity, "actions", []) or []
            )
        ],
    }


def draft_pick_json(pick) -> dict:
    return {
        "round": getattr(pick, "round_num", None),
        "round_pick": getattr(pick, "round_pick", None),
        "player_id": getattr(pick, "playerId", None),
        "player_name": getattr(pick, "playerName", None),
        "team": team_reference(getattr(pick, "team", None)),
        "bid_amount": getattr(pick, "bid_amount", None),
        "keeper": getattr(pick, "keeper_status", None),
    }


def fetch_optional(section: str, operation, warnings: list, default):
    """Let optional ESPN endpoint failures degrade to a warning."""
    try:
        return operation()
    except Exception as error:  # ESPN sometimes changes individual endpoints.
        warnings.append(
            {
                "section": section,
                "error_type": type(error).__name__,
                "message": str(error),
            }
        )
        return default


def chunks(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def normalize_player_result(result) -> list:
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


def fetch_detailed_players(league, player_ids: list[int], warnings: list) -> dict:
    """Batch player-card calls for schedules and recent/game stat splits."""
    detailed = {}
    ids = sorted({player_id for player_id in player_ids if isinstance(player_id, int)})
    for batch_number, batch in enumerate(
        chunks(ids, PLAYER_DETAIL_BATCH_SIZE), start=1
    ):
        players = fetch_optional(
            f"player_details_batch_{batch_number}",
            lambda current_batch=batch: league.player_info(playerId=current_batch),
            warnings,
            [],
        )
        for player in normalize_player_result(players):
            detailed[player.playerId] = player
    return detailed


def fetch_news(league, player_ids: list[int], limit: int, warnings: list) -> dict:
    """Fetch news only for a bounded number of priority players."""
    result = {}
    for player_id in player_ids[:limit]:
        raw = fetch_optional(
            f"player_news_{player_id}",
            lambda current_id=player_id: league.espn_request.get_player_news(
                current_id
            ),
            warnings,
            {},
        )
        feed = raw.get("news", {}).get("feed", []) if isinstance(raw, dict) else []
        result[player_id] = [
            {
                "published": item.get("published"),
                "headline": item.get("headline"),
                "story": item.get("story"),
            }
            for item in feed
        ]
    return result


def team_json(
    team,
    league,
    detailed_players: dict,
    news_by_player: dict,
    my_team_id,
    include_game_logs: bool,
):
    budget_remaining = None
    if getattr(league.settings, "faab", False):
        budget_remaining = getattr(league.settings, "acquisition_budget", 0) - getattr(
            team, "acquisition_budget_spent", 0
        )

    return {
        "id": team.team_id,
        "is_my_team": my_team_id is not None and team.team_id == my_team_id,
        "name": team.team_name,
        "abbreviation": team.team_abbrev,
        "division_id": getattr(team, "division_id", None),
        "division_name": getattr(team, "division_name", None),
        "logo_url": getattr(team, "logo_url", None),
        "record": {
            "wins": getattr(team, "wins", 0),
            "losses": getattr(team, "losses", 0),
            "ties": getattr(team, "ties", 0),
            "standing": getattr(team, "standing", None),
            "final_standing": getattr(team, "final_standing", None),
            "points_for": getattr(team, "points_for", None),
            "points_against": getattr(team, "points_against", None),
            "category_totals": getattr(team, "stats", None),
        },
        "transactions": {
            "acquisitions": getattr(team, "acquisitions", 0),
            "drops": getattr(team, "drops", 0),
            "trades": getattr(team, "trades", 0),
            "acquisition_budget_spent": getattr(team, "acquisition_budget_spent", 0),
            "acquisition_budget_remaining": budget_remaining,
        },
        "roster": [
            player_json(
                player,
                league.year,
                detailed_player=detailed_players.get(player.playerId),
                fantasy_status="ROSTERED",
                fantasy_team_id=team.team_id,
                news=news_by_player.get(player.playerId),
                include_game_logs=include_game_logs,
            )
            for player in team.roster
        ],
    }


def build_snapshot(
    league,
    raw_settings: dict,
    available_players: list,
    detailed_players: dict,
    current_matchups: list,
    current_matchup_box_scores: list,
    current_day_box_scores: list,
    recent_transactions: list,
    recent_activity: list,
    news_by_player: dict,
    warnings: list,
    my_team_id=None,
    include_game_logs: bool = False,
) -> dict:
    teams = [
        team_json(
            team,
            league,
            detailed_players,
            news_by_player,
            my_team_id,
            include_game_logs,
        )
        for team in league.teams
    ]

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "context": {
            "my_team_id": my_team_id,
            "current_matchup_period": getattr(league, "currentMatchupPeriod", None),
            "current_scoring_period": getattr(league, "scoringPeriodId", None),
            "first_scoring_period": getattr(league, "firstScoringPeriod", None),
            "final_scoring_period": getattr(league, "finalScoringPeriod", None),
            "previous_seasons": list(getattr(league, "previousSeasons", []) or []),
        },
        "league": {
            "id": league.league_id,
            "season": league.year,
            "name": getattr(league.settings, "name", ""),
            "team_count": getattr(league.settings, "team_count", len(teams)),
            "rules": league_rules_json(league.settings, raw_settings),
        },
        "teams": teams,
        "available_players": [
            player_json(
                player,
                league.year,
                detailed_player=detailed_players.get(player.playerId),
                fantasy_status="FREE_AGENT_OR_WAIVERS",
                news=news_by_player.get(player.playerId),
                include_game_logs=include_game_logs,
            )
            for player in available_players
        ],
        "current_matchups": [matchup_json(matchup) for matchup in current_matchups],
        "box_scores": {
            "current_matchup": [
                box_score_json(box_score) for box_score in current_matchup_box_scores
            ],
            "current_scoring_period": [
                box_score_json(box_score) for box_score in current_day_box_scores
            ],
        },
        "recent_transactions": [
            transaction_json(transaction) for transaction in recent_transactions
        ],
        "recent_activity": [
            activity_json(activity)
            for activity in recent_activity
            if getattr(activity, "actions", None)
        ],
        "draft": [draft_pick_json(pick) for pick in getattr(league, "draft", [])],
        "warnings": warnings,
    }


def write_snapshot_json(document: dict, output_path: Path):
    """Atomically replace the JSON so Java never reads a partial file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(document, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")
    temporary_path.replace(output_path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export an ESPN fantasy basketball league for a Java assistant."
    )
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/basketball_snapshot.json"),
    )
    parser.add_argument(
        "--my-team-id",
        type=int,
        help="Marks your fantasy team in the JSON without guessing from its name.",
    )
    parser.add_argument(
        "--free-agent-limit",
        type=int,
        default=100,
        help="Maximum free-agent/waiver players to request; 0 skips them.",
    )
    parser.add_argument(
        "--recent-activity-limit",
        type=int,
        default=50,
        help="Maximum recent add/drop/trade activity records; 0 skips them.",
    )
    parser.add_argument(
        "--news-limit",
        type=int,
        default=0,
        help="Fetch news for this many priority players; 0 skips news requests.",
    )
    parser.add_argument(
        "--skip-player-details",
        action="store_true",
        help="Skip player-card enrichment when a faster, smaller export is desired.",
    )
    parser.add_argument(
        "--include-game-logs",
        action="store_true",
        help="Include every player game log; substantially increases JSON size.",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def credentials_from_environment() -> tuple[str | None, str | None]:
    espn_s2 = os.getenv("ESPN_S2")
    swid = os.getenv("ESPN_SWID")
    if bool(espn_s2) != bool(swid):
        raise ValueError(
            "Set both ESPN_S2 and ESPN_SWID, or unset both for a public league."
        )
    return espn_s2, swid


def validate_args(args):
    if args.output.suffix.lower() != ".json":
        raise ValueError("--output must end in .json")
    if args.free_agent_limit < 0:
        raise ValueError("--free-agent-limit cannot be negative")
    if args.recent_activity_limit < 0:
        raise ValueError("--recent-activity-limit cannot be negative")
    if args.news_limit < 0:
        raise ValueError("--news-limit cannot be negative")


def priority_news_ids(league, available_players: list, my_team_id) -> list[int]:
    ids = []
    if my_team_id is not None:
        my_team = next(
            (team for team in league.teams if team.team_id == my_team_id), None
        )
        ids.extend(player.playerId for player in getattr(my_team, "roster", []) or [])
    ids.extend(player.playerId for player in available_players)
    ids.extend(player.playerId for team in league.teams for player in team.roster)
    return list(dict.fromkeys(ids))


def collect_snapshot(league, args) -> dict:
    warnings = []

    raw_league = fetch_optional(
        "raw_league_settings",
        lambda: league.espn_request.get_league(),
        warnings,
        {},
    )
    raw_settings = (
        raw_league.get("settings", {}) if isinstance(raw_league, dict) else {}
    )

    available_players = []
    if args.free_agent_limit:
        available_players = fetch_optional(
            "available_players",
            lambda: league.free_agents(size=args.free_agent_limit),
            warnings,
            [],
        )

    all_player_ids = [
        player.playerId for team in league.teams for player in team.roster
    ] + [player.playerId for player in available_players]
    detailed_players = (
        {}
        if args.skip_player_details
        else fetch_detailed_players(league, all_player_ids, warnings)
    )

    current_matchups = fetch_optional(
        "current_matchups", lambda: league.scoreboard(), warnings, []
    )
    current_matchup_box_scores = fetch_optional(
        "current_matchup_box_scores", lambda: league.box_scores(), warnings, []
    )
    current_day_box_scores = fetch_optional(
        "current_scoring_period_box_scores",
        lambda: league.box_scores(
            matchup_period=league.currentMatchupPeriod,
            scoring_period=league.scoringPeriodId,
            matchup_total=False,
        ),
        warnings,
        [],
    )
    recent_transactions = fetch_optional(
        "recent_transactions",
        lambda: league.transactions(
            scoring_period=league.scoringPeriodId,
            types=set(TRANSACTION_TYPES),
        ),
        warnings,
        [],
    )
    recent_activity = []
    if args.recent_activity_limit:
        recent_activity = fetch_optional(
            "recent_activity",
            lambda: league.recent_activity(
                size=args.recent_activity_limit, include_moved=True
            ),
            warnings,
            [],
        )

    news_by_player = {}
    if args.news_limit:
        news_by_player = fetch_news(
            league,
            priority_news_ids(league, available_players, args.my_team_id),
            args.news_limit,
            warnings,
        )

    return build_snapshot(
        league=league,
        raw_settings=raw_settings,
        available_players=available_players,
        detailed_players=detailed_players,
        current_matchups=current_matchups,
        current_matchup_box_scores=current_matchup_box_scores,
        current_day_box_scores=current_day_box_scores,
        recent_transactions=recent_transactions,
        recent_activity=recent_activity,
        news_by_player=news_by_player,
        warnings=warnings,
        my_team_id=args.my_team_id,
        include_game_logs=args.include_game_logs,
    )


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        validate_args(args)
        espn_s2, swid = credentials_from_environment()
        league = League(
            league_id=args.league_id,
            year=args.year,
            espn_s2=espn_s2,
            swid=swid,
            debug=args.debug,
        )
        document = collect_snapshot(league, args)
        write_snapshot_json(document, args.output)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except ESPNAccessDenied:
        print(
            "ESPN denied access. For a private league, set valid ESPN_S2 and "
            "ESPN_SWID cookie values.",
            file=sys.stderr,
        )
        return 3
    except ESPNInvalidLeague:
        print(
            "ESPN could not find that league. Check --league-id and --year.",
            file=sys.stderr,
        )
        return 4
    except RequestException as error:
        print(f"Could not communicate with ESPN: {error}", file=sys.stderr)
        return 5
    except ESPNUnknownError as error:
        print(f"ESPN returned an unexpected response: {error}", file=sys.stderr)
        return 6

    rostered_count = sum(len(team["roster"]) for team in document["teams"])
    available_count = len(document["available_players"])
    print(
        f"Wrote {rostered_count} rostered players, {available_count} available "
        f"players, and {len(document['warnings'])} warnings to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
