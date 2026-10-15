using StardewValley;
using StardewValley.Menus;

namespace StateBridge.Utils;

/// <summary>Shared predicates so the agent only acts when the game actually accepts input.</summary>
public static class GameQuery
{
    /// <summary>
    /// True when nothing blocks player input (no menu, no dialogue, no event, and the farmer can move).
    /// Stardew 1.6 has no single flag for this, so we compose it from the real state.
    /// </summary>
    public static bool IsPlayerFree()
    {
        Farmer? f = Game1.player;
        if (f == null)
            return false;
        if (Game1.activeClickableMenu != null)
            return false;
        if (Game1.eventUp || Game1.dialogueUp)
            return false;
        if (!f.CanMove)
            return false;
        return true;
    }

    /// <summary>Why the player is currently blocked; empty string when free.</summary>
    public static string BlockedReason()
    {
        if (Game1.player == null)
            return "no active save";

        // Most specific first. Sitting on Game1.eventUp first reported a plain greeting as
        // "cutscene/event playing", which sends the agent looking for the wrong remedy - and the
        // remedy for a dialogue box is simply to advance it.
        if (Game1.activeClickableMenu is DialogueBox dialogue)
        {
            return dialogue.responses is { Length: > 0 }
                ? "dialogue is waiting for a choice"
                : "dialogue is open";
        }

        if (Game1.activeClickableMenu != null)
            return $"menu open: {Game1.activeClickableMenu.GetType().Name}";

        if (Game1.dialogueUp)
            return "dialogue is open";

        if (Game1.eventUp)
            return "cutscene/event playing";

        if (!Game1.player.CanMove)
            return "farmer cannot move right now";

        return "";
    }

    public static (int x, int y) TileOf(Farmer f) => ((int)f.Tile.X, (int)f.Tile.Y);

    /// <summary>The tile the farmer is facing (the one an action/tool would hit).</summary>
    public static (int x, int y) FacingTile(Farmer f)
    {
        (int dx, int dy) = f.FacingDirection switch
        {
            0 => (0, -1), // up
            1 => (1, 0),  // right
            2 => (0, 1),  // down
            3 => (-1, 0), // left
            _ => (0, 0),
        };
        (int x, int y) = TileOf(f);
        return (x + dx, y + dy);
    }
}
