# oc-fleet design

CLI dashboard buat manage OpenCode agent fleet (Hermes-side tool).

## Commands
- `fleet status` - overview: active sessions, stats, last N completions
- `fleet dispatch TASK --workdir DIR [--model M] [--title T]` - fire task
- `fleet watch` - live SSE stream, prints completions realtime
- `fleet sessions [N]` - list recent sessions + outcomes
- `fleet show <session_id>` - full detail satu session (reply + outcome)
- `fleet stats` - aggregate stats (sessions, prompts, tools, tokens, cost)

## Architecture
- fleet.py: core lib (Fleet class) - OpenCode buat, tested
- cli.py: argparse layer, thin wrapper
- watch mode: SSE /api/event, print `session.execution.succeeded/failed`

## Non-goals
- Bukan pengganti hermes delegation; ini spesifik OpenCode fleet
- Gak manage opencode serve lifecycle (udah ada service)

## Konvensi
- Author Rouge <tarigansdavid@gmail.com>
- NO em-dash anywhere (sed replace ' - ')
