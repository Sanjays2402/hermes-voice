# Agent tasks (read-only)

Lets a voice profile answer "What is Fritz working on?", "Is anything blocked?" or "What did Montclair
finish today?" from Hermes' own task boards (kanban) and scheduled jobs, without being able to change them.

`agent_tasks_mcp.py` is an MCP server (stdio) with three tools: `agent_tasks` (by agent, status or board),
`agent_task` (one task's details and latest notes) and `agent_schedules` (an agent's cron jobs). Every
command it runs is checked against an allow-list first: only `hermes kanban boards list`,
`hermes kanban --board <b> list`, `hermes kanban --board <b> show <id>` and `hermes -p <p> cron list`.
It cannot create, assign, comment, block, complete, dispatch or hand off. Keep the profile's own `kanban`
toolset disabled so the voice profile has no other way to.

## Set up

```bash
cp extras/agent-tasks/agent_tasks_mcp.py ~/.hermes/profiles/<profile>/scripts/
hermes -p <profile> config set mcp_servers.agent_tasks '{"command": "<hermes venv python>",
  "args": ["~/.hermes/profiles/<profile>/scripts/agent_tasks_mcp.py"],
  "env": {"HERMES_BIN": "<path to hermes>", "AGENT_ALIASES": "fritz=personal-coding,personal-integration"}}'
hermes -p <profile> gateway restart
```

`AGENT_ALIASES` maps an agent's name to the boards or assignees that count as its work
(`name=a,b;other=c`). Without it, a task counts as an agent's when its assignee or board names the agent.
Each `hermes kanban` call takes about a second, so an answer takes 2–4 s.
