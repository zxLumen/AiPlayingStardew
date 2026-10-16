"""A banned primitive must not compile.

"Everything goes through the game's own path" is a RULE, not a chore. Writing it in a ledger and
looking for violations by hand turns it into a chore - which is how `Game1.warpFarmer` sat inside
EnterBed for months while `warp` was already declared illegal in the action table, and how planting
charged nothing for a whole field of parsnips.

Rules that are checked by a person are suggestions. This one is checked by the compiler: it runs
before every build and fails the build, so a cheat cannot be merged into the bridge at all - not by
accident, not by a helper, not by a fallback.

One list, not two: the patterns come from `authenticity.py`, so the thing the build forbids and the
thing the runtime audit reports can never drift apart.

Run by the build (see StateBridge.csproj). Also runnable by hand:
    .venv\\Scripts\\python.exe tools\\check_fidelity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import authenticity as A  # noqa: E402

MOD_DIR = ROOT / "mod"


def main() -> int:
    if not MOD_DIR.exists():
        print(f"no mod directory at {MOD_DIR}", file=sys.stderr)
        return 1

    violations: list[str] = []

    for path in sorted(MOD_DIR.rglob("*.cs")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"could not read {path}: {exc}", file=sys.stderr)
            return 1

        rel = path.relative_to(ROOT)

        for primitive, why in A.CHEAT_PRIMITIVES.items():
            if primitive in source:
                violations.append(f"{rel}: {primitive} - {why}")

        # State-writing shapes: the action gets somewhere a player could not.
        for primitive, why in A.FIDELITY_PRIMITIVES.items():
            if primitive in source:
                violations.append(f"{rel}: {primitive} - {why}")

        # Primitives that are only legitimate when their cost is paid in the same file.
        for primitive, (costs, why) in A.FIDELITY_WITH_COST.items():
            if primitive in source and not any(cost in source for cost in costs):
                violations.append(f"{rel}: {primitive} - {why}")

    if not violations:
        print(f"fidelity: clean ({len(list(MOD_DIR.rglob('*.cs')))} files)")
        return 0

    print("fidelity: BANNED PRIMITIVES FOUND\n", file=sys.stderr)
    for line in violations:
        print(f"  {line}", file=sys.stderr)
    print(
        "\nEvery action must DRIVE the game, not write its outcome: the game charges the costs and "
        "applies the side effects along its own path (checkAction / beginUsing / DoFunction / "
        "performUseAction / the movement controller), and skipping that path is a cheat even when the "
        "action itself is something a player can do.\n"
        "\nFix the code, or - if a primitive is genuinely unavoidable - move it to the allowed list in "
        "agent/authenticity.py AND add a case to agent/detector_tests.py proving why.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
