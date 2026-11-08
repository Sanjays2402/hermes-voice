"""Recipes on the Hermes screen, from Mealie through Home Assistant's own Mealie integration.

Hermes reads recipes with a Mealie MCP server (extras/mealie). When it does, the screen of the device
that asked shows the recipe; when it searches, the matches. Home Assistant fetches what the screen shows
with the Mealie integration's services (mealie.get_recipe, get_recipes, get_mealplan), so the token stays
in that integration and the screen needs nothing new.

While a recipe is open on a device, short cooking commands said to that device ("next step", "back",
"step 4", "ingredients", "close the recipe") are answered here at once, without Hermes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re
import time
from typing import Any

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

MEALIE = "mealie"
IMAGE_URL = "/hermes_voice/recipe_image"
COOK_IDLE = 4 * 3600  # a recipe left open this long without a command is closed
MAX_STEPS = 60
MAX_INGREDIENTS = 80


class RecipeError(Exception):
    """No Mealie integration, or the recipe was not found."""


def mealie_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Home Assistant's Mealie integration, if it is set up and loaded."""
    return next(
        (e for e in hass.config_entries.async_entries(MEALIE) if e.state is ConfigEntryState.LOADED), None
    )


def _value(v: Any) -> Any:
    """Enums (entry types) as their value."""
    return getattr(v, "value", v)


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


# ----------------------------------------------------------------------------------- normalizing --

# "1 Prepare the ingredients:", "For The Sauce:": a heading line that Paprika and many sites store as a step
# (or an ingredient). It becomes the title of the steps (ingredients) that follow.
_HEADING = re.compile(r"^(?:\d{1,2}[.)]?\s+)?(?P<title>[^.!?]{2,70}):$")
_DURATION = re.compile(
    r"(?P<a>\d+(?:[.,]\d+)?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty)"
    r"(?:\s*(?:to|-|\u2013|or)\s*(?P<b>\d+))?\s*(?P<unit>hours?|hrs?|minutes?|mins?|seconds?|secs?)\b",
    re.IGNORECASE,
)
_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
}  # fmt: skip


def step_seconds(text: str) -> int | None:
    """The first time a step mentions, for a timer button: "Cook 7 to 8 minutes" -> 7 minutes."""
    m = _DURATION.search(text)
    if not m:
        return None
    raw = m.group("a").lower().replace(",", ".")
    n = float(_NUMBERS.get(raw, raw)) if raw in _NUMBERS or raw.replace(".", "", 1).isdigit() else 0
    unit = m.group("unit").lower()
    seconds = n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return int(seconds) if 0 < seconds <= 24 * 3600 else None


def _image(recipe_id: Any, image: Any) -> str | None:
    rid = str(recipe_id or "")
    return f"{IMAGE_URL}/{rid}" if image and _RECIPE_ID.match(rid) else None


def _times(r: dict[str, Any]) -> dict[str, str]:
    out = {
        "prep": _text(r.get("prep_time")),
        "cook": _text(r.get("perform_time")),  # Mealie's "cook time" for imported recipes
        "total": _text(r.get("total_time")),
    }
    return {k: v for k, v in out.items() if v}


def summary(r: dict[str, Any]) -> dict[str, Any]:
    """A recipe for a list: name, slug, photo, times."""
    return {
        "slug": r.get("slug") or "",
        "name": _text(r.get("name")),
        "image": _image(r.get("recipe_id"), r.get("image")),
        "times": _times(r),
    }


def normalize(r: dict[str, Any]) -> dict[str, Any]:
    """A recipe as the screen shows it: headings folded into the steps and ingredients that follow."""
    ingredients: list[dict[str, str]] = []
    section = ""
    for ing in (r.get("ingredients") or [])[:MAX_INGREDIENTS]:
        text = _text(ing.get("display") or ing.get("original_text") or ing.get("note"))
        if ing.get("title"):
            section = _text(ing["title"])
        if not text:
            continue
        if (h := _HEADING.match(text)) and not text[:1].isdigit():
            section = h.group("title").strip()
            continue
        ingredients.append({"text": text, "section": section})
    steps: list[dict[str, Any]] = []
    section = ""
    for st in r.get("instructions") or []:
        text = _text(st.get("text"))
        if st.get("title"):
            section = _text(st["title"])
        if not text:
            continue
        if h := _HEADING.match(text):
            section = h.group("title").strip()
            continue
        steps.append({"text": text, "section": section, "seconds": step_seconds(text)})
    return {
        **summary(r),
        "id": str(r.get("recipe_id") or ""),
        "description": _text(r.get("description"))[:400],
        "servings": _text(r.get("recipe_yield"))
        or (f"{r['recipe_servings']:g} servings" if r.get("recipe_servings") else ""),
        "ingredients": ingredients,
        "steps": steps[:MAX_STEPS],
        "source": _text(r.get("original_url")),
    }


# -------------------------------------------------------------------------------------- fetching --


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any]) -> dict[str, Any]:
    entry = mealie_entry(hass)
    if entry is None:
        raise RecipeError("Home Assistant's Mealie integration is not set up")
    try:
        result = await hass.services.async_call(
            MEALIE, service, {"config_entry_id": entry.entry_id, **data}, blocking=True, return_response=True
        )
    except HomeAssistantError as err:
        raise RecipeError(str(err) or type(err).__name__) from err
    return dict(result or {})


async def async_get_recipe(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """A recipe by its slug or (part of) its name, normalized."""
    ref = ref.strip().strip("[]").rstrip("…").rstrip(".").strip()
    if not ref:
        raise RecipeError("no recipe given")
    slug = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")
    try:
        found = await _call(hass, "get_recipe", {"recipe_id": slug})
    except RecipeError:
        hits = await async_search(hass, ref, 1)
        if not hits:
            raise RecipeError(f'no recipe matches "{ref}"') from None
        found = await _call(hass, "get_recipe", {"recipe_id": hits[0]["slug"]})
    return normalize(found.get("recipe") or {})


async def async_search(hass: HomeAssistant, query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Recipes matching words, newest first without words."""
    data: dict[str, Any] = {"result_limit": max(1, min(limit, 40))}
    if query.strip():
        data["search_terms"] = query.strip()
    found = await _call(hass, "get_recipes", data)
    items = (found.get("recipes") or {}).get("items") or []
    return [summary(r) for r in items if r.get("slug")]


async def async_meal_plan(hass: HomeAssistant, today: date, days: int = 7) -> list[dict[str, Any]]:
    """The meal plan from today: date, meal, and the recipe (or a note)."""
    found = await _call(
        hass,
        "get_mealplan",
        {"start_date": today.isoformat(), "end_date": (today + timedelta(days=days - 1)).isoformat()},
    )
    out = []
    for e in found.get("mealplan") or []:
        recipe = e.get("recipe")
        when = e.get("mealplan_date")
        out.append(
            {
                "date": when.isoformat() if isinstance(when, date) else str(when or ""),
                "meal": str(_value(e.get("entry_type")) or "dinner"),
                "title": _text(e.get("title")),
                "recipe": summary(recipe) if isinstance(recipe, dict) else None,
            }
        )
    order = ("breakfast", "lunch", "dinner", "side", "snack", "dessert", "drink")
    out.sort(key=lambda x: (x["date"], order.index(x["meal"]) if x["meal"] in order else 9))
    return out


# Mealie's recipe photos, passed through Home Assistant so a screen needs no address for Mealie (which may be
# a Docker name only Home Assistant can reach) and no Mealie login. Only recipe photos, by recipe id.
_RECIPE_ID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")


class RecipeImageView(HomeAssistantView):
    """GET /hermes_voice/recipe_image/<recipe id>: the recipe's photo from Mealie."""

    url = IMAGE_URL + "/{recipe_id}"
    name = "hermes_voice:recipe_image"
    requires_auth = False  # an <img> on the screen cannot send a token; recipe photos only

    async def get(self, request: web.Request, recipe_id: str) -> web.Response:
        hass = request.app[KEY_HASS]
        entry = mealie_entry(hass)
        if entry is None or not _RECIPE_ID.match(recipe_id):
            return web.Response(status=404)
        host = str(entry.data.get(CONF_HOST, "")).rstrip("/")
        url = f"{host}/api/media/recipes/{recipe_id}/images/min-original.webp"
        session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return web.Response(status=404)
                body = await resp.read()
        except (TimeoutError, OSError):
            return web.Response(status=502)
        return web.Response(
            body=body, content_type="image/webp", headers={"Cache-Control": "public, max-age=86400"}
        )


# --------------------------------------------------------------------------------------- cooking --


@dataclass(slots=True)
class Cooking:
    """The recipe open on one device's screen."""

    recipe: dict[str, Any]
    view: str = "card"  # card, ingredients, steps
    step: int = 0
    checked: set[int] = field(default_factory=set)  # ingredients ticked off
    at: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict[str, Any]:
        return {"recipe": self.recipe, "view": self.view, "step": self.step, "checked": sorted(self.checked)}

    @property
    def stale(self) -> bool:
        return time.monotonic() - self.at > COOK_IDLE

    def context(self) -> str:
        """One line for Hermes: what the screen shows, so "how much garlic here?" has an answer."""
        name = self.recipe.get("name", "")
        steps = self.recipe.get("steps") or []
        if self.view == "steps" and steps:
            st = steps[min(self.step, len(steps) - 1)]
            return (
                f'The user\'s screen shows the recipe "{name}" ([{self.recipe.get("slug")}]), '
                f"step {self.step + 1} of {len(steps)}: {st['text']}"
            )
        return f'The user\'s screen shows the recipe "{name}" ([{self.recipe.get("slug")}]).'


_FILLER = re.compile(r"^(?:(?:ok(?:ay)?|alright|all right|hey|hermes|please|and|so|now|um|uh)\s+)+")
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "first": 1,
    "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
    "tenth": 10,
}  # fmt: skip
_N = r"(\d{1,2}|" + "|".join(_WORD_NUMBERS) + ")"
COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("next", re.compile(r"^(?:go to the )?next(?: step| one)?|what(?:s| is) next|continue|keep going|go on|done next$")),
    ("previous", re.compile(r"^(?:go )?back(?: one| a step)?|previous(?: step)?|step back|(?:go back to the )?last step$")),
    ("repeat", re.compile(r"^repeat(?: that| the step| this step| step)?|say (?:that|it) again|what was that|read (?:the|this|that) step(?: again)?|read (?:it|that) again|again$")),
    ("goto", re.compile(rf"^(?:go to |show |read )?(?:the )?step {_N}|(?:go to |show |read )?(?:the )?{_N} step$")),
    ("final", re.compile(r"^(?:go to |show )?(?:the )?final step$")),
    ("start", re.compile(r"^(?:let'?s |lets )?(?:start|begin)(?: cooking| the recipe| from the (?:beginning|top))?|let'?s cook|lets cook|start over$")),
    ("ingredients", re.compile(r"^(?:show |read )?(?:me )?(?:the )?ingredients(?: list)?|what do i need$")),
    ("close", re.compile(r"^close(?: the)?(?: recipe| it)?|(?:i'?m|im|we'?re|were|all) done(?: cooking)?|done cooking|stop cooking|exit(?: the)? recipe|finished cooking$")),
)  # fmt: skip


def parse_command(text: str) -> tuple[str, int | None] | None:
    """A cooking command in what was said, or None for anything else (that goes to Hermes)."""
    t = re.sub(r"['\u2019]", "", text.lower())  # "what's" -> "whats": spoken text varies
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _FILLER.sub("", t)
    t = re.sub(r"\s+please$", "", t).strip()
    for action, pattern in COMMANDS:
        if m := pattern.fullmatch(t):
            if action != "goto":
                return action, None
            raw = next(g for g in m.groups() if g)
            return action, int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
    return None


def say_step(cook: Cooking, previous_section: str | None = None) -> str:
    steps = cook.recipe.get("steps") or []
    st = steps[cook.step]
    head = f"Step {cook.step + 1} of {len(steps)}."
    if st.get("section") and st["section"] != previous_section:
        head += f" {st['section'].rstrip('.')}."
    return f"{head} {st['text']}"


def apply_command(cook: Cooking, action: str, n: int | None) -> tuple[str, bool]:
    """Change what the screen shows. Returns what to say, and whether the recipe was closed."""
    steps = cook.recipe.get("steps") or []
    cook.at = time.monotonic()
    if action == "close":
        return f"Closed {cook.recipe.get('name') or 'the recipe'}.", True
    if action == "ingredients":
        cook.view = "ingredients"
        items = [i["text"] for i in cook.recipe.get("ingredients") or []]
        if not items:
            return "This recipe has no ingredient list.", False
        if len(items) <= 10:
            return "You need: " + "; ".join(items) + ".", False
        return f"{len(items)} ingredients. They're on the screen.", False
    if not steps:
        return "This recipe has no steps.", False
    before = steps[cook.step]["section"] if cook.view == "steps" and cook.step < len(steps) else None
    was_steps = cook.view == "steps"
    cook.view = "steps"
    if action == "start":
        cook.step = 0
        return say_step(cook), False
    if action == "repeat":
        cook.step = min(cook.step, len(steps) - 1)
        return say_step(cook), False
    if action == "next":
        if was_steps and cook.step >= len(steps) - 1:
            return "That was the last step. Enjoy!", False
        cook.step = cook.step + 1 if was_steps else cook.step
    elif action == "previous":
        if cook.step == 0:
            return "This is the first step. " + say_step(cook), False
        cook.step -= 1
        before = None
    elif action == "final":
        cook.step = len(steps) - 1
    elif action == "goto" and n is not None:
        if not 1 <= n <= len(steps):
            return f"This recipe has {len(steps)} steps.", False
        cook.step = n - 1
    return say_step(cook, before), False
