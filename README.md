# ESPN Fantasy Basketball Exporter

Export an ESPN fantasy basketball league to a single JSON snapshot that a Java
application can read. The export includes league rules, teams, rosters,
available players, schedules and statistics, current matchups and box scores,
recent transactions and activity, and draft results.

The current JSON schema version is `2`.

## Requirements

- Python 3.10 or newer
- [`espn-api`](https://github.com/cwendt94/espn-api) (`1.0.1` is the version
  currently used by this project)
- An internet connection to ESPN
- `ESPN_S2` and `ESPN_SWID` cookie values when accessing a private league

Create a virtual environment and install the dependency:

```bash
python3 -m venv env
source env/bin/activate
python -m pip install "espn-api==1.0.1"
```

On Windows, activate the environment with `env\Scripts\activate` instead.

## Usage

At minimum, provide the ESPN league ID and season year:

```bash
env/bin/python export_basketball_rosters.py \
  --league-id 123456789 \
  --year 2027
```

By default, this writes `data/basketball_snapshot.json` relative to the
process's working directory. Parent directories are created automatically, and
the file is replaced atomically so another application does not read a partial
snapshot.

### Command-line arguments

| Argument                            | Required | Default                         | Description                                                                                                                        |
| ----------------------------------- | -------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `--league-id <integer>`             | Yes      | —                               | ESPN fantasy league ID.                                                                                                            |
| `--year <integer>`                  | Yes      | —                               | ESPN season year to export. Use the season shown in the league URL/settings.                                                       |
| `--output <path>`                   | No       | `data/basketball_snapshot.json` | Destination JSON file. The filename must end in `.json`.                                                                           |
| `--my-team-id <integer>`            | No       | None                            | Identifies your team. The matching team gets `is_my_team: true`, and its ID is written to `context.my_team_id`.                    |
| `--free-agent-limit <integer>`      | No       | `100`                           | Maximum number of free-agent/waiver players to request. Use `0` to skip this request. Must not be negative.                        |
| `--recent-activity-limit <integer>` | No       | `50`                            | Maximum recent add/drop/trade activity records to request. Use `0` to skip this request. Must not be negative.                     |
| `--news-limit <integer>`            | No       | `0`                             | Number of priority players for which to request news. Use `0` to skip news requests. Must not be negative.                         |
| `--skip-player-details`             | No       | Off                             | Skips player-card enrichment for a faster, smaller export. Schedules and detailed stat splits may then be absent or less complete. |
| `--include-game-logs`               | No       | Off                             | Includes individual game stat splits. This can substantially increase the JSON size.                                               |
| `--debug`                           | No       | Off                             | Enables debugging in the underlying ESPN API client.                                                                               |
| `-h`, `--help`                      | No       | —                               | Prints the built-in command help and exits.                                                                                        |

Example with the optional export controls:

```bash
env/bin/python export_basketball_rosters.py \
  --league-id 123456789 \
  --year 2027 \
  --output data/my_league.json \
  --my-team-id 6 \
  --free-agent-limit 150 \
  --recent-activity-limit 75 \
  --news-limit 25 \
  --include-game-logs
```

### Private leagues

Set both ESPN cookie values in the exporter's environment. Supplying only one
is treated as a configuration error.

```bash
ESPN_S2='your-espn-s2-cookie' \
ESPN_SWID='{YOUR-SWID-COOKIE}' \
env/bin/python export_basketball_rosters.py \
  --league-id 123456789 \
  --year 2027
```

Do not put these values in source control or pass them as command-line
arguments. Public leagues should leave both variables unset.

## Calling the exporter from Java

Pass each argument to `ProcessBuilder` as a separate list item. This avoids
shell quoting problems and works when paths contain spaces.

For the repository's default virtual environment, pass
`projectDirectory.resolve("env/bin/python")` on macOS/Linux or
`projectDirectory.resolve("env\\Scripts\\python.exe")` on Windows. Keep the
cookie values in your application's secret/configuration system and pass them
only through `ProcessBuilder.environment()`.

The exporter writes a human-readable success summary to standard output. Your
Java application should use the process exit code to decide whether the JSON is
safe to read.

### Exit codes

|               Code | Meaning                                                                                                                                                                         |
| -----------------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|                `0` | Export completed and the output file was written.                                                                                                                               |
|                `2` | Invalid configuration or arguments, including incomplete credentials or an invalid output filename/limit. `argparse` also uses code `2` for malformed or missing CLI arguments. |
|                `3` | ESPN denied access; private-league credentials may be missing or invalid.                                                                                                       |
|                `4` | ESPN could not find the requested league/year.                                                                                                                                  |
|                `5` | A network request to ESPN failed.                                                                                                                                               |
|                `6` | ESPN returned an unexpected response.                                                                                                                                           |
| Other nonzero code | An unexpected Python/runtime failure occurred. Treat the export as failed and inspect the captured output.                                                                      |

## JSON contract

The top-level document contains:

```text
schema_version
generated_at
context
league
teams
available_players
current_matchups
box_scores
recent_transactions
recent_activity
draft
warnings
```

Important integration details:

- Check `schema_version` before deserializing fields into a strict Java model.
- ISO date/time strings include a UTC offset and can be parsed as Java
  `OffsetDateTime`. Date-only values can be parsed as `LocalDate`.
- Optional ESPN data is represented by JSON `null`, empty arrays/objects, or
  omitted enrichment data, depending on the source field.
- A failure in an optional ESPN endpoint does not necessarily fail the export.
  It is recorded in the top-level `warnings` array with `section`,
  `error_type`, and `message` fields.
- ESPN IDs and epoch timestamps should be represented as Java `long` values.
- [`data/my_league_test.json`](data/my_league_test.json) is a representative
  schema-v2 document for developing and testing the Java deserializer without
  making a live ESPN request.

## Getting IDs from ESPN

The league ID is the numeric value associated with `leagueId` in the ESPN
Fantasy Basketball league URL. Team IDs are written to each `teams[].id` in an
export; use the relevant value as `--my-team-id` on later runs.
