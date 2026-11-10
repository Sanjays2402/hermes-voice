"""Recipes on the screen (recipes.py): Mealie through Home Assistant's Mealie integration, cooking commands."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.hermes_voice import recipes
from custom_components.hermes_voice.const import CONF_STREAM

from .test_screen import AGENT, CHAT, _echo, _events, _setup
from .test_streaming import SSE, chunk, stream, tool

RID = "0f6c6c1e-2b55-4d0f-9a43-6f8f1c2a9b10"
MEALIE_HOST = "http://mealie.local:9000"


def raw_recipe() -> dict[str, Any]:
    """A recipe as Home Assistant's mealie.get_recipe returns it (aiomealie field names), Paprika-style."""
    return {
        "recipe_id": RID,
        "name": "Greek Chicken Pitas",
        "slug": "greek-chicken-pitas",
        "description": "Pitas with a cucumber salad.",
        "image": "abc1",
        "prep_time": None,
        "perform_time": "20-30mins",
        "total_time": None,
        "recipe_yield": "",
        "recipe_servings": 2.0,
        "original_url": "https://example.com/pitas",
        "ingredients": [
            {"display": "For The Chicken:", "note": "", "title": None},
            {"display": "2 chicken thighs", "note": "", "title": None},
            {"display": "1 cucumber", "note": "", "title": None},
            {"display": "", "note": "", "title": None},
        ],
        "instructions": [
            {"text": "1 Prepare the ingredients:", "title": None},
            {"text": "Boil a pot of salted water. Wash the produce.", "title": None},
            {"text": "2 Cook the pasta:", "title": None},
            {"text": "Cook the pasta 7 to 8 minutes, until tender.", "title": None},
        ],
    }


def test_normalize_folds_headings() -> None:
    r = recipes.normalize(raw_recipe())
    assert r["name"] == "Greek Chicken Pitas" and r["slug"] == "greek-chicken-pitas"
    assert r["image"] == f"/hermes_voice/recipe_image/{RID}"
    assert r["times"] == {"cook": "20-30mins"}  # Mealie's performTime is the cook time of imported recipes
    assert r["servings"] == "2 servings"
    assert r["ingredients"] == [
        {"text": "2 chicken thighs", "section": "For The Chicken"},
        {"text": "1 cucumber", "section": "For The Chicken"},
    ]
    assert [(s["section"], s["seconds"]) for s in r["steps"]] == [
        ("Prepare the ingredients", None),
        ("Cook the pasta", 420),
    ]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("Cook 7 to 8 minutes.", 420),
        ("Bake for 1 hour.", 3600),
        ("Warm 30 seconds to 1 minute.", 30),
        ("Simmer for a minute.", 60),
        ("Stir well.", None),
    ],
)
def test_step_seconds(text: str, seconds: int | None) -> None:
    assert recipes.step_seconds(text) == seconds


@pytest.mark.parametrize(
    ("said", "command"),
    [
        ("Next step.", ("next", None)),
        ("OK, next", ("next", None)),
        ("What's next?", ("next", None)),
        ("Go back", ("previous", None)),
        ("previous step please", ("previous", None)),
        ("Repeat that.", ("repeat", None)),
        ("Go to step 3", ("goto", 3)),
        ("step four", ("goto", 4)),
        ("the second step", ("goto", 2)),
        ("Let's start cooking", ("start", None)),
        ("What do I need?", ("ingredients", None)),
        ("Close the recipe.", ("close", None)),
        ("I'm done cooking", ("close", None)),
        ("How much garlic goes in?", None),
        ("What's the weather?", None),
        ("next week's forecast", None),
    ],
)
def test_parse_command(said: str, command: tuple[str, int | None] | None) -> None:
    assert recipes.parse_command(said) == command


def test_apply_command() -> None:
    cook = recipes.Cooking(recipe=recipes.normalize(raw_recipe()))
    said, closed = recipes.apply_command(cook, "next", None)  # from the card, "next" starts at step 1
    assert (cook.view, cook.step, closed) == ("steps", 0, False)
    assert said.startswith("Step 1 of 2. Prepare the ingredients. Boil")
    said, _ = recipes.apply_command(cook, "next", None)
    assert cook.step == 1 and said.startswith("Step 2 of 2. Cook the pasta.")
    said, _ = recipes.apply_command(cook, "next", None)
    assert cook.step == 1 and said == "That was the last step. Enjoy!"
    said, _ = recipes.apply_command(cook, "goto", 9)
    assert said == "This recipe has 2 steps." and cook.step == 1
    said, _ = recipes.apply_command(cook, "ingredients", None)
    assert cook.view == "ingredients" and said == "You need: 2 chicken thighs; 1 cucumber."
    assert "step 2 of 2" not in cook.context()
    cook.view = "steps"
    assert "step 2 of 2: Cook the pasta" in cook.context()
    _, closed = recipes.apply_command(cook, "close", None)
    assert closed


def _mealie(hass: HomeAssistant) -> list[tuple[str, dict[str, Any]]]:
    """A loaded Mealie entry and fake mealie.* services that answer like Home Assistant's integration."""
    entry = MockConfigEntry(domain="mealie", data={"host": MEALIE_HOST}, state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    calls: list[tuple[str, dict[str, Any]]] = []
    base = {k: raw_recipe()[k] for k in ("recipe_id", "name", "slug", "image", "perform_time")}

    async def get_recipe(call: ServiceCall) -> dict[str, Any]:
        calls.append(("get_recipe", dict(call.data)))
        if call.data["recipe_id"] != "greek-chicken-pitas":
            raise intent.IntentHandleError("Recipe not found")  # any HomeAssistantError
        return {"recipe": raw_recipe()}

    async def get_recipes(call: ServiceCall) -> dict[str, Any]:
        calls.append(("get_recipes", dict(call.data)))
        return {"recipes": {"items": [base]}}

    async def get_mealplan(call: ServiceCall) -> dict[str, Any]:
        calls.append(("get_mealplan", dict(call.data)))
        today = dt_util.now().date()
        return {
            "mealplan": [
                {
                    "mealplan_date": today + timedelta(days=1),
                    "entry_type": "dinner",
                    "title": None,
                    "recipe": base,
                },
                {"mealplan_date": today, "entry_type": "dinner", "title": "Leftovers", "recipe": None},
            ]
        }

    for name, fn in (
        ("get_recipe", get_recipe),
        ("get_recipes", get_recipes),
        ("get_mealplan", get_mealplan),
    ):
        hass.services.async_register("mealie", name, fn, supports_response=SupportsResponse.ONLY)
    return calls


async def _subscribe(hass_ws_client: WebSocketGenerator, hass: HomeAssistant, device_id: str) -> Any:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    assert (await ws.receive_json())["success"]
    snapshot = (await ws.receive_json())["event"]
    await ws.receive_json()  # timers
    return ws, snapshot


async def test_hermes_opens_recipe_and_cooking_commands(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Hermes reads a recipe: it opens on the screen. "Next step" is answered here; other questions go to
    Hermes with what the screen shows."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    calls = _mealie(hass)
    aioclient_mock.post(
        CHAT,
        text=stream(
            tool("c1", "running", name="mcp__mealie__get_recipe", label="Greek pitas"),
            tool("c1", "completed", name="mcp__mealie__get_recipe", label="Greek pitas"),
            chunk("It takes about half an hour."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    ws, snapshot = await _subscribe(hass_ws_client, hass, device_id)
    assert snapshot["cook"] == {}
    await conversation.async_converse(
        hass, "How long do the pitas take?", "c-1", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    await _events(ws, 5)  # heard, tool_start, tool_end, delta, reply_done
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    cook = (await ws.receive_json())["event"]
    assert cook["type"] == "cook" and cook["cook"]["view"] == "card"
    assert cook["cook"]["recipe"]["slug"] == "greek-chicken-pitas"
    # Not a slug: Home Assistant searched by name, then fetched the recipe.
    assert [c[0] for c in calls] == ["get_recipe", "get_recipes", "get_recipe"]

    posts = len([c for c in aioclient_mock.mock_calls if c[0] == "POST"])
    result = await conversation.async_converse(
        hass, "Next step.", "c-2", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    assert result.response.speech["plain"]["speech"].startswith("Step 1 of 2. Prepare the ingredients.")
    assert len([c for c in aioclient_mock.mock_calls if c[0] == "POST"]) == posts  # Hermes was not asked
    events = await _events(ws, 2)
    assert (
        events[0]["type"] == "cook"
        and events[0]["cook"]["view"] == "steps"
        and events[0]["cook"]["step"] == 0
    )
    assert events[1]["type"] == "local_answer" and events[1]["cook"] is True

    await conversation.async_converse(
        hass, "How much salt?", "c-3", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    body = aioclient_mock.mock_calls[-1][2]
    said = body["messages"][-1]["content"]
    assert 'screen shows the recipe "Greek Chicken Pitas"' in said and "step 1 of 2" in said
    assert said.endswith("\n\nHow much salt?")

    await conversation.async_converse(
        hass, "close the recipe", "c-4", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    await conversation.async_converse(
        hass, "next step", "c-5", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    # Nothing open any more: "next step" went to Hermes.
    assert aioclient_mock.mock_calls[-1][2]["messages"][-1]["content"].endswith("]\n\nnext step")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4))  # Hermes' reply opened it again
    await hass.async_block_till_done()


async def test_search_shows_results(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await _setup(hass, aioclient_mock, {CONF_STREAM: True})
    device_id, _ = _echo(hass, entry)
    _mealie(hass)
    aioclient_mock.post(
        CHAT,
        text=stream(
            tool("c1", "running", name="mcp__mealie__search_recipes", label="chicken"),
            chunk("You have pitas."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    ws, _ = await _subscribe(hass_ws_client, hass, device_id)
    await conversation.async_converse(
        hass, "Any chicken recipes?", None, Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    await hass.async_block_till_done()
    events = await _events(ws, 5)
    found = next(e for e in events if e["type"] == "recipes")
    assert found["title"] == "chicken" and found["items"][0]["name"] == "Greek Chicken Pitas"


async def test_screen_commands(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The screen browses, opens, steps through, ticks off and closes; starts a step's timer; shows photos."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/recipes"})
    assert (await ws.receive_json())["result"] == {"available": False, "plan": [], "items": []}

    calls = _mealie(hass)
    await ws.send_json({"id": 2, "type": "hermes_voice/screen/recipes"})
    res = (await ws.receive_json())["result"]
    assert [p["title"] or p["recipe"]["name"] for p in res["plan"]] == ["Leftovers", "Greek Chicken Pitas"]
    assert res["items"][0]["slug"] == "greek-chicken-pitas"
    today = dt_util.now().date()  # Home Assistant's time zone, not the machine's
    plan_call = next(c[1] for c in calls if c[0] == "get_mealplan")
    assert (plan_call["start_date"], plan_call["end_date"]) == (
        today.isoformat(),
        (today + timedelta(days=6)).isoformat(),
    )

    async def cook(n: int, **msg: Any) -> dict[str, Any]:
        await ws.send_json({"id": n, "type": "hermes_voice/screen/cook", "device_id": device_id, **msg})
        return await ws.receive_json()

    assert (await cook(3, action="step", step=1))["error"]["code"] == "not_open"
    assert (await cook(4, action="open", recipe="no-such-thing-at-all"))["result"]["recipe"]["name"]
    opened = await cook(5, action="open", recipe="greek-chicken-pitas")
    assert opened["result"]["view"] == "card"
    assert (await cook(6, action="step", step=9))["result"]["step"] == 1  # clamped to the last step
    assert (await cook(7, action="check", index=0, checked=True))["result"]["checked"] == [0]
    assert (await cook(8, action="view", view="ingredients"))["result"]["view"] == "ingredients"
    assert (await cook(9, action="close"))["success"]
    assert async_get_cook(hass, device_id) is None

    # A step's timer: a voice timer on the device, through Home Assistant's timer intent.
    started: list[dict[str, Any]] = []

    class StartTimer(intent.IntentHandler):
        intent_type = intent.INTENT_START_TIMER

        async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
            started.append(
                {"device_id": intent_obj.device_id, **{k: v["value"] for k, v in intent_obj.slots.items()}}
            )
            return intent_obj.create_response()

    intent.async_register(hass, StartTimer())
    await ws.send_json(
        {
            "id": 10,
            "type": "hermes_voice/screen/start_timer",
            "device_id": device_id,
            "seconds": 420,
            "name": "Pasta",
        }
    )
    assert (await ws.receive_json())["success"]
    assert started == [{"device_id": device_id, "seconds": 420, "name": "Pasta"}]

    # Photos come through Home Assistant, by recipe id only.
    aioclient_mock.get(f"{MEALIE_HOST}/api/media/recipes/{RID}/images/min-original.webp", content=b"RIFFwebp")
    client = await hass_client_no_auth()
    resp = await client.get(f"/hermes_voice/recipe_image/{RID}")
    assert resp.status == 200 and await resp.read() == b"RIFFwebp"
    assert (await client.get("/hermes_voice/recipe_image/not-a-recipe-id")).status == 404


def async_get_cook(hass: HomeAssistant, device_id: str) -> Any:
    from custom_components.hermes_voice.screen import async_get_hub

    hub = async_get_hub(hass)
    assert hub is not None
    return hub.get_cook(device_id)
