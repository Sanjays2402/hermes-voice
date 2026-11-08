#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2"]
# ///
"""Mealie for a Hermes voice profile: a small MCP server (stdio).

Six tools, kept few and short so they cost little prompt: search recipes, read one recipe,
the meal plan, add to the meal plan, the shopping list, add to the shopping list. Nothing
here deletes or edits recipes, so a misheard sentence can't lose one.

Environment:
  MEALIE_URL        e.g. http://127.0.0.1:9925
  MEALIE_API_TOKEN  a Mealie API token (Mealie: your profile -> API Tokens)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

URL = os.environ.get("MEALIE_URL", "http://127.0.0.1:9925").rstrip("/")
TOKEN = os.environ.get("MEALIE_API_TOKEN", "")
MEAL_TYPES = ("breakfast", "lunch", "dinner", "side", "snack", "dessert", "drink")

mcp = FastMCP("mealie")


class MealieError(Exception):
    pass


def api(method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
    if not TOKEN:
        raise MealieError("MEALIE_API_TOKEN is not set")
    query = "?" + urllib.parse.urlencode(params, doseq=True) if params else ""
    req = urllib.request.Request(
        URL + path + query,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise MealieError("Mealie rejected the API token") from err
        if err.code == 404:
            raise MealieError("not found in Mealie") from err
        raise MealieError(f"Mealie answered {err.code}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise MealieError("Mealie is not reachable") from err
    return json.loads(raw) if raw else None


def minutes(text: str | None) -> str:
    return (text or "").strip()


def times(r: dict[str, Any]) -> list[str]:
    """Prep, cook and total time as saved. Mealie keeps the cook time of imported recipes
    (Paprika's cook_time, most scraped sites) in performTime; cookTime is rarely set."""
    cook = minutes(r.get("cookTime")) or minutes(r.get("performTime"))
    return [
        f"{label} {value}"
        for label, value in (("prep", minutes(r.get("prepTime"))), ("cook", cook), ("total", minutes(r.get("totalTime"))))
        if value
    ]


def summary(r: dict[str, Any]) -> str:
    parts = [r.get("name") or r.get("slug", "?"), f"[{r.get('slug')}]"]
    if t := times(r):
        parts.append(", ".join(t))
    if r.get("rating"):
        parts.append(f"rated {r['rating']}/5")
    desc = re.sub(r"\s+", " ", r.get("description") or "").strip()
    if desc:
        parts.append("- " + (desc[:120] + "…" if len(desc) > 120 else desc))
    return " ".join(parts)


def find_slug(name_or_slug: str) -> str:
    """A recipe's slug from its slug or (part of) its name."""
    guess = re.sub(r"[^a-z0-9]+", "-", name_or_slug.lower()).strip("-")
    try:
        api("GET", f"/api/recipes/{guess}")
        return guess
    except MealieError:
        pass
    hits = api("GET", "/api/recipes", {"search": name_or_slug, "perPage": 1}).get("items") or []
    if not hits:
        raise MealieError(f'no recipe matches "{name_or_slug}"')
    return hits[0]["slug"]


def run(fn, *args, **kwargs) -> str:
    try:
        return fn(*args, **kwargs)
    except MealieError as err:
        return f"Error: {err}"


@mcp.tool()
def search_recipes(query: str = "", limit: int = 5) -> str:
    """Search the user's own recipe collection (Mealie) by words in the name, description or
    ingredients. Empty query: the most recently added. Returns name, [slug], times. The matches
    also show on the user's screen."""

    def go() -> str:
        params: dict[str, Any] = {"perPage": max(1, min(limit, 15)), "orderBy": "created_at", "orderDirection": "desc"}
        if query.strip():
            params = {"search": query.strip(), "perPage": params["perPage"]}
        data = api("GET", "/api/recipes", params)
        items = data.get("items") or []
        if not items:
            return f'No recipes match "{query}".' if query else "The recipe collection is empty."
        head = f"{data.get('total', len(items))} match(es):" if query else "Newest recipes:"
        return "\n".join([head, *("- " + summary(r) for r in items)])

    return run(go)


@mcp.tool()
def get_recipe(name: str) -> str:
    """One recipe from the user's collection, by name or [slug]: servings, times, ingredients and
    numbered steps. Also opens it on the user's screen, so use it for "show me ..." too."""

    def go() -> str:
        r = api("GET", f"/api/recipes/{find_slug(name)}")
        out = [f"{r.get('name')} [{r.get('slug')}]"]
        facts = times(r)
        yld = r.get("recipeYield") or (f"{r['recipeServings']:g} servings" if r.get("recipeServings") else "")
        if yld:
            facts.insert(0, str(yld))
        if facts:
            out.append(", ".join(facts))
        out.append("Ingredients:")
        for ing in r.get("recipeIngredient") or []:
            if ing.get("title"):
                out.append(f"  ({ing['title']})")
            text = ing.get("display") or ing.get("originalText") or ing.get("note") or ""
            if text.strip():
                out.append("- " + text.strip())
        out.append("Steps:")
        for i, step in enumerate(r.get("recipeInstructions") or [], 1):
            title = f"{step['title']}: " if step.get("title") else ""
            text = re.sub(r"\s+", " ", step.get("text") or "").strip()
            out.append(f"{i}. {title}{text}")
        notes = [n.get("text", "").strip() for n in r.get("notes") or [] if n.get("text")]
        if notes:
            out.append("Notes: " + " / ".join(notes))
        return "\n".join(out)

    return run(go)


@mcp.tool()
def meal_plan(start: str = "", days: int = 7) -> str:
    """The user's meal plan from `start` (YYYY-MM-DD, default today) for `days` days."""

    def go() -> str:
        first = date.fromisoformat(start) if start else date.today()
        last = first + timedelta(days=max(1, min(days, 31)) - 1)
        data = api(
            "GET",
            "/api/households/mealplans",
            {"start_date": first.isoformat(), "end_date": last.isoformat(), "perPage": 100, "orderBy": "date"},
        )
        items = sorted(data.get("items") or [], key=lambda e: (e["date"], MEAL_TYPES.index(e.get("entryType", "dinner")) if e.get("entryType") in MEAL_TYPES else 9))
        if not items:
            return f"Nothing planned from {first} to {last}."
        lines = []
        for e in items:
            what = (e.get("recipe") or {}).get("name") or e.get("title") or e.get("text") or "?"
            slug = (e.get("recipe") or {}).get("slug")
            day = date.fromisoformat(e["date"]).strftime("%a %b %-d")
            lines.append(f"- {day}, {e.get('entryType', 'dinner')}: {what}" + (f" [{slug}]" if slug else ""))
        return "\n".join(lines)

    return run(go)


@mcp.tool()
def add_to_meal_plan(day: str, recipe: str = "", note: str = "", meal: str = "dinner") -> str:
    """Put a recipe (name or [slug]) or a free-text note (e.g. "leftovers", "eat out") on the meal
    plan. day: YYYY-MM-DD. meal: breakfast, lunch, dinner, side, snack, dessert or drink."""

    def go() -> str:
        when = date.fromisoformat(day)
        kind = meal if meal in MEAL_TYPES else "dinner"
        body: dict[str, Any] = {"date": when.isoformat(), "entryType": kind}
        if recipe.strip():
            r = api("GET", f"/api/recipes/{find_slug(recipe)}")
            body["recipeId"] = r["id"]
            label = r["name"]
        elif note.strip():
            body["title"] = label = note.strip()
        else:
            return "Error: give a recipe or a note."
        api("POST", "/api/households/mealplans", body=body)
        return f"Planned {label} for {kind} on {when.strftime('%A %B %-d')}."

    return run(go)


def default_list() -> dict[str, Any]:
    lists = api("GET", "/api/households/shopping/lists", {"perPage": 50, "orderBy": "created_at"}).get("items") or []
    if not lists:
        return api("POST", "/api/households/shopping/lists", body={"name": "Shopping"})
    return lists[0]


@mcp.tool()
def shopping_list() -> str:
    """What is still on the user's shopping list (not yet checked off)."""

    def go() -> str:
        lst = api("GET", f"/api/households/shopping/lists/{default_list()['id']}")
        items = [i for i in lst.get("listItems") or [] if not i.get("checked")]
        if not items:
            return f"The {lst.get('name', 'shopping')} list is empty."
        names = [(i.get("display") or i.get("note") or "").strip() for i in items]
        return f"{lst.get('name')}, {len(items)} item(s):\n" + "\n".join("- " + n for n in names if n)

    return run(go)


@mcp.tool()
def add_to_shopping_list(items: list[str]) -> str:
    """Add items to the user's shopping list, one string each, e.g. ["2 lemons", "eggs"]."""

    def go() -> str:
        lst = default_list()
        clean = [i.strip() for i in items if i and i.strip()][:30]
        if not clean:
            return "Error: nothing to add."
        api(
            "POST",
            "/api/households/shopping/items/create-bulk",
            body=[{"shoppingListId": lst["id"], "note": i, "quantity": 0, "isFood": False} for i in clean],  # quantity 0: no "1 " shown before the text
        )
        return f"Added to {lst.get('name', 'the shopping list')}: " + ", ".join(clean) + "."

    return run(go)


if __name__ == "__main__":
    mcp.run()
