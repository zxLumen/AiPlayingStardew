"""The doctor: the one who asks *why*.

The system could already see that an action was not changing the world. What it could not do was
find out why - so its only move was to blacklist the action, which is not a repair, it is tying your
hands. That is how a full watering can's worth of trouble got the useful actions `use_tool_at` and
`enter` banned while the real fault - an empty can - went untouched.

The doctor turns a repeated failure into a cause and a prescription. Its method is deliberately
general, because the failures are not:

    1. Look at what failed, and at the objects involved (tool, seed, door, tile, place).
    2. Read the game's own data about them - it describes its own rules.
    3. Compare what the game says with what is actually true right now.
    4. State the cause, classify it, and write a prescription.

Classification is the part that decides who fixes it:

    cognitive   the game's data holds the answer; the agent should have read it
    capability  the game can do this and the bridge cannot - a human must write code
    bridge      the bridge is lying or misreporting; a human must fix it
"""

from __future__ import annotations

import json

import prompts

DOCTOR_SYSTEM = """You are the DOCTOR AI for an agent that plays Stardew Valley through a bridge.

Something has been failing repeatedly. Your only job is to find out WHY and say what to do about it.

You have three sources of truth, in this order:
1. THE GAME ITSELF. The bridge exposes the game's own data files by name (ask for them - see below).
   The game describes its own rules: what a tool needs, what an item does, what a shop's hours are,
   what a crop wants. If the answer is in there, it is not a mystery, it is a lookup.
2. THE CURRENT FACTS. The live state you were given: what is held, how much it holds, where the
   farmer is, what is on the ground, what the world counters say.
3. THE FAILURE EVIDENCE. What was sent, what came back, and what the world did (or did not) do.

Reply with strict JSON:

{"lookups": [{"what": "Data/Objects", "query": "喷壶"}],
 "cause": "one sentence: what is actually wrong",
 "class": "cognitive | capability | bridge",
 "prescription": {...},
 "check": "how to tell, from the world, that the fix worked"}

"lookups" is optional and may be empty: only ask for data you actually need. You will be given the
results and asked again, so keep it to what matters.

The prescription is ONE of:
  {"kind": "precondition", "action": "<action>",
   "requires": {"fact": "<dotted state path>", "op": ">|>=|<|<=|==|!=", "value": <number or text>},
   "satisfy_with": "<action to run first, or empty>", "why": "<why>"}
  {"kind": "plan_constraint", "text": "<a constraint the daily planner must respect>"}
  {"kind": "capability", "title": "<short>", "detail": "<what the game can do that the bridge cannot>"}
  {"kind": "ban", "action": "<action>", "why": "<why it can never work>"}
  {"kind": "declare", "action": "<action>", "class": "action|read|recovery|illegal",
   "how": "<what a player does to trigger it, or why it is recovery / illegal>"}

IMPORTANT - before prescribing anything, ask which of these an exposed action is:
  action    a player does it. If it can be done, this is usually the answer.
  read      looking at something; legal only while the matching screen is open.
  recovery  the harness uses it on the agent's behalf and the agent must never choose it as play.
            This includes anything that draws or narrates FOR THE AUDIENCE - a subtitle, a chat
            panel, the agents' board. Those are not gameplay verbs a model invented; they are part
            of the show, and the right prescription is "declare", never "ban".
  illegal   a player cannot do it at all.

Use "ban" only for something that is neither a player action nor a recovery tool. Banning a
recovery action takes a piece of the harness away from itself.

For a precondition, "fact" is a dotted path into the live state. These work:
  held.charges, held.name, held.slot, money, stamina, work.thirsty_crops, work.empty_soil,
  work.debris, work.tillable, game.time_of_day, game.day_of_week
Use a precondition whenever a fixable condition is missing - that is almost always the right answer
for "the action does nothing". Only use "ban" when no condition could ever make it work.

Choose honestly:
- "cognitive" when the game's data already says what to do and the agent simply did not look.
- "capability" when the game can do it and the bridge has no action for it (e.g. a missing verb).
- "bridge" when the bridge reports success but the world does not change, or reports the wrong thing.
Never prescribe "ban" for something a precondition would fix."""


def _brief(value, limit: int = 1400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


class Doctor:
    def __init__(self, llm, client, log=print, catalog=None, actions=None) -> None:
        self.llm = llm
        self.client = client
        self.log = log
        self.catalog = catalog or []
        self.actions = actions or []

    # -- data lookups ------------------------------------------------------

    def _lookup(self, request: dict) -> dict:
        what = str(request.get("what") or "").strip()
        if not what:
            return {"error": "no asset named"}

        args = {"what": what}
        for key in ("query", "key"):
            if request.get(key):
                args[key] = request[key]
        args["limit"] = 12

        result = self.client.action("game_data", **args)
        if not result.get("ok"):
            return {"what": what, "error": str(result.get("detail"))[:200]}

        return {"what": result.get("data", {}).get("asset", what),
                "entries": (result.get("data") or {}).get("entries")}

    # -- diagnosing --------------------------------------------------------

    def diagnose(self, defect, state: dict, context: dict, rounds: int = 2) -> dict:
        """Turn a defect into a cause and a prescription."""
        evidence = {
            "defect": {
                "key": getattr(defect, "key", ""),
                "title": getattr(defect, "title", ""),
                "detail": getattr(defect, "detail", ""),
                "count": getattr(defect, "count", 0),
                "evidence": getattr(defect, "evidence", []),
            },
            "what happened": context,
            "current facts": self._facts(state),
            "actions that exist (use these exact names)": list(self.actions),
            "game data you may ask for": self.catalog[:200],
        }

        verdict: dict = {}
        for round_index in range(rounds):
            messages = [
                {"role": "system", "content": DOCTOR_SYSTEM + prompts.LANGUAGE},
                {"role": "user", "content": _brief(evidence, 6000)},
            ]

            try:
                verdict, _ = self.llm.chat_json(messages, max_tokens=1100)
            except Exception as exc:
                return {"error": f"doctor call failed: {exc}"}

            if not isinstance(verdict, dict):
                return {"error": "doctor did not return an object"}

            wanted = verdict.get("lookups") or []
            if round_index >= rounds - 1 or not isinstance(wanted, list) or not wanted:
                break

            results = [self._lookup(item) for item in wanted[:4] if isinstance(item, dict)]
            self.log(f"[医生] 查了 {len(results)} 处游戏数据，继续下结论")

            if verdict.get("cause") and verdict.get("class") not in (None, "", "unknown"):
                # It already has an answer; the lookups were confirmatory.
                break

            evidence["results from the game's own data"] = results

        return verdict

    def _facts(self, state: dict) -> dict:
        """The live facts the doctor compares against what the game says."""
        player = state.get("player") or {}
        work = (state.get("location") or {}).get("work") or {}

        return {
            "location": player.get("current_location"),
            "money": player.get("money"),
            "stamina": f"{player.get('stamina')}/{player.get('max_stamina')}",
            "held": player.get("current_item"),
            "inventory": [
                {k: slot.get(k) for k in ("slot", "name", "stack", "charges", "charges_max", "kind")}
                for slot in (state.get("inventory") or [])
            ],
            "work": work,
            "uistate": {
                "menu": (state.get("ui") or {}).get("menu_name"),
                "dialogue": ((state.get("ui") or {}).get("dialogue") or {}).get("text"),
            },
            "chore_last": state.get("chore_last"),
        }
