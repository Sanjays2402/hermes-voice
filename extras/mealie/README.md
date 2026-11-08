# Mealie for a Hermes voice profile

A small MCP server that lets a Hermes profile use your [Mealie](https://mealie.io) recipes by voice:
"what's on the meal plan this week?", "read me the steps for the ramen", "add eggs to the shopping list".

Six tools, kept few so they cost little prompt: `search_recipes`, `get_recipe`, `meal_plan`,
`add_to_meal_plan`, `shopping_list`, `add_to_shopping_list`. Nothing deletes or edits recipes, so a
misheard sentence can't lose one.

## Set up

1. In Mealie: your profile → **API Tokens** → create one.
2. Put it in the profile's `.env` without echoing it:
   ```bash
   read -s "t?Mealie token: " && echo "MEALIE_API_TOKEN=$t" >> ~/.hermes/profiles/voice/.env && unset t
   ```
3. Copy `mealie_mcp.py` somewhere stable (e.g. `~/.hermes/profiles/voice/scripts/`) and add to the
   profile's `config.yaml`:
   ```yaml
   mcp_servers:
     mealie:
       command: /path/to/hermes-agent/venv/bin/python   # any Python with the `mcp` package
       args: [/path/to/mealie_mcp.py]
       env:
         MEALIE_URL: http://127.0.0.1:9925
         MEALIE_API_TOKEN: ${MEALIE_API_TOKEN}
   tools:
     tool_search:
       enabled: "off"   # otherwise Hermes adds a lookup round trip (~2 s) before each MCP tool
   ```
4. `hermes -p voice gateway restart`, then `hermes -p voice mcp test mealie` should list six tools.

## On the Hermes screen

Add Home Assistant's own **Mealie** integration (Settings → Devices & services → Add integration → Mealie,
the same address and a token). Then recipes Hermes reads open on the Echo Show's Hermes screen, with a
step-by-step cooking view; see [docs/show-screen.md](../../docs/show-screen.md#recipes-mealie). The screen
spots this server's tools by name (`mealie` in the server name, tools `get_recipe` and `search_recipes`).

Tested with Mealie v3.30.0 and 192 recipes imported from Paprika.
