"""Export every fantasy team's current roster to nested JSON.

Private ESPN leagues require the ESPN_S2 and ESPN_SWID environment variables.
Public leagues can be read without them.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from requests.exceptions import RequestException
from espn_api.basketball import League
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague

JSON_SCHEMA_VERSION = 1


def value_or_none(values: dict, key: str):
    """Return a stat value as-is; missing JSON values are represented by null."""
    return values.get(key)


def optional_scalar(value):
    """Normalize the library's empty-list sentinel into a JSON null."""
    return None if value == [] else value


def player_json(player, year: int) -> dict:
    """Convert one Player object into a JSON-safe, Java-friendly dictionary."""
    season_stats = player.stats.get(f"{year}_total", {})
    totals = season_stats.get("total") or {}
    averages = season_stats.get("avg") or {}
    expected_return_date = player.expected_return_date

    return {
        "id": player.playerId,
        "name": player.name,
        "nba_team": player.proTeam,
        "primary_position": player.position,
        "lineup_slot": player.lineupSlot,
        "eligible_slots": list(player.eligibleSlots),
        "acquisition_type": optional_scalar(player.acquisitionType),
        "injury_status": optional_scalar(player.injuryStatus),
        "injured": player.injured,
        "expected_return_date": (
            expected_return_date.isoformat() if expected_return_date else None
        ),
        "positional_rank": optional_scalar(player.posRank),
        "fantasy_points": {
            "total": player.total_points,
            "average": player.avg_points,
            "projected_total": player.projected_total_points,
            "projected_average": player.projected_avg_points,
        },
        "stats": {
            "games_played": value_or_none(totals, "GP"),
            "points": {
                "total": value_or_none(totals, "PTS"),
                "average": value_or_none(averages, "PTS"),
            },
            "rebounds": {
                "total": value_or_none(totals, "REB"),
                "average": value_or_none(averages, "REB"),
            },
            "assists": {
                "total": value_or_none(totals, "AST"),
                "average": value_or_none(averages, "AST"),
            },
            "steals": {
                "total": value_or_none(totals, "STL"),
                "average": value_or_none(averages, "STL"),
            },
            "blocks": {
                "total": value_or_none(totals, "BLK"),
                "average": value_or_none(averages, "BLK"),
            },
            "three_pointers_made": {
                "total": value_or_none(totals, "3PM"),
                "average": value_or_none(averages, "3PM"),
            },
            "turnovers": {
                "total": value_or_none(totals, "TO"),
                "average": value_or_none(averages, "TO"),
            },
            "field_goal_percentage": value_or_none(averages, "FG%"),
            "free_throw_percentage": value_or_none(averages, "FT%"),
        },
    }


def roster_document(league) -> dict:
    """Build a nested League -> Team -> Player document for JSON consumers."""
    teams = []
    for team in league.teams:
        teams.append(
            {
                "id": team.team_id,
                "name": team.team_name,
                "abbreviation": team.team_abbrev,
                "roster": [player_json(player, league.year) for player in team.roster],
            }
        )

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "league": {
            "id": league.league_id,
            "season": league.year,
            "name": getattr(league.settings, "name", ""),
            "scoring_type": getattr(league.settings, "scoring_type", None),
        },
        "teams": teams,
    }


def write_rosters_json(league, output_path: Path) -> int:
    """Write nested roster JSON and return the number of exported players."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = roster_document(league)

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(document, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    return sum(len(team["roster"]) for team in document["teams"])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch an ESPN fantasy basketball league and export its rosters."
    )
    parser.add_argument(
        "--league-id",
        type=int,
        required=True,
        help="Numeric league ID from the ESPN fantasy league URL.",
    )
    parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="ESPN season ID (verify it in the league URL).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/basketball_rosters.json"),
        help="Destination JSON path (default: data/basketball_rosters.json).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the underlying ESPN requests and responses.",
    )
    return parser.parse_args(argv)


def credentials_from_environment() -> tuple[str | None, str | None]:
    espn_s2 = os.getenv("ESPN_S2")
    swid = os.getenv("ESPN_SWID")

    if bool(espn_s2) != bool(swid):
        raise ValueError(
            "Set both ESPN_S2 and ESPN_SWID, or unset both for a public league."
        )
    return espn_s2, swid


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        if args.output.suffix.lower() != ".json":
            raise ValueError("--output must end in .json")
        espn_s2, swid = credentials_from_environment()
        league = League(
            league_id=args.league_id,
            year=args.year,
            espn_s2=espn_s2,
            swid=swid,
            debug=args.debug,
        )
        row_count = write_rosters_json(league, args.output)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except ESPNAccessDenied:
        print(
            "ESPN denied access. For a private league, export valid ESPN_S2 and "
            "ESPN_SWID cookie values before running the script.",
            file=sys.stderr,
        )
        return 3
    except ESPNInvalidLeague:
        print(
            "ESPN could not find that league. Check both --league-id and --year; "
            "the league must exist in that season.",
            file=sys.stderr,
        )
        return 4
    except RequestException as error:
        print(f"Could not communicate with ESPN: {error}", file=sys.stderr)
        return 5

    print(f"Wrote {row_count} players to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
