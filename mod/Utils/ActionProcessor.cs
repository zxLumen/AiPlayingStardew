using System.Reflection;
using System.Text;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;
using StardewValley.Pathfinding;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using StateBridge.Models;
using xTile.Dimensions;

namespace StateBridge.Utils;

/// <summary>
/// Executes agent actions against the live game. Must only be called on the game thread.
/// </summary>
public static class ActionProcessor
{
    /// <summary>The route a go_to is currently walking, so progress can be reported to the agent.</summary>
    internal readonly record struct PlannedRoute(int TargetX, int TargetY, int PlannedSteps);

    /// <summary>Set while a go_to route is active; null when nothing is planned.</summary>
    internal static PlannedRoute? PlannedPath;

    /// <summary>
    /// Every action this bridge actually implements.
    ///
    /// It exists because the project had TWO lists of actions that were never compared: this one's
    /// predecessors, and the authenticity ledger's declarations. The ledger declared `open_screen`,
    /// `map` and `inventory` as things a player can do - none of which were ever built - so its own
    /// defect ("reading the world map must require the map to be open") pointed at an action that did
    /// not exist, and the defect stood open for weeks. A claim nothing can check is not a claim.
    ///
    /// Keep this next to the switch it describes, and let the python side diff it against the ledger
    /// every cycle. Anything declared but absent here is a defect, loudly.
    /// </summary>
    public static readonly string[] ACTIONS =
    {
        // handled before the switch
        "observe", "scan", "game_data", "overlay", "narrate", "chat", "progress",
        // the switch
        "select_slot", "use_tool", "use_tool_at", "work", "plant", "enter", "clear_area", "refill",
        "till_area", "plant_all", "water_all", "harvest_all", "chore", "cancel_chore",
        "interact", "interact_at", "exit_building", "move", "go_to", "sleep", "clear_event",
        "stop", "press_key", "focus_window", "menu_click", "title_button", "skip_intro",
        "new_game", "create_character", "set_appearance", "set_text",
        "dialogue_choose", "dialogue_advance", "noop",
        // asking questions about the running game, and opening screens
        "inspect", "open_screen", "actions",
    };


    public static (bool ok, string detail, Dictionary<string, object?>? data) Execute(string? body, bool actionsEnabled, IMonitor monitor)
    {
        if (!actionsEnabled)
            return (false, "actions are disabled (enable_actions=false in config.json)", null);

        if (string.IsNullOrWhiteSpace(body))
            return (false, "empty request body", null);

        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(body);
        }
        catch (Exception ex)
        {
            return (false, $"invalid JSON: {ex.Message}", null);
        }

        using (doc)
        {
            JsonElement root = doc.RootElement;
            string action = root.TryGetProperty("action", out JsonElement actionEl)
                ? actionEl.GetString() ?? ""
                : "";

            monitor.Log($"Action received: {action}", LogLevel.Debug);

            try
            {
                // observe answers with a structured payload rather than a plain sentence.
                if (action == "observe")
                    return Observe(root);

                if (action == "scan")
                    return Scan(root);

                if (action == "game_data")
                    return GameData(root, monitor);

                if (action == "overlay")
                    return Overlay(root);

                if (action == "narrate")
                    return Narrate(root);

                if (action == "chat")
                    return Chat(root);

                if (action == "progress")
                    return Progress(root);

                (bool ok, string detail) = action switch
                {
                    "select_slot" => SelectSlot(root),
                    "use_tool" => UseTool(),
                    "use_tool_at" => UseToolAt(root),
                    "work" => Work(root),
                    "plant" => Plant(root),
                    "enter" => Enter(root),
                    "clear_area" => ClearArea(root),
                    "refill" => Refill(),                    "till_area" => TillArea(root),
                    "plant_all" => PlantAll(),
                    "water_all" => WaterAll(),
                    "harvest_all" => HarvestAll(),
                    "chore" => ChoreStatus(),
                    "cancel_chore" => ChoreCancel(),
                    "interact" => Interact(),
                    "interact_at" => InteractAt(root),
                    "exit_building" => ExitBuilding(),
                    "move" => Move(root),
                    "go_to" => GoTo(root),
                    "sleep" => Sleep(),
                    "clear_event" => ClearEvent(),
                    "stop" => StopMove(),
                    "press_key" => PressKey(root),
                    "focus_window" => FocusWindow(),
                    "menu_click" => MenuClick(root),
                    "title_button" => TitleButton(root),
                    "skip_intro" => SkipIntro(),
                    "new_game" => NewGame(root),
                    "create_character" => NewGame(root),
                    "set_appearance" => SetAppearance(root),
                    "set_text" => SetText(root),
                    "dialogue_choose" => DialogueChoose(root),
                    "dialogue_advance" => DialogueAdvance(),
                    "noop" => (true, "noop"),
                    "inspect" => Inspect(root),
                    "open_screen" => OpenScreen(root),
                    "actions" => (true, "bridge actions: " + string.Join(", ", ACTIONS)),
                    _ => (false, $"unknown action '{action}'"),
                };

                return (ok, detail, null);
            }
            catch (Exception ex)
            {
                return (false, $"action '{action}' threw: {ex.Message}", null);
            }
        }
    }


    /// <summary>
    /// Turns a failure sentence into a stable code an agent can branch on. An agent that learns
    /// needs to tell "wrong tool" apart from "nothing there"; prose is not enough.
    /// </summary>
    public static string ClassifyFailure(bool ok, string detail)
    {
        if (ok)
            return "";

        string text = detail ?? "";

        if (text.Contains("the door is locked")) return "LOCKED";
        if (text.Contains("no walkable path")) return "NO_PATH";
        if (text.Contains("not free")) return "NOT_FREE";
        if (text.Contains("current item is not a tool")) return "WRONG_TOOL";
        if (text.Contains("no menu is open")) return "NO_MENU";
        if (text.Contains("no button")) return "NO_SUCH_BUTTON";
        if (text.Contains("out of range")) return "OUT_OF_RANGE";
        if (text.Contains("not adjacent")) return "NOT_ADJACENT";
        if (text.Contains("already outdoors")) return "ALREADY_OUTDOORS";
        if (text.Contains("no bed here")) return "NO_BED";
        if (text.Contains("missing '")) return "BAD_ARGS";
        if (text.Contains("unknown action")) return "UNKNOWN_ACTION";
        if (text.Contains("could not read")) return "NOT_AVAILABLE";
        if (text.Contains("is not open yet") || text.Contains("not ready yet")) return "NOT_READY";
        if (text.Contains("did nothing")) return "NOTHING_DONE";
        if (text.Contains("no active save")) return "NO_SAVE";
        if (text.Contains("invalid JSON")) return "BAD_JSON";
        if (text.Contains("disabled")) return "DISABLED";
        if (text.Contains("threw")) return "EXCEPTION";

        return "FAILED";
    }


    /// <summary>
    /// Reports what a single tile contains: whether it can be walked on, what object or crop
    /// stands there, and who is on it. This is the agent's sense organ for exploring.
    /// </summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) Observe(JsonElement root)
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save", null);

        if (!TryGetInt(root, "x", out int x) || !TryGetInt(root, "y", out int y))
            return (false, "missing 'x' / 'y'", null);

        GameLocation location = Game1.currentLocation;
        var tile = new Vector2(x, y);

        var info = new Dictionary<string, object?>
        {
            ["x"] = x,
            ["y"] = y,
            ["location"] = location.Name,
            ["distance"] = Math.Abs(x - (int)Game1.player.Tile.X) + Math.Abs(y - (int)Game1.player.Tile.Y),
        };

        // What this tile is, and what it would take to work it - so an agent knows if it is worth
        // walking over and whether it has the right tool.
        try
        {
            (string kind, Type? toolType) = ClassifyTarget(location, x, y);
            info["kind"] = kind;
            if (toolType != null)
                info["needs_tool"] = FriendlyToolName(toolType);
            if (toolType != null && FindToolIndex(Game1.player, toolType) < 0)
                info["has_tool"] = false;
        }
        catch { }

        try { info["passable"] = location.isTilePassable(tile); } catch { }
        try { info["water"] = location.isWaterTile(x, y); } catch { }
        try { info["buildable"] = location.isBuildable(tile, false); } catch { }
        try { info["diggable"] = location.doesTileHaveProperty(x, y, "Diggable", "Back", false) != null; } catch { }

        // The game's own instruction for this tile, verbatim: "Mailbox", "Board", "Bed", "Warp ...".
        // It is written on the map, and it is what checkAction acts on - so an agent that can read it
        // knows what a tile DOES before walking to it, instead of standing on it and experimenting.
        try
        {
            string action = WorldMapper.TileAction(location, x, y);
            if (action.Length > 0)
                info["action"] = action;
        }
        catch { }

        try
        {
            foreach (KeyValuePair<Vector2, StardewValley.Object> pair in location.objects.Pairs)
            {
                if (pair.Key.X != x || pair.Key.Y != y || pair.Value == null)
                    continue;

                StardewValley.Object obj = pair.Value;
                info["object"] = obj.DisplayName ?? obj.Name ?? "";
                info["object_craftable"] = obj.bigCraftable.Value;
                try { info["object_ready"] = obj.readyForHarvest.Value; } catch { }
                try { info["object_is_weed"] = obj.IsWeeds(); } catch { }
                try { info["object_is_twig"] = obj.IsTwig(); } catch { }
                break;
            }
        }
        catch { }

        try
        {
            TerrainFeature? feature = FindTerrainFeature(location, tile);
            if (feature is HoeDirt dirt)
            {
                info["ground"] = "hoe_dirt";
                info["watered"] = dirt.isWatered();
                if (dirt.crop != null)
                {
                    info["crop_ready"] = dirt.readyForHarvest();
                    info["crop_dead"] = dirt.crop.dead.Value;
                    try { info["crop"] = ItemRegistry.GetDataOrErrorItem(dirt.crop.indexOfHarvest.Value).DisplayName; } catch { }
                }
            }
            else if (feature != null)
            {
                info["ground"] = feature.GetType().Name;
            }
        }
        catch { }

        try
        {
            foreach (NPC npc in location.characters)
            {
                if (npc != null && (int)npc.Tile.X == x && (int)npc.Tile.Y == y)
                {
                    info["character"] = npc.displayName ?? npc.Name ?? "";
                    break;
                }
            }
        }
        catch { }

        return (true, $"tile ({x},{y}) in {location.Name}", info);
    }


    private static TerrainFeature? FindTerrainFeature(GameLocation location, Vector2 tile)
    {
        foreach (SerializableDictionary<Vector2, TerrainFeature> chunk in location.terrainFeatures)
        {
            if (chunk != null && chunk.TryGetValue(tile, out TerrainFeature? feature))
                return feature;
        }
        return null;
    }


    private static (bool, string) SelectSlot(JsonElement root)
    {
        if (!root.TryGetProperty("index", out JsonElement idxEl))
            return (false, "missing 'index'");

        int index = idxEl.GetInt32();
        Farmer f = Game1.player;
        if (f?.Items == null)
            return (false, "no inventory");

        if (index < 0 || index >= f.Items.Count)
            return (false, $"index {index} out of range (0..{f.Items.Count - 1})");

        if (f.Items[index] == null)
            return (false, $"slot {index} is empty");

        f.CurrentToolIndex = index;
        string name = f.Items[index]?.DisplayName ?? "?";
        return (true, $"selected slot {index}: {name}");
    }


    private static (bool, string) UseTool()
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        Farmer f = Game1.player;
        if (f.CurrentItem is not Tool tool)
            return (false, "current item is not a tool");

        (int x, int y) = GameQuery.FacingTile(f);
        bool started = tool.beginUsing(Game1.currentLocation, x, y, f);
        return (started, started
            ? $"used {tool.DisplayName} at ({x}, {y})"
            : $"{tool.DisplayName} could not start using at ({x}, {y})");
    }


    private static (bool, string) Interact()
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        Farmer f = Game1.player;
        (int x, int y) = GameQuery.FacingTile(f);

        string locked = CheckTileAction(Game1.currentLocation, x, y, f, out bool handled);
        if (locked.Length > 0)
            return (false, locked);

        return (handled, $"checkAction at ({x}, {y}) -> {handled}");
    }


    /// <summary>
    /// Runs the game's own tile action and READS THE RESULT rather than its return value.
    ///
    /// checkAction returns true when it handled the tile - and "handled" includes painting the
    /// locked-door dialogue. So knocking on a shop that is shut has been reported as SUCCESS every
    /// day: the bridge said ok, the witness saw "a dialogue box opened" and called it progress, and
    /// the learning layer recorded a win - which is precisely why nothing ever corrected it. The
    /// streamer knocked on Pierre's door seventeen times in one day, every knock a recorded success,
    /// while the game was printing the answer on the screen: 上锁了……（营业时间：9:00~21:00）.
    ///
    /// Returns the reason the tile refused, or "" when there is nothing to explain. `handled` is the
    /// game's own return value, unchanged.
    /// </summary>
    internal static string CheckTileAction(GameLocation location, int x, int y, Farmer farmer, out bool handled)
    {
        handled = false;

        try { handled = location.checkAction(new Location(x, y), Game1.viewport, farmer); }
        catch { }

        // A door that opened is a different location - but NOT YET: warpFarmer fades the screen out
        // first, so being still here a moment later proves nothing. Judging on that reported a shop
        // that had just let the farmer in as LOCKED, with its own opening hours quoted back at it.
        // The game's own words are the discriminator: a shut door PAINTS its message, so compare what
        // is on the screen with the sentence the game says for this door.
        ShopDoorInfo? door = WorldMapper.LockedDoorAt(location, x, y);
        if (door == null)
            return "";

        if (!ReferenceEquals(Game1.currentLocation, location))
            return "";

        string sentence = WorldMapper.LockedSentence(door);
        string shown = UiStateBuilder.OpenDialogueText();
        if (sentence.Length == 0 || shown.Length == 0)
            return "";

        string a = sentence.Replace(" ", "").Replace("\n", "");
        string b = shown.Replace(" ", "").Replace("\n", "");
        if (a.Length == 0 || !b.Contains(a, StringComparison.Ordinal))
            return "";

        return LockedDetail(door);
    }


    /// <summary>
    /// "This door is shut, here is why and until when", in the game's own words and then the clock.
    /// The hours are on the tile, so they can be said before the knock as well - see
    /// LockedDoorRefusal.
    /// </summary>
    private static string LockedDetail(ShopDoorInfo door)
    {
        string sentence = WorldMapper.LockedSentence(door);
        var text = new StringBuilder("the door is locked");

        if (sentence.Length > 0)
            text.Append($" ('{sentence}')");

        text.Append($" - {door.LeadsTo} opens {door.OpensText} and shuts {door.ClosesText}");

        try
        {
            int now = Game1.timeOfDay;
            int wait = door.OpensAt - now;

            if (now >= door.ClosesAt)
                text.Append($". It is {WorldMapper.ClockText(now)} and it is shut for the day - come back tomorrow");
            else if (wait > 0)
                text.Append($". It is {WorldMapper.ClockText(now)}, so {wait} game-minutes to wait");
        }
        catch { }

        text.Append(". Standing here knocking is not something a player does; the hours are known, so plan around them.");
        return text.ToString();
    }


    /// <summary>
    /// Shut by its own declared hours? Then say so instead of walking the farmer across town to find
    /// out. Note that this is the SAME fact the game prints when you knock, and it is only refused
    /// when the CLOCK is the reason: a friends-only door or a festival is not about the hour, and
    /// guessing wrong there would refuse a door that would have opened.
    /// </summary>
    private static string LockedDoorRefusal(GameLocation location, int x, int y)
    {
        ShopDoorInfo? door = WorldMapper.LockedDoorAt(location, x, y);
        if (door == null)
            return "";

        if (door.OpenNow)
            return "";

        return LockedDetail(door);
    }


    /// <summary>
    /// Does the right physical thing to a tile, picking and holding the correct tool itself.
    ///
    /// This is muscle memory, not judgement: a real player never thinks "a rock needs a pickaxe",
    /// they just swing the pickaxe. Putting the game's own rules here means the agent spends its
    /// (expensive, slow) reasoning on strategy instead of on re-learning the controls.
    /// </summary>
    private static (bool, string) Work(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        if (!TryGetInt(root, "x", out int x) || !TryGetInt(root, "y", out int y))
            return (false, "missing 'x' / 'y'");

        GameLocation location = Game1.currentLocation;
        Farmer farmer = Game1.player;

        // Out of reach: walk into range first, then swing on arrival. Reporting success while
        // standing five tiles away is exactly the kind of lie that makes an agent loop forever.
        if (!IsAdjacent(farmer, x, y))
        {
            Point? stand = FindAdjacentWalkable(location, x, y);
            if (stand == null)
                return (false, $"there is nowhere to stand next to ({x},{y})");

            try
            {
                var controller = new PathFindController(farmer, location, stand.Value, 2);
                farmer.controller = controller;

                if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
                {
                    farmer.controller = null;
                    return (false, $"cannot reach a tile next to ({x},{y})");
                }

                PlannedPath = new PlannedRoute(stand.Value.X, stand.Value.Y, controller.pathToEndPoint.Count);
            }
            catch (Exception ex)
            {
                return (false, $"could not walk to ({x},{y}): {ex.Message}");
            }

            _pendingAction = ("work", x, y);
            return (true, $"walking into range of ({x},{y}) - it will be worked on arrival");
        }

        (string kind, Type? toolType) = ClassifyTarget(location, x, y);
        FaceTowards(farmer, x, y);

        switch (kind)
        {
            case "nothing":
                return (false, $"nothing to work on at ({x},{y}) - use observe to see the tile");

            case "ready_crop":
            case "forage":
            case "ready_machine":
            case "bush":
            {
                bool handled = location.checkAction(new Location(x, y), Game1.viewport, farmer);
                return (handled, handled
                    ? $"{kind} handled at ({x},{y})"
                    : $"{kind} at ({x},{y}) did not respond");
            }

            case "growing_crop":
                return (true, $"the crop at ({x},{y}) is watered and still growing - nothing to do");
        }

        if (toolType == null)
            return (false, $"do not know how to work a '{kind}' at ({x},{y})");

        if (!HoldTool(farmer, toolType))
            return (false, $"no {FriendlyToolName(toolType)} in the inventory for this {kind}");

        if (farmer.CurrentItem is not Tool tool)
            return (false, "could not hold the tool");

        if (!farmer.CanMove)
            return (false, $"the farmer cannot act right now ({GameQuery.BlockedReason()})");

        // Swing it the way the game itself does. Tool.beginUsing() silently does nothing for the
        // scythe (a MeleeWeapon), so go through the farmer's own use-tool routine instead: it
        // works uniformly for every tool and runs the real animation and effect.
        SwingTool(tool, location, x, y, farmer);
        return (true, $"{kind}: swung {tool.DisplayName} at ({x},{y})");
    }


    /// <summary>
    /// Actually applies a tool to a tile.
    ///
    /// beginUsing() only starts the swing animation - it does not apply any effect, and it returns
    /// false outright for the scythe. DoFunction() is what really tills, waters, chops and clears.
    /// Without it the farmer swings at thin air forever and nothing in the world changes.
    /// </summary>
    /// <summary>
    /// Puts the held seed into a tilled tile.
    ///
    /// Planting is NOT checkAction and NOT a tool swing - that is why every earlier attempt failed
    /// with "interacted -> False" or "current item is not a tool". The game plants through
    /// HoeDirt.plant().
    ///
    /// But HoeDirt.plant() only puts the CROP into the soil. The game charges the player for the seed
    /// elsewhere - in the seed's own use path - so planting through HoeDirt.plant() alone plants FOR
    /// FREE, and that is exactly what was happening: four parsnip seeds became fifty-one parsnip
    /// crops while the bag still held all four. No player can do that, which makes it a cheat - and
    /// the seed was always taken here, right after the crop goes in.
    /// </summary>
    internal static bool PlantAt(GameLocation location, int x, int y, Farmer farmer)
    {
        if (farmer.CurrentItem is not StardewValley.Object held)
            return false;

        try
        {
            if (location.terrainFeatures.TryGetValue(new Vector2(x, y), out TerrainFeature? feature)
                && feature is HoeDirt dirt)
            {
                try
                {
                    if (dirt.plant(held.QualifiedItemId, farmer, false))
                    {
                        ChargeSeed(farmer, held);
                        return true;
                    }
                }
                catch { }

                try
                {
                    if (dirt.plant(held.ItemId, farmer, false))
                    {
                        ChargeSeed(farmer, held);
                        return true;
                    }
                }
                catch { }
            }
        }
        catch { }

        // Fallback: let the item act on whatever the farmer is facing. The game's own path already
        // charges for the seed, so nothing is taken here.
        try { return held.performUseAction(location); }
        catch { return false; }
    }


    /// <summary>
    /// Takes one seed out of the farmer's hands, the way the game does when a player plants.
    ///
    /// Its absence was not a small bug. Whatever the soil is doing, the player is paying a seed for
    /// it, and the bridge was not: an entire field appeared out of four seeds. A cost that is not
    /// charged is a cheat, however good the rest of the action looks.
    /// </summary>
    private static void ChargeSeed(Farmer farmer, StardewValley.Object seed)
    {
        try
        {
            // The game's own call, and the one that behaves with the held stack.
            if (ReferenceEquals(farmer.CurrentItem, seed))
            {
                farmer.reduceActiveItemByOne();
                return;
            }
        }
        catch { /* fall through to the explicit search */ }

        try
        {
            for (int i = 0; i < farmer.Items.Count; i++)
            {
                Item? item = farmer.Items[i];
                if (item == null || item.QualifiedItemId != seed.QualifiedItemId)
                    continue;

                item.Stack--;
                if (item.Stack <= 0)
                    farmer.Items[i] = null;
                return;
            }
        }
        catch { }
    }


    /// <summary>Plants the held seeds into one tile, walking into range first.</summary>
    private static (bool, string) Plant(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        if (!TryGetInt(root, "x", out int x) || !TryGetInt(root, "y", out int y))
            return (false, "missing 'x' / 'y'");

        if (Game1.player.CurrentItem is not StardewValley.Object held)
            return (false, $"hold the seeds first - currently holding '{Game1.player.CurrentItem?.DisplayName ?? "nothing"}'");

        if (Game1.player.CurrentItem is Tool)
            return (false, "you are holding a tool - select the seeds first");

        GameLocation location = Game1.currentLocation;

        if (!IsAdjacent(Game1.player, x, y))
        {
            Point? stand = FindAdjacentWalkable(location, x, y);
            if (stand == null)
                return (false, $"there is nowhere to stand next to ({x},{y})");

            try
            {
                var controller = new PathFindController(Game1.player, location, stand.Value, 2);
                Game1.player.controller = controller;
                if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
                {
                    Game1.player.controller = null;
                    return (false, $"cannot reach a tile next to ({x},{y})");
                }
            }
            catch (Exception ex)
            {
                return (false, $"could not walk to ({x},{y}): {ex.Message}");
            }

            _pendingAction = ("plant", x, y);
            return (true, $"walking into range of ({x},{y}) - the seed goes in on arrival");
        }

        FaceTowards(Game1.player, x, y);
        bool planted = PlantAt(location, x, y, Game1.player);

        if (planted)
            return (true, $"planted {held.DisplayName} at ({x},{y})");

        // Say what the tile actually is, instead of asking a question the agent cannot answer.
        (string kind, _) = ClassifyTarget(location, x, y);
        return (false,
            $"cannot plant at ({x},{y}) - that tile is '{kind}', not empty tilled soil. " +
            "till it first (till_area) and confirm it became hoe_dirt.");
    }


    /// <summary>
    /// Feeds the on-screen agent panel. The agent side pushes what the agents just did, tagged with
    /// who said it; the game draws it down the left. Nothing is read back - this is one-way.
    /// </summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) Overlay(JsonElement root)
    {
        if (root.TryGetProperty("clear", out JsonElement clearEl)
            && clearEl.ValueKind == JsonValueKind.True)
        {
            OverlayPanel.Clear();
            return (true, "overlay cleared", null);
        }

        var items = new List<KeyValuePair<string, string>>();

        if (root.TryGetProperty("lines", out JsonElement linesEl)
            && linesEl.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement entry in linesEl.EnumerateArray())
            {
                if (entry.ValueKind == JsonValueKind.String)
                {
                    items.Add(new KeyValuePair<string, string>("", entry.GetString() ?? ""));
                    continue;
                }

                if (entry.ValueKind != JsonValueKind.Object)
                    continue;

                string tag = entry.TryGetProperty("tag", out JsonElement tagEl)
                    ? tagEl.GetString() ?? ""
                    : "";

                string text = entry.TryGetProperty("text", out JsonElement textEl)
                    ? textEl.GetString() ?? ""
                    : "";

                items.Add(new KeyValuePair<string, string>(tag, text));
            }
        }

        if (items.Count == 0)
            return (false, "nothing to show (send 'lines')", null);

        OverlayPanel.Push(items);
        return (true, $"overlay +{items.Count} line(s), {OverlayPanel.Count} kept", null);
    }


    /// <summary>
    /// The streamer's voice, on screen. This is the show: one line of what it is saying out loud,
    /// big, at the bottom, exactly as a viewer would hear it. Nothing is read back.
    /// </summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) Narrate(JsonElement root)
    {
        string text = root.TryGetProperty("text", out JsonElement textEl) ? textEl.GetString() ?? "" : "";
        string speaker = root.TryGetProperty("speaker", out JsonElement speakerEl) ? speakerEl.GetString() ?? "" : "";

        if (string.IsNullOrWhiteSpace(text))
            return (false, "nothing to say (send 'text')", null);

        OverlayPanel.Say(speaker, text);
        return (true, $"subtitle: {text.Length} chars", null);
    }


    /// <summary>The room's messages, down the right. Newest at the bottom.</summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) Chat(JsonElement root)
    {
        if (root.TryGetProperty("clear", out JsonElement clearEl)
            && clearEl.ValueKind == JsonValueKind.True)
        {
            OverlayPanel.ClearChat();
            return (true, "chat cleared", null);
        }

        var items = new List<KeyValuePair<string, string>>();

        if (root.TryGetProperty("lines", out JsonElement linesEl)
            && linesEl.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement entry in linesEl.EnumerateArray())
            {
                if (entry.ValueKind == JsonValueKind.String)
                {
                    items.Add(new KeyValuePair<string, string>("", entry.GetString() ?? ""));
                    continue;
                }

                if (entry.ValueKind != JsonValueKind.Object)
                    continue;

                string user = entry.TryGetProperty("user", out JsonElement userEl)
                    ? userEl.GetString() ?? ""
                    : "";

                string text = entry.TryGetProperty("text", out JsonElement textEl)
                    ? textEl.GetString() ?? ""
                    : "";

                items.Add(new KeyValuePair<string, string>(user, text));
            }
        }

        if (items.Count == 0)
            return (false, "nothing to show (send 'lines')", null);

        OverlayPanel.PushChat(items);
        return (true, $"chat +{items.Count} message(s)", null);
    }


    /// <summary>The strip across the top: day, season, quests, money, today's goal.</summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) Progress(JsonElement root)
    {
        string text = root.TryGetProperty("text", out JsonElement textEl) ? textEl.GetString() ?? "" : "";
        OverlayPanel.SetProgress(text);
        return (true, "progress set", null);
    }



    ///
    /// Call it with what="catalog" to list every data asset the game ships. Nothing about Stardew
    /// Valley's rules lives in this mod - the agent reads the same tables the game itself reads, and
    /// works the rules out from them.
    /// </summary>
    private static (bool ok, string detail, Dictionary<string, object?>? data) GameData(JsonElement root, IMonitor monitor)
    {
        string wanted = GetString(root, "what").Trim();

        if (wanted.Length == 0 || wanted.Equals("catalog", StringComparison.OrdinalIgnoreCase) || wanted.Equals("list", StringComparison.OrdinalIgnoreCase))
        {
            List<string> all = GameContent.Catalog(monitor);

            var listing = new Dictionary<string, object?>
            {
                ["count"] = all.Count,
                ["assets"] = all,
            };

            return (true, $"{all.Count} data assets available - ask for any of them by name", listing);
        }

        List<string> catalog = GameContent.Catalog(monitor);

        string? asset =
            catalog.FirstOrDefault(a => a.Equals(wanted, StringComparison.OrdinalIgnoreCase))
            ?? catalog.FirstOrDefault(a => a.EndsWith("/" + wanted, StringComparison.OrdinalIgnoreCase))
            ?? catalog.FirstOrDefault(a => Path.GetFileName(a).Equals(wanted, StringComparison.OrdinalIgnoreCase))
            ?? catalog.FirstOrDefault(a => a.Contains(wanted, StringComparison.OrdinalIgnoreCase));

        if (asset == null)
        {
            string near = string.Join(", ", catalog.Where(a => a.Contains(wanted, StringComparison.OrdinalIgnoreCase)).Take(12));
            return (false, $"no data called '{wanted}'. Closest: {(near.Length > 0 ? near : "ask for \"catalog\"")}", null);
        }

        (object? loaded, string typeName) = GameContent.Load(asset, monitor);

        if (loaded == null)
            return (false, $"could not read {asset}: {GameContent.LastError}", null);

        string key = GetString(root, "key").Trim();
        string query = GetString(root, "query").Trim();
        int limit = TryGetInt(root, "limit", out int value) ? Math.Clamp(value, 1, 500) : (query.Length > 0 ? 25 : 30);

        if (loaded is System.Collections.IDictionary dictionary)
        {
            var picked = new Dictionary<string, object?>();
            int taken = 0;

            foreach (System.Collections.DictionaryEntry entry in dictionary)
            {
                string entryKey = entry.Key?.ToString() ?? "";
                if (key.Length > 0 && !entryKey.Equals(key, StringComparison.OrdinalIgnoreCase))
                    continue;

                if (query.Length > 0 && !Mentions(entryKey, entry.Value, query))
                    continue;

                picked[entryKey] = entry.Value;
                if (++taken >= limit)
                    break;
            }

            var data = new Dictionary<string, object?>
            {
                ["asset"] = asset,
                ["entry_type"] = $"table of {typeName}",
                ["count_total"] = dictionary.Count,
                ["count_returned"] = picked.Count,
                ["entries"] = GameContent.ExpandTokens(picked),
            };

            return (true, $"{asset} ({typeName}): {picked.Count} of {dictionary.Count} entries", data);
        }

        if (loaded is System.Collections.IEnumerable list and not string)
        {
            var items = list.Cast<object?>().ToList();

            var matched = query.Length == 0
                ? items
                : items.Where(item => Mentions("", item, query)).ToList();

            var picked = matched.Take(limit).ToList();

            var data = new Dictionary<string, object?>
            {
                ["asset"] = asset,
                ["entry_type"] = $"list of {typeName}",
                ["count_total"] = matched.Count,
                ["count_returned"] = picked.Count,
                ["entries"] = GameContent.ExpandTokens(picked),
            };

            return (true, $"{asset} (list of {typeName}): {picked.Count} of {matched.Count} items", data);
        }

        var single = new Dictionary<string, object?>
        {
            ["asset"] = asset,
            ["entry_type"] = typeName,
            ["value"] = GameContent.ExpandTokens(loaded),
        };

        return (true, $"{asset} ({typeName}): a single object", single);
    }


    /// <summary>
    /// Whether an entry mentions some text, in its key or anywhere in its content.
    ///
    /// Without this an agent can only read data it already knows the identifier for, which is the
    /// same problem as not having the data at all - it can see that crops exist but not find one.
    /// </summary>
    private static bool Mentions(string entryKey, object? value, string query)
    {
        if (entryKey.Contains(query, StringComparison.OrdinalIgnoreCase))
            return true;

        try
        {
            string json = JsonSerializer.Serialize(value, new JsonSerializerOptions { IncludeFields = true });
            return json.Contains(query, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }


    /// <summary>Diagnostic: what each collector actually sees, so a stuck chore can be explained.</summary>
    private static (bool, string, Dictionary<string, object?>?) Scan(JsonElement root)
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save", null);

        GameLocation location = Game1.currentLocation;
        int cx = (int)Game1.player.Tile.X;
        int cy = (int)Game1.player.Tile.Y;
        int radius = TryGetInt(root, "radius", out int r) ? Math.Clamp(r, 1, 40) : 12;

        var data = new Dictionary<string, object?>
        {
            ["at"] = new[] { cx, cy },
            ["location"] = location.Name,
            ["radius"] = radius,
            ["debris"] = ChoreController.CollectDebris(location, cx, cy, radius).Count,
            ["tillable"] = ChoreController.CollectTillable(location, cx, cy, radius).Count,
            ["empty_soil"] = ChoreController.CollectEmptySoil(location, cx, cy, radius).Count,
            ["thirsty_crops"] = ChoreController.CollectThirstyCrops(location, cx, cy, radius).Count,
            ["ready_crops"] = ChoreController.CollectReadyCrops(location, cx, cy, radius).Count,
            ["chore"] = ChoreController.Status(),
        };

        // A few example tiles so the agent knows WHERE the work is, not just how much.
        try
        {
            data["sample_empty_soil"] = ChoreController.CollectEmptySoil(location, cx, cy, radius)
                .Take(5).Select(t => new[] { (int)t.X, (int)t.Y }).ToArray();
            data["sample_tillable"] = ChoreController.CollectTillable(location, cx, cy, radius)
                .Take(5).Select(t => new[] { (int)t.X, (int)t.Y }).ToArray();
            data["sample_debris"] = ChoreController.CollectDebris(location, cx, cy, radius)
                .Take(5).Select(t => new[] { (int)t.X, (int)t.Y }).ToArray();
        }
        catch { }

        // Independent probe: can we read one tile's terrain feature at all?
        try
        {
            bool found = location.terrainFeatures.TryGetValue(new Vector2(cx, cy), out TerrainFeature? here);
            data["feature_here"] = found && here != null ? here.GetType().Name : "none";
        }
        catch (Exception ex) { data["feature_here"] = "threw: " + ex.Message; }

        return (true, $"scan around ({cx},{cy}) r={radius}", data);
    }


    /// <summary>
    /// Walks into a building or area by name (e.g. "SeedShop").
    ///
    /// Door tiles are not pathable, so go_to can never reach them - that is why the agent kept
    /// bouncing off Pierre's shop. A door opens through checkAction, not by walking onto it.
    /// </summary>
    private static (bool, string) Enter(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        string target = GetString(root, "target").Trim();
        if (string.IsNullOrWhiteSpace(target))
            return (false, "missing 'target' - the place to enter, e.g. SeedShop");

        GameLocation location = Game1.currentLocation;

        // ONE list of exits, the same one /state reports, so the door the agent can SEE and the door
        // the action can USE cannot drift apart. `enter FarmHouse` used to fail on the farm because it
        // searched location.doors and location.warps by hand - and a building's door is in neither of
        // them: the game synthesizes it in getWarpFromDoor. See WorldMapper.BuildWarps.
        List<WarpInfo> exits = WorldMapper.BuildWarps(location);
        WarpInfo? match = null;

        foreach (WarpInfo exit in exits)
        {
            if (exit.Target != null && exit.Target.Contains(target, StringComparison.OrdinalIgnoreCase))
            {
                match = exit;
                break;
            }
        }

        if (match == null)
        {
            string names = string.Join(", ",
                exits.Select(w => w.Target).Where(t => !string.IsNullOrWhiteSpace(t)).Distinct().Take(12));
            return (false, $"nothing here leads to '{target}'. Known exits: {names}");
        }

        (int dx, int dy) = (match.X, match.Y);

        // Shut by its own hours? Then do not walk the farmer across town to find out. Same fact, said
        // before the trip instead of after it.
        string shut = LockedDoorRefusal(location, dx, dy);
        if (shut.Length > 0)
            return (false, shut);

        if (!IsAdjacent(Game1.player, dx, dy))
        {
            Point? stand = FindAdjacentWalkable(location, dx, dy);
            if (stand == null)
                return (false, $"nowhere to stand next to the door at ({dx},{dy})");

            try
            {
                var controller = new PathFindController(Game1.player, location, stand.Value, 2);
                Game1.player.controller = controller;
                if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
                {
                    Game1.player.controller = null;
                    return (false, $"cannot reach the door at ({dx},{dy})");
                }
            }
            catch (Exception ex)
            {
                return (false, $"could not walk to the door: {ex.Message}");
            }

            _pendingAction = ("enter", dx, dy);
            return (true, $"walking to the door of '{target}' - it opens on arrival");
        }

        FaceTowards(Game1.player, dx, dy);

        string locked = CheckTileAction(location, dx, dy, Game1.player, out bool opened);
        if (locked.Length > 0)
            return (false, locked);

        return (opened,
            opened ? $"entered via the door at ({dx},{dy})"
                   : $"the door at ({dx},{dy}) would not open - it may be locked or shut at this hour");
    }


    internal static void SwingTool(Tool tool, GameLocation location, int x, int y, Farmer farmer)
    {
        try { tool.beginUsing(location, x, y, farmer); }
        catch { }

        try { tool.DoFunction(location, x, y, 1, farmer); }
        catch { }
    }


    // -- the watering can --------------------------------------------------
    //
    // A watering can runs out. Nothing in the bridge used to say so, and there was no way to fill
    // it, so every watering action swung an empty can and reported success. These three helpers are
    // what turn "watering silently does nothing" into "watering needs a full can".

    /// <summary>How much the can holds when full.</summary>
    internal static int CanCapacity(WateringCan can)
    {
        try
        {
            var field = typeof(WateringCan).GetField(
                "waterCanMax", BindingFlags.Instance | BindingFlags.NonPublic);

            if (field?.GetValue(can) is int max && max > 0)
                return max;
        }
        catch { }

        return 40;
    }


    /// <summary>The watering can the farmer is holding, or null when there is none.</summary>
    internal static WateringCan? HeldCan(Farmer farmer)
    {
        if (farmer.CurrentItem is WateringCan current)
            return current;

        return farmer.Items?.OfType<WateringCan>().FirstOrDefault();
    }


    internal static bool CanIsFull(WateringCan can)
    {
        try { return can.IsBottomless || can.WaterLeft >= CanCapacity(can); }
        catch { return true; }
    }


    /// <summary>
    /// Fills the can from the water tile it faces, using the game's own refill path.
    ///
    /// The fallback writes the capacity directly because the game's refill only runs from a proper
    /// use action, and a chore's swing is not one.
    /// </summary>
    internal static void RefillCanAt(GameLocation location, int x, int y, Farmer farmer)
    {
        WateringCan? can = HeldCan(farmer);
        if (can == null)
            return;

        try
        {
            FaceTowards(farmer, x, y);
            int before = can.WaterLeft;
            can.DoFunction(location, x, y, 1, farmer);

            if (can.WaterLeft > before)
                return;
        }
        catch { }

        try { can.WaterLeft = CanCapacity(can); }
        catch { }
    }


    private static (string Kind, int X, int Y)? _pendingAction;

    private static readonly Point[] AdjacentOffsets =
    {
        new Point(0, 1), new Point(0, -1), new Point(1, 0), new Point(-1, 0),
    };

    /// <summary>
    /// Clears weeds, twigs and stones in an area, at game speed, with one model decision.
    /// This is what makes the agent look like a person playing instead of a person typing.
    /// </summary>
    /// <summary>
    /// Fills the watering can from the nearest water.
    ///
    /// This exists because the game says the can has to be refilled and the bridge had no way to do
    /// it - so every watering action quietly did nothing. It is a capability, not a rule: the agent
    /// still has to work out for itself that watering needs water.
    /// </summary>
    private static (bool, string) Refill()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        WateringCan? can = HeldCan(Game1.player);

        if (can == null)
            return (false, "no watering can in the inventory");

        if (CanIsFull(can))
            return (false, $"the watering can is already full ({can.WaterLeft}/{CanCapacity(can)})");

        int cx = (int)Game1.player.Tile.X;
        int cy = (int)Game1.player.Tile.Y;

        List<Vector2> water = ChoreController.CollectRefillTiles(Game1.currentLocation, cx, cy, 40);

        if (water.Count == 0)
            return (false, "no water to fill from within 40 tiles of here");

        // One water tile is the whole job. Queuing every water tile in sight made a five-second
        // job into a 351-target chore that could never be "finished".
        List<Vector2> nearest = ChoreController.Nearest(water, Game1.player.Tile, 1);

        return ChoreController.Start("refill", nearest);
    }


    private static (bool, string) ClearArea(JsonElement root)
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        int cx = TryGetInt(root, "x", out int x) ? x : (int)Game1.player.Tile.X;
        int cy = TryGetInt(root, "y", out int y) ? y : (int)Game1.player.Tile.Y;
        int radius = TryGetInt(root, "radius", out int r) ? Math.Clamp(r, 1, 60) : 25;

        List<Vector2> targets = ChoreController.CollectDebris(Game1.currentLocation, cx, cy, radius);
        return ChoreController.Start("clear_area", targets);
    }


    /// <summary>Tills a whole patch at game speed. One decision instead of one per tile.</summary>
    private static (bool, string) TillArea(JsonElement root)
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        int cx = TryGetInt(root, "x", out int x) ? x : (int)Game1.player.Tile.X;
        int cy = TryGetInt(root, "y", out int y) ? y : (int)Game1.player.Tile.Y;
        int radius = TryGetInt(root, "radius", out int r) ? Math.Clamp(r, 1, 20) : 40;

        List<Vector2> targets = ChoreController.CollectTillable(Game1.currentLocation, cx, cy, radius);
        return ChoreController.Start("till_area", targets);
    }


    /// <summary>
    /// Plants the held seeds into every empty tilled tile nearby. Select the seeds first, then call
    /// this once - it does the walking and the planting at game speed.
    /// </summary>
    private static (bool, string) PlantAll()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        if (Game1.player.CurrentItem is null || Game1.player.CurrentItem is Tool)
            return (false, $"hold the seeds first - currently holding '{Game1.player.CurrentItem?.DisplayName ?? "nothing"}'");

        List<Vector2> targets = ChoreController.CollectEmptySoil(
            Game1.currentLocation, (int)Game1.player.Tile.X, (int)Game1.player.Tile.Y, 60);

        if (targets.Count == 0)
        {
            GameLocation here = Game1.currentLocation;
            int px = (int)Game1.player.Tile.X;
            int py = (int)Game1.player.Tile.Y;

            return (false,
                $"no empty tilled soil within 60 of ({px},{py}); " +
                $"tillable here = {ChoreController.CollectTillable(here, px, py, 60).Count}, " +
                $"debris = {ChoreController.CollectDebris(here, px, py, 60).Count}. " +
                "Till some ground first with till_area, or move to the tilled patch.");
        }

        return ChoreController.Start("plant_all", targets);
    }


    private static (bool, string) WaterAll()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        List<Vector2> targets = ChoreController.CollectThirstyCrops(
            Game1.currentLocation, (int)Game1.player.Tile.X, (int)Game1.player.Tile.Y, 25);

        return ChoreController.Start("water_all", targets);
    }


    private static (bool, string) HarvestAll()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        List<Vector2> targets = ChoreController.CollectReadyCrops(
            Game1.currentLocation, (int)Game1.player.Tile.X, (int)Game1.player.Tile.Y, 25);

        return ChoreController.Start("harvest_all", targets);
    }


    private static (bool, string) ChoreStatus() => (true, ChoreController.Status());

    private static (bool, string) ChoreCancel() => ChoreController.Cancel();


    internal static bool IsAdjacent(Farmer farmer, int x, int y)
    {
        int dx = Math.Abs((int)farmer.Tile.X - x);
        int dy = Math.Abs((int)farmer.Tile.Y - y);
        return dx + dy == 1;
    }


    internal static Point? FindAdjacentWalkable(GameLocation location, int x, int y)
    {
        foreach (Point offset in AdjacentOffsets)
        {
            int nx = x + offset.X;
            int ny = y + offset.Y;

            try
            {
                if (!location.isTilePassable(new Location(nx, ny), Game1.viewport))
                    continue;

                if (location.objects.TryGetValue(new Vector2(nx, ny), out StardewValley.Object? obj)
                    && obj != null && !obj.isPassable())
                    continue;

                return new Point(nx, ny);
            }
            catch { }
        }

        return null;
    }


    /// <summary>
    /// Finishes a work request whose walk has completed: swing at the target once we are beside it.
    /// Called every tick from UpdateTicked.
    /// </summary>
    internal static void ProcessPendingWork(IMonitor monitor)
    {
        if (_pendingAction is not { } pending)
            return;

        Farmer? farmer = Game1.player;
        GameLocation? location = Game1.currentLocation;

        if (farmer == null || location == null || farmer.controller != null)
            return;

        _pendingAction = null;

        int tx = pending.X;
        int ty = pending.Y;

        if (!IsAdjacent(farmer, tx, ty))
            return;   // the walk ended somewhere else; let the caller re-issue

        try
        {
            FaceTowards(farmer, tx, ty);

            if (pending.Kind == "plant")
            {
                PlantAt(location, tx, ty, farmer);
            }
            else if (pending.Kind == "enter")
            {
                // A door opens through checkAction, not by standing on it. And a door that does NOT
                // open has to say why: this was the last place a locked door still reported success.
                string locked = CheckTileAction(location, tx, ty, farmer, out bool handled);
                monitor.Log(locked.Length > 0
                    ? $"pending enter: {locked}"
                    : $"pending enter: checkAction at ({tx},{ty}) -> {handled}", LogLevel.Trace);
                return;
            }
            else if (farmer.CurrentItem is Tool tool)
            {
                SwingTool(tool, location, tx, ty, farmer);
            }

            monitor.Log($"pending {pending.Kind}: acted at ({tx},{ty})", LogLevel.Trace);
        }
        catch (Exception ex)
        {
            monitor.Log($"pending action failed: {ex.Message}", LogLevel.Warn);
        }
    }


    /// <summary>What a tile is and which tool it takes. This is the game's own rules, in one place.</summary>
    internal static (string kind, Type? toolType) ClassifyTarget(GameLocation location, int x, int y)
    {
        var tile = new Vector2(x, y);

        try
        {
            if (location.terrainFeatures.TryGetValue(tile, out TerrainFeature? feature) && feature != null)
            {
                switch (feature)
                {
                    case HoeDirt dirt when dirt.crop != null:
                        if (dirt.readyForHarvest())
                            return ("ready_crop", null);
                        return dirt.isWatered() ? ("growing_crop", null) : ("thirsty_crop", typeof(WateringCan));

                    case HoeDirt:
                        return ("empty_soil", null);

                    case Tree:
                    case FruitTree:
                        return ("tree", typeof(Axe));

                    case Grass:
                        return ("grass", typeof(MeleeWeapon));

                    case Bush:
                        return ("bush", null);
                }
            }
        }
        catch { }

        try
        {
            if (location.objects.TryGetValue(tile, out StardewValley.Object? obj) && obj != null)
            {
                if (obj.IsWeeds()) return ("weed", typeof(MeleeWeapon));
                if (obj.IsTwig()) return ("twig", typeof(Axe));
                if (obj.IsBreakableStone()) return ("stone", typeof(Pickaxe));

                bool ready = false;
                try { ready = obj.readyForHarvest.Value; } catch { }
                if (ready) return ("ready_machine", null);

                if (obj.isForage() || obj.IsSpawnedObject) return ("forage", null);
                if (obj.bigCraftable.Value) return ("machine", null);

                if (!obj.isPassable()) return ("blocked_object", typeof(Axe));
            }
        }
        catch { }

        try
        {
            foreach (ResourceClump clump in location.resourceClumps)
            {
                if (clump != null && clump.occupiesTile(x, y))
                    return ("big_debris", typeof(Axe));
            }
        }
        catch { }

        try
        {
            if (location.doesTileHaveProperty(x, y, "Diggable", "Back", false) != null)
                return ("tillable", typeof(Hoe));
        }
        catch { }

        return ("nothing", null);
    }


    /// <summary>Holds the first matching tool from the inventory. Scythes live on MeleeWeapon.</summary>
    internal static bool HoldTool(Farmer farmer, Type toolType)
    {
        int index = FindToolIndex(farmer, toolType);
        if (index < 0)
            return false;

        farmer.CurrentToolIndex = index;
        return true;
    }


    /// <summary>Index of the matching tool, or -1. Read-only - safe to call while inspecting state.</summary>
    private static int FindToolIndex(Farmer farmer, Type toolType)
    {
        if (farmer?.Items == null)
            return -1;

        for (int i = 0; i < farmer.Items.Count; i++)
        {
            Item? item = farmer.Items[i];
            if (item == null)
                continue;

            bool matches = toolType == typeof(MeleeWeapon)
                ? item is MeleeWeapon weapon && weapon.isScythe()
                : toolType.IsInstanceOfType(item);

            if (matches)
                return i;
        }

        return -1;
    }


    private static string FriendlyToolName(Type toolType) =>
        toolType == typeof(MeleeWeapon) ? "scythe" : toolType.Name.ToLowerInvariant();


    /// <summary>Faces a specific tile and swings the held tool at it. The building block for chores.</summary>
    private static (bool, string) UseToolAt(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        if (!TryGetInt(root, "x", out int x) || !TryGetInt(root, "y", out int y))
            return (false, "missing 'x' / 'y'");

        Farmer farmer = Game1.player;
        FaceTowards(farmer, x, y);

        if (farmer.CurrentItem is not Tool tool)
            return (false, "current item is not a tool");

        if (!farmer.CanMove)
            return (false, $"the farmer cannot act right now ({GameQuery.BlockedReason()})");

        // Same reason as `work`: beginUsing() alone never applies the effect.
        SwingTool(tool, Game1.currentLocation, x, y, farmer);
        return (true, $"swung {tool.DisplayName} at ({x},{y})");
    }


    /// <summary>Faces a specific tile and interacts with it (harvest, forage, talk, open).</summary>
    private static (bool, string) InteractAt(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        if (!TryGetInt(root, "x", out int x) || !TryGetInt(root, "y", out int y))
            return (false, "missing 'x' / 'y'");

        Farmer farmer = Game1.player;
        FaceTowards(farmer, x, y);

        // Shut by its own hours? Same answer as the game would give, said without drawing the
        // dialogue - the agent does not need to knock seventeen times to be told the hours.
        string shut = LockedDoorRefusal(Game1.currentLocation, x, y);
        if (shut.Length > 0)
            return (false, shut);

        string locked = CheckTileAction(Game1.currentLocation, x, y, farmer, out bool handled);
        if (locked.Length > 0)
            return (false, locked);

        return (handled, $"interacted with ({x},{y}) -> {handled}");
    }


    /// <summary>
    /// Leaves the current building by taking its own outgoing warp, arriving outside.
    /// Much more reliable than trying to walk onto a door tile, which is not pathable.
    /// </summary>
    private static (bool, string) ExitBuilding()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        (bool free, string why) = RequireFreePlayer();
        if (!free)
            return (false, why);

        GameLocation location = Game1.currentLocation;
        if (location.IsOutdoors)
            return (false, "already outdoors");

        foreach (Warp warp in location.warps)
        {
            if (warp == null)
                continue;

            string target = warp.TargetName ?? "";
            if (string.IsNullOrWhiteSpace(target) || target == location.Name || target == "null")
                continue;

            // warp.X/warp.Y are the tile on THIS map that triggers it - the doorstep. A player walks
            // onto that tile and the game takes them through; it does not lift them out of the room.
            // Putting the farmer on the far side directly is the same class of shortcut as teleporting
            // to town, and it skips the step that makes leaving a building a thing you did.
            var controller = new PathFindController(Game1.player, location, new Point(warp.X, warp.Y), 2);

            if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
                continue;                                   // that door is not walkable; try another

            Game1.player.controller = controller;
            return (true, $"walking to the exit at ({warp.X},{warp.Y}) -> {target}");
        }

        return (false, $"no walkable exit found in {location.Name}");
    }


    /// <summary>Freezes or resumes the in-game clock.</summary>
    /// <summary>
    /// Refused on purpose.
    ///
    /// A player cannot decide to stop the clock. Time does stop on its own when a menu is open - the
    /// inventory, the map - and that is the game's own behaviour, so this deliberately touches
    /// nothing: it just refuses. Holding the clock still for a whole day while carrying on playing
    /// is the thing a player cannot do, and it quietly breaks the game's rules: the clock never
    /// reaches 9:00, so the shop never opens and nothing that happens at an hour ever happens.
    /// </summary>
    private static (bool, string) PauseTime(JsonElement root)
    {
        return (false,
            "pause_time is disabled: a player cannot stop the clock. Open the inventory or the map " +
            "and time pauses by itself, the way it does for anyone playing - but the world cannot be " +
            "held still while you carry on.");
    }


    /// <summary>Points the farmer at a tile so use_tool_at / interact_at hit the right thing.</summary>
    internal static void FaceTowards(Farmer farmer, int targetX, int targetY)
    {
        int dx = targetX - (int)farmer.Tile.X;
        int dy = targetY - (int)farmer.Tile.Y;

        if (Math.Abs(dx) >= Math.Abs(dy))
            farmer.FacingDirection = dx > 0 ? 1 : 3;
        else
            farmer.FacingDirection = dy > 0 ? 2 : 0;
    }


    private static (bool, string) Move(JsonElement root)
    {
        string direction = root.TryGetProperty("direction", out JsonElement dirEl)
            ? (dirEl.GetString() ?? "").ToLowerInvariant()
            : "";

        bool valid = direction is "up" or "down" or "left" or "right";
        if (!valid)
            return (false, $"invalid direction '{direction}' (use up/down/left/right)");

        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        int durationMs = 400;
        if (root.TryGetProperty("duration_ms", out JsonElement durEl))
            durationMs = Math.Clamp(durEl.GetInt32(), 50, 10_000);

        MovementController.Start(direction, durationMs);
        return (true, $"moving {direction} for {durationMs}ms");
    }


    private static (bool, string) StopMove()
    {
        MovementController.Stop();
        return (true, "movement stopped");
    }


    /// <summary>
    /// Walks the farmer to a tile using the game's own pathfinder, stepping around walls and
    /// furniture. Far more reliable than issuing raw direction moves.
    /// </summary>
    private static (bool, string) GoTo(JsonElement root)
    {
        (bool ok, string why) = RequireFreePlayer();
        if (!ok)
            return (false, why);

        if (!root.TryGetProperty("x", out JsonElement xEl) || !root.TryGetProperty("y", out JsonElement yEl))
            return (false, "missing 'x' / 'y'");

        int x = xEl.GetInt32();
        int y = yEl.GetInt32();
        int facing = root.TryGetProperty("facing", out JsonElement facingEl) ? facingEl.GetInt32() : 2;

        Farmer farmer = Game1.player;
        GameLocation? location = Game1.currentLocation;
        if (location == null)
            return (false, "no current location");

        if (farmer.Tile.X == x && farmer.Tile.Y == y)
            return (true, $"already at ({x},{y})");

        MovementController.Stop();

        try
        {
            var controller = new PathFindController(farmer, location, new Point(x, y), facing);
            farmer.controller = controller;

            // A failed pathfind leaves an empty route. Without this check we would report
            // "walking there" while the farmer never moves - which is exactly what makes an
            // agent retry the same hopeless command forever.
            if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
            {
                farmer.controller = null;
                PlannedPath = null;
                return (false,
                    $"no walkable path to ({x},{y}) - it is probably blocked. " +
                    $"The farmer is at ({(int)farmer.Tile.X},{(int)farmer.Tile.Y}); " +
                    "aim at an adjacent free tile instead.");
            }

            PlannedPath = new PlannedRoute(x, y, controller.pathToEndPoint.Count);
        }
        catch (Exception ex)
        {
            farmer.controller = null;
            PlannedPath = null;
            return (false, $"could not start pathfinding to ({x},{y}): {ex.Message}");
        }

        return (true, $"walking to ({x},{y}) - {PlannedPath?.PlannedSteps ?? 0} steps");
    }


    /// <summary>
    /// Makes sure the game window is in front before injecting input.
    ///
    /// The game only acts on menu input while its window is active, so a click delivered to a
    /// background window lands in the menu's state and is never processed. The menu still reports
    /// the click, nothing happens, and the caller has no way to tell the difference - which is how
    /// a save that loads perfectly well by hand looks permanently unloadable to an agent.
    /// This is a precondition of every click-based action, so the bridge enforces it.
    /// </summary>
    internal static void EnsureWindowActive()
    {
        try
        {
            if (!NativeInput.IsGameWindowActive())
                NativeInput.FocusGameWindow();
        }
        catch { }
    }


    private static (bool, string) PressKey(JsonElement root)
    {
        EnsureWindowActive();

        string key = root.TryGetProperty("key", out JsonElement keyEl)
            ? (keyEl.GetString() ?? "").ToLowerInvariant()
            : "";

        if (!NativeInput.TryGetVirtualKey(key, out byte vk))
            return (false, $"unsupported key '{key}'");

        NativeInput.PressAndRelease(vk);
        return (true, $"pressed key '{key}' (needs the game window focused)");
    }


    /// <summary>
    /// Ends the day the legitimate way: walk into bed, lie down and answer "yes" to the game's
    /// sleep prompt. Falls back to the raw new-day routine if the bed interaction does not take.
    /// </summary>
    private static (bool, string) Sleep()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return (false, "no active save");

        (bool free, string why) = RequireFreePlayer();
        if (!free)
            return (false, why);

        // ALREADY ON THE WAY. This is not a nicety: the harness re-issues `sleep` whenever the watcher
        // runs out of patience, and this used to stop the movement and re-plan the walk to the bed
        // every time - so the farmer was restarted on the way to bed, over and over, and never
        // arrived. "He has never once managed to get into bed" is exactly what that produces.
        if (_pendingSleep)
            return (true, "already walking to the bed - it will lie down on arrival");

        MovementController.Stop();
        GameLocation location = Game1.currentLocation;
        BedFurniture? bed = FindBed(location);

        if (bed == null)
        {
            // Not home. A player walks home; they do not appear in their kitchen. If the night runs
            // out first, the game puts them to bed at 2am, which is also what happens to a player.
            return (false,
                $"there is no bed in {location.Name}. A player walks home to sleep - go_to the " +
                "FarmHouse door and go inside, then sleep at the bed. Stay up past 2am and the game " +
                "will put you to bed itself.");
        }

        Point spot = bed.GetBedSpot();
        return LieDown(location, bed);
    }


    private static bool _pendingSleep;

    /// <summary>Lies down once the walk home has finished. Called every tick from UpdateTicked.</summary>
    internal static void ProcessPendingSleep(IMonitor monitor)
    {
        if (!_pendingSleep)
            return;

        if (Game1.player == null || Game1.currentLocation == null)
        {
            _pendingSleep = false;
            return;
        }

        if (Game1.player.controller != null)
            return;

        BedFurniture? bed = FindBed(Game1.currentLocation);
        if (bed == null)
            return;

        _pendingSleep = false;
        (bool ok, string detail) = LieDown(Game1.currentLocation, bed);
        monitor.Log($"pending sleep: ok={ok} {detail}", LogLevel.Trace);
    }


    private static (bool, string) LieDown(GameLocation location, BedFurniture bed)
    {
        Point spot = bed.GetBedSpot();

        // Interacting with a bed means standing next to it, so walk there first. Putting the farmer
        // onto the bed from across the room is the same class of shortcut as teleporting to town.
        if (!IsAdjacent(Game1.player, spot.X, spot.Y))
        {
            Point? stand = FindAdjacentWalkable(location, spot.X, spot.Y);
            if (stand == null)
                return (false, $"nowhere to stand beside the bed at ({spot.X},{spot.Y})");

            Game1.player.controller = new PathFindController(Game1.player, location, stand.Value, 2);
            _pendingSleep = true;
            return (true, $"walking to the bed at ({spot.X},{spot.Y}) - will lie down on arrival");
        }

        return EnterBed(location, spot);
    }


    /// <summary>
    /// Gets into the bed: the bed runs its own sleep flow and we accept it.
    ///
    /// It used to start by teleporting the farmer onto the bed tile and setting `isInBed` by hand.
    /// Both are the same primitive as the `warp` action this project already bans - a player WALKS
    /// onto the bed, and the bed sets `isInBed` itself when the player answers yes. Reaching in and
    /// writing the outcome is exactly the cheat this class of bug is made of: the action looked
    /// legal, the farmer ended up in bed, and nothing along the way was how a person gets there.
    /// </summary>
    private static (bool, string) EnterBed(GameLocation location, Point spot)
    {
        string note;
        try
        {
            // Ask the bed to run its own "sleep?" flow, then accept it. The game does the rest -
            // including putting the farmer in the bed.
            location.checkAction(new Location(spot.X, spot.Y), Game1.viewport, Game1.player);
            location.answerDialogueAction("Sleep_Yes", null);
            note = "answered yes";
        }
        catch (Exception ex)
        {
            note = $"bed dialogue failed: {ex.Message}";
        }

        return (true, $"going to sleep at ({spot.X},{spot.Y}) - {note}");
    }


    /// <summary>
    /// Refused on purpose.
    ///
    /// A player cannot skip to tomorrow. They sleep in a bed, or they stay up until 2am and the game
    /// puts them to bed itself. Rolling the day over from an API is also the routine that once left
    /// the game permanently unable to act, because a raw new day started on top of a live cutscene
    /// leaves the event flag set with no event behind it.
    /// </summary>
    private static (bool, string) NewDay()
    {
        return (false,
            "new_day is disabled: a player cannot skip a day. Walk home and sleep in the bed, or " +
            "stay up past 2am and the game will put you to bed.");
    }


    /// <summary>
    /// Ends a cutscene that is holding the farmer, and clears anything left behind by one.
    ///
    /// This is recovery, not gameplay: a live event is the one state the game cannot be argued out
    /// of, and while it is set no action works and the day can never end.
    /// </summary>
    private static (bool, string) ClearEvent()
    {
        if (Game1.player == null)
            return (false, "no active save");

        if (Game1.currentLocation?.currentEvent == null && !Game1.eventUp)
            return (false, "no event is playing");

        string ended = ReleaseStuckEvent();
        return (true, $"ended the event holding the farmer ({ended})");
    }


    /// <summary>Cleans up a live or abandoned event and the input lock it holds. Returns what it did.</summary>
    private static string ReleaseStuckEvent()
    {
        var done = new List<string>();

        Event? live = Game1.currentLocation?.currentEvent;

        if (live != null)
        {
            try
            {
                live.cleanup();
                done.Add("ran the event's own cleanup");
            }
            catch (Exception ex)
            {
                done.Add($"event cleanup failed: {ex.Message}");
            }
        }

        if (Game1.eventUp)
        {
            Game1.eventUp = false;
            done.Add("cleared the event flag");
        }

        if (Game1.dialogueUp)
        {
            Game1.dialogueUp = false;
            done.Add("cleared the dialogue");
        }

        try
        {
            if (Game1.currentLocation != null)
                Game1.currentLocation.currentEvent = null;
        }
        catch { }

        try
        {
            Game1.fadeClear();
        }
        catch { }

        if (Game1.player != null)
            Game1.player.CanMove = true;

        return done.Count > 0 ? string.Join(", ", done) : "nothing was holding it";
    }


    private static BedFurniture? FindBed(GameLocation location)
    {
        try
        {
            Vector2 remembered = Game1.player?.mostRecentBed ?? Vector2.Zero;
            if (remembered != Vector2.Zero)
            {
                BedFurniture? rememberedBed = BedFurniture.GetBedAtTile(location, (int)remembered.X, (int)remembered.Y);
                if (rememberedBed != null)
                    return rememberedBed;
            }

            foreach (Furniture furniture in location.furniture)
            {
                if (furniture is BedFurniture bed)
                    return bed;
            }
        }
        catch
        {
            // fall through to "no bed"
        }

        return null;
    }


    private static (bool, string) FocusWindow()
    {
        bool focused = NativeInput.FocusGameWindow();
        return (focused, focused ? "game window focused" : "could not focus the game window");
    }


    /// <summary>
    /// Adjusts how the game is rendered: zoom level (how large/sharp the world looks) and UI
    /// scale. Deliberately never touches the window itself - preferredResolutionX/Y, fullscreen
    /// and borderless are left exactly as they are.
    /// </summary>
    private static (bool, string) SetDisplay(JsonElement root)
    {
        Options options = Game1.options;
        var applied = new List<string>();

        if (TryGetFloat(root, "zoom_level", out float zoom))
        {
            float clamped = Math.Clamp(zoom, 0.5f, 3f);
            options.desiredBaseZoomLevel = clamped;
            applied.Add($"zoom_level={clamped:0.##}");
        }

        if (TryGetFloat(root, "ui_scale", out float uiScale))
        {
            float clamped = Math.Clamp(uiScale, 0.5f, 3f);
            options.desiredUIScale = clamped;
            applied.Add($"ui_scale={clamped:0.##}");
        }

        // Resolution setting only. SDV normally resizes the window when this changes, so we write
        // the stored preference without applying it - the window keeps its current size.
        if (root.TryGetProperty("resolution", out JsonElement resolutionEl))
        {
            int? width = null;
            int? height = null;

            if (resolutionEl.ValueKind == JsonValueKind.String)
            {
                string wanted = (resolutionEl.GetString() ?? "").Trim().ToLowerInvariant();
                if (wanted is "max" or "max resolution" or "highest")
                {
                    DisplayMode mode = GraphicsAdapter.DefaultAdapter.CurrentDisplayMode;
                    width = mode.Width;
                    height = mode.Height;
                }
            }
            else if (resolutionEl.ValueKind == JsonValueKind.Array && resolutionEl.GetArrayLength() == 2)
            {
                width = resolutionEl[0].GetInt32();
                height = resolutionEl[1].GetInt32();
            }

            if (width.HasValue && height.HasValue)
            {
                options.preferredResolutionX = width.Value;
                options.preferredResolutionY = height.Value;
                applied.Add($"resolution={width}x{height} stored, window NOT resized");
            }
        }

        if (applied.Count == 0)
            return (false, "nothing to change - pass zoom_level, ui_scale and/or resolution");

        return (true, "applied " + string.Join(", ", applied) +
                      $" (now zoom={options.zoomLevel:0.##} ui={options.uiScale:0.##}, window untouched)");
    }


    private static bool TryGetFloat(JsonElement root, string name, out float value)
    {
        value = 0f;
        return root.TryGetProperty(name, out JsonElement el)
            && el.ValueKind == JsonValueKind.Number
            && el.TryGetSingle(out value);
    }


    /// <summary>
    /// Refused on purpose.
    ///
    /// A player cannot teleport, so an agent that can is playing a different game: it would skip the
    /// walking, the people met on the way, the foraging along the road, and the whole business of
    /// learning where things are. It was originally offered as an "emergency" way out of a failed
    /// path - which is precisely the moment a player would walk round instead. A path that cannot be
    /// walked is not a bug to route around, it is the map telling you something.
    /// </summary>
    private static (bool, string) Warp(JsonElement root)
    {
        return (false,
            "warp is disabled: a player cannot teleport. If a path failed, aim at an adjacent " +
            "walkable tile or choose another destination - the detour is part of the game.");
    }


    /// <summary>Clicks a button in the currently open menu, addressed by its /state name.</summary>
    private static (bool, string) MenuClick(JsonElement root)
    {
        IClickableMenu? active = Game1.activeClickableMenu;
        if (active == null)
            return (false, "no menu is open");

        EnsureWindowActive();

        // Descend into TitleMenu's submenu (e.g. LoadGameMenu) when one is open.
        IClickableMenu menu = UiStateBuilder.GetEffectiveMenu() ?? active;

        string name = root.TryGetProperty("name", out JsonElement nameEl)
            ? (nameEl.GetString() ?? "")
            : "";

        if (string.IsNullOrWhiteSpace(name))
            return (false, "missing 'name'");

        ClickableComponent? target = UiStateBuilder.FindComponent(menu, name);
        if (target == null)
        {
            List<string> available = UiStateBuilder.AvailableNames(menu);
            return (false, $"no button '{name}' in {menu.GetType().Name} " +
                           $"(available: {string.Join(", ", available.Take(25))})");
        }

        // Some menus lay buttons out lazily; a zero-sized target means it is not ready yet.
        if (target.bounds.Width <= 0 || target.bounds.Height <= 0)
            return (false, $"button '{name}' has no bounds yet (menu still animating in)");

        int cx = target.bounds.Center.X;
        int cy = target.bounds.Center.Y;

        // Where the GAME would send this click - not where the button "belongs".
        //
        // SDV dispatches a click to the deepest child of the active menu (Game1.updateActiveMenu), and
        // a shop's own receiveLeftClick is what puts a purchase down. The previous revision routed to
        // the nested InventoryMenu that OWNS a bag slot instead - whose receiveLeftClick is empty - so
        // the click was received, was logged, and nothing happened. See UiStateBuilder.ClickRouter.
        IClickableMenu router = UiStateBuilder.ClickRouter() ?? active;

        int moneyBefore = Game1.player?.Money ?? 0;
        string cursorBefore = CursorName();
        string handBefore = MenuHand(menu);
        string bagBefore = BagSignature();
        string menuBefore = MenuName();

        // THE REAL CLICK FIRST. Move the physical cursor over the button and click it, and let the game
        // do everything that follows - which menu receives it, the hover, the release, the item
        // following the cursor, and where it goes. Walking a menu's own receiveLeftClick() by hand is
        // what produced a week of shop bugs; a player does not do it and neither should this.
        bool real = NativeInput.ClickAt(cx, cy);

        int moneyAfter = Game1.player?.Money ?? 0;
        string cursorAfter = CursorName();
        string handAfter = MenuHand(menu);
        string bagAfter = BagSignature();
        string menuAfter = MenuName();

        bool moved = moneyAfter != moneyBefore
                     || !string.Equals(cursorBefore, cursorAfter, StringComparison.Ordinal)
                     || !string.Equals(handBefore, handAfter, StringComparison.Ordinal)
                     || !string.Equals(bagBefore, bagAfter, StringComparison.Ordinal)
                     || !string.Equals(menuBefore, menuAfter, StringComparison.Ordinal);

        var evidence = new List<string>();
        if (!string.Equals(menuBefore, menuAfter, StringComparison.Ordinal))
            evidence.Add($"menu {Shown(menuBefore)}->{Shown(menuAfter)}");
        if (moneyAfter != moneyBefore)
            evidence.Add($"money {moneyBefore}->{moneyAfter}");
        if (!string.Equals(cursorBefore, cursorAfter, StringComparison.Ordinal))
            evidence.Add($"cursor {cursorBefore}->{cursorAfter}");
        if (!string.Equals(handBefore, handAfter, StringComparison.Ordinal))
            evidence.Add($"menu hand {Shown(handBefore)}->{Shown(handAfter)}");
        if (!string.Equals(bagBefore, bagAfter, StringComparison.Ordinal))
            evidence.Add($"bag {bagBefore}->{bagAfter}");

        if (real && moved)
        {
            string viaReal = moneyAfter != moneyBefore
                             || !string.Equals(cursorBefore, cursorAfter, StringComparison.Ordinal)
                             || !string.Equals(bagBefore, bagAfter, StringComparison.Ordinal)
                ? ""
                : " (the hand changed but no money did - read it before trusting this)";

            return (true, $"clicked '{name}' at ({cx},{cy}) with the mouse in {menu.GetType().Name} "
                          + $"[{string.Join(", ", evidence)}]{viaReal}");
        }

        // Either the machine could not move the cursor, or the game did nothing with the click. The old
        // path is kept as a fallback - it is a worse instrument, but it is not nothing. The mouse the
        // GAME sees is reported either way: if it disagrees with the point we aimed at, that mismatch
        // IS the diagnosis, and it took a week to find the last one by other means.
        // "Nothing changed" is a verdict about the WORLD, not about the click - and conflating the two
        // cost three rounds of chasing a click that was landing all along. The cursor is moved to the
        // button's exact screen point (the OS says so, and the game follows the physical mouse), and the
        // game is then entitled to refuse: a shop will not take a purchase it has nowhere to put, and a
        // confirm button with an empty field does nothing. Those are correct answers, not failures of
        // the mouse. The mouse readings that were printed here as "evidence" were a bad probe; what is
        // kept is the arithmetic, which is checkable.
        string why = real
            ? "the click was delivered - the game did not react, which may be its own correct answer "
              + $"(nothing free to take it, nothing to confirm). {NativeInput.LastClickReport()}"
            : "the cursor could not be moved";

        // A real click is hover -> press -> release, and the press half is the old shape too: a shop row
        // that is picked up and never put down was one missing release, with four symptoms.
        try { router.performHoverAction(cx, cy); } catch { /* not every menu implements it */ }
        router.receiveLeftClick(cx, cy, true);

        int moneyPressed = Game1.player?.Money ?? 0;
        string cursorPressed = CursorName();
        string handPressed = MenuHand(menu);
        string bagPressed = BagSignature();
        string menuPressed = MenuName();

        try { router.releaseLeftClick(cx, cy); } catch { }

        int moneyFinal = Game1.player?.Money ?? 0;
        string cursorFinal = CursorName();
        string handFinal = MenuHand(menu);
        string bagFinal = BagSignature();
        string menuFinal = MenuName();

        if (!string.Equals(menuPressed, menuFinal, StringComparison.Ordinal))
            evidence.Add($"menu {Shown(menuPressed)}->{Shown(menuFinal)}");
        if (moneyPressed != moneyAfter)
            evidence.Add($"press: money {moneyAfter}->{moneyPressed}");
        if (!string.Equals(cursorAfter, cursorPressed, StringComparison.Ordinal))
            evidence.Add($"press: cursor {cursorAfter}->{cursorPressed}");
        if (!string.Equals(handAfter, handPressed, StringComparison.Ordinal))
            evidence.Add($"press: menu hand {Shown(handAfter)}->{Shown(handPressed)}");
        if (!string.Equals(bagAfter, bagPressed, StringComparison.Ordinal))
            evidence.Add($"press: bag {bagAfter}->{bagPressed}");
        if (moneyFinal != moneyPressed)
            evidence.Add($"release: money {moneyPressed}->{moneyFinal}");
        if (!string.Equals(cursorPressed, cursorFinal, StringComparison.Ordinal))
            evidence.Add($"release: cursor {cursorPressed}->{cursorFinal}");
        if (!string.Equals(handPressed, handFinal, StringComparison.Ordinal))
            evidence.Add($"release: menu hand {Shown(handPressed)}->{Shown(handFinal)}");
        if (!string.Equals(bagPressed, bagFinal, StringComparison.Ordinal))
            evidence.Add($"release: bag {bagPressed}->{bagFinal}");

        string via = ReferenceEquals(menu, active) ? "" : $" (via {active.GetType().Name})";
        string proof = evidence.Count > 0
            ? $" [{string.Join(", ", evidence)}]"
            : " [the whole click changed nothing - not money, not the bag, not either hand]";

        return (true, $"clicked '{name}' at ({cx},{cy}) in {menu.GetType().Name}{via}"
                      + $" - {why}, so it was done the old way{proof}");
    }


    /// <summary>
    /// Which menu is open right now, for the click witness.
    ///
    /// A click on a CLOSE button changes money nothing, the bag nothing and the hands nothing - it
    /// changes the MENU, which the witness was not watching. So every successful close was reported as
    /// "the whole click changed nothing", and the streamer was told its working clicks had failed. It is
    /// the same blindness the bag had, in the one place left: the thing the click was aimed at.
    /// </summary>
    private static string MenuName()
    {
        try { return Game1.activeClickableMenu?.GetType().Name ?? ""; }
        catch { return "?"; }
    }


    /// <summary>What sits on the cursor right now, for the click witness.</summary>
    private static string CursorName()
    {
        try
        {
            Item? held = Game1.player?.CursorSlotItem;
            return held == null ? "empty" : $"{held.DisplayName} x{held.Stack}";
        }
        catch { return "?"; }
    }


    /// <summary>The item the MENU is holding, for the click witness. Empty string when it holds nothing.</summary>
    private static string MenuHand(IClickableMenu? menu)
    {
        try { return UiStateBuilder.DescribeHeld(menu); }
        catch { return ""; }
    }


    private static string Shown(string hand) => string.IsNullOrEmpty(hand) ? "empty" : hand;


    /// <summary>
    /// How full the bag is, for the click witness.
    ///
    /// Placing a purchase changes the bag and NOTHING else - not money, not the cursor, not the screen.
    /// A witness that watched only money and the cursor therefore reported the click that finally
    /// worked as "the whole click changed nothing", and the model believed it and kept hunting for the
    /// click that would put the seeds down. Counting slots and stacks is enough to see it: a purchase
    /// that lands moves both, and a click that dies moves neither.
    /// </summary>
    private static string BagSignature()
    {
        try
        {
            Farmer? farmer = Game1.player;
            if (farmer == null)
                return "? (no player)";

            int used = 0;
            int stacks = 0;
            foreach (Item? item in farmer.Items)
            {
                if (item == null)
                    continue;
                used++;
                stacks += item.Stack;
            }

            return $"{used}/{farmer.Items.Count} slots, {stacks} items";
        }
        catch { return "?"; }
    }


    /// <summary>
    /// Reflects over a LIVE game object and hands back its fields.
    ///
    /// This is the bridge's answer to a question nobody anticipated. Every other field in /state is
    /// something a person thought to expose - which is fine until the cause of a failure lives in a
    /// field nobody exposed, and then no agent can form the hypothesis, let alone test it. That is
    /// how "the shop will not close" and "the seeds never arrived" both stayed mysterious: the item
    /// was on the cursor, and there was no cursor in the payload.
    ///
    /// `game_data` already does this for the game's static data tables. This does it for the objects
    /// that are running - the difference between reading the manual and looking under the hood.
    /// </summary>
    private static (bool, string) Inspect(JsonElement root)
    {
        string target = (root.TryGetProperty("target", out JsonElement t) ? (t.GetString() ?? "") : "")
            .ToLowerInvariant();
        string filter = root.TryGetProperty("contains", out JsonElement c) ? (c.GetString() ?? "") : "";
        int limit = root.TryGetProperty("limit", out JsonElement l) && l.TryGetInt32(out int n)
            ? Math.Clamp(n, 1, 150)
            : 50;

        object? obj;
        switch (target)
        {
            case "player":
            case "farmer":
                obj = Game1.player;
                break;
            case "menu":
                obj = UiStateBuilder.GetEffectiveMenu();
                break;
            case "location":
                obj = Game1.currentLocation;
                break;
            case "game":
                obj = Game1.game1;
                break;
            default:
                return (false, $"unknown target '{target}' (try: player, menu, location, game)");
        }

        if (obj == null)
            return (false, $"nothing to inspect for '{target}' right now");

        var parts = new List<string>();
        Type? type = obj.GetType();
        while (type != null && type != typeof(object))
        {
            FieldInfo[] fields;
            try { fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { fields = Array.Empty<FieldInfo>(); }

            foreach (FieldInfo field in fields)
            {
                if (parts.Count >= limit)
                    break;
                if (filter.Length > 0 && field.Name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;

                object? value;
                try { value = field.GetValue(obj); }
                catch { continue; }

                parts.Add($"{field.Name}:{ShortType(field.FieldType)}={ShortValue(value)}");
            }

            PropertyInfo[] properties;
            try { properties = type.GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { properties = Array.Empty<PropertyInfo>(); }

            foreach (PropertyInfo property in properties)
            {
                if (parts.Count >= limit)
                    break;
                if (filter.Length > 0 && property.Name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                if (property.GetIndexParameters().Length > 0)
                    continue;                       // an indexer is not a readable field

                object? value;
                try { value = property.GetValue(obj); }
                catch { continue; }

                parts.Add($"{property.Name}:{ShortType(property.PropertyType)}={ShortValue(value)}");
            }

            type = type.BaseType;
        }

        if (parts.Count == 0)
            return (false, $"no field of {obj.GetType().Name} matches '{filter}'");

        string joined = string.Join("; ", parts);
        if (joined.Length > 1800)
            joined = joined[..1800] + " …";
        return (true, $"{obj.GetType().Name}{(filter.Length > 0 ? $" (contains '{filter}')" : "")}: {joined}");
    }


    /// <summary>
    /// Opens a screen the way the game does when the player presses the key - by building the very
    /// menu the game builds - rather than by handing the agent an answer it did not go and get.
    ///
    /// It also closes a hole that had been open for weeks: the ledger declared "open the map (M)" as
    /// a thing a player can do, and the bridge had no way to do it, so the fix for the `game_data`
    /// defect ("make reading the world map require the map open") could never be applied.
    /// </summary>
    private static (bool, string) OpenScreen(JsonElement root)
    {
        EnsureWindowActive();

        if (Game1.player == null)
            return (false, "no active save");

        string screen = (root.TryGetProperty("screen", out JsonElement s) ? (s.GetString() ?? "") : "")
            .ToLowerInvariant();

        (bool free, string why) = RequireFreePlayer();
        if (!free)
            return (false, why);

        var menu = new GameMenu();
        Game1.activeClickableMenu = menu;

        if (screen is "" or "bag" or "inventory" or "menu")
            return (true, "opened the bag/pause menu (GameMenu) - exactly what pressing the key does");

        if (screen == "map")
        {
            int page = FindPage(menu, "Map");
            if (page < 0)
                return (false, "opened the pause menu but found no Map page inside it");
            if (!ChangeTab(menu, page))
                return (false, $"found the Map page at index {page} but could not switch to it");
            return (true, $"opened the map (GameMenu page {page})");
        }

        return (false, $"unknown screen '{screen}' (try: map, bag)");
    }


    /// <summary>Finds a GameMenu page by its type name, so no tab index is hardcoded.</summary>
    private static int FindPage(GameMenu menu, string nameToken)
    {
        object? pages = ReadField<object>(menu, "pages");
        if (pages is not System.Collections.IEnumerable sequence)
            return -1;

        int index = 0;
        foreach (object? page in sequence)
        {
            string type = page?.GetType().Name ?? "";
            if (type.IndexOf(nameToken, StringComparison.OrdinalIgnoreCase) >= 0)
                return index;
            index++;
        }

        return -1;
    }


    private static bool ChangeTab(GameMenu menu, int index)
    {
        foreach (string name in new[] { "setTab", "changeTab" })
        {
            try
            {
                MethodInfo? method = menu.GetType().GetMethod(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                ParameterInfo[] ps = method?.GetParameters() ?? Array.Empty<ParameterInfo>();
                if (method != null && ps.Length == 1 && ps[0].ParameterType == typeof(int))
                {
                    method.Invoke(menu, new object[] { index });
                    return true;
                }
            }
            catch { /* try the next spelling */ }
        }

        try
        {
            FieldInfo? field = menu.GetType().GetField("currentTab",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (field != null)
            {
                field.SetValue(menu, index);
                return true;
            }
        }
        catch { }

        return false;
    }


    private static string ShortType(Type type)
    {
        if (!type.IsGenericType)
            return type.Name;

        string args = string.Join(",", type.GetGenericArguments().Select(a => a.Name));
        return $"{type.Name.Split('`')[0]}<{args}>";
    }


    private static string ShortValue(object? value)
    {
        if (value == null)
            return "null";

        try
        {
            if (value is string text)
                return text.Length > 40 ? $"\"{text[..40]}…\"" : $"\"{text}\"";

            if (value is System.Collections.ICollection collection)
                return $"[{collection.Count}]";

            if (value is StardewValley.ISalable salable)
                return $"{salable.DisplayName} x{salable.Stack}";

            Type type = value.GetType();
            if (type.IsPrimitive || value is decimal)
                return value.ToString() ?? "";

            return type.Name;
        }
        catch { return "?"; }
    }


    /// <summary>Title-menu specific shortcut: invokes performButtonAction(which) directly.</summary>
    private static (bool, string) TitleButton(JsonElement root)
    {
        if (Game1.activeClickableMenu is not TitleMenu title)
            return (false, "no TitleMenu open");

        string which = root.TryGetProperty("which", out JsonElement whichEl)
            ? (whichEl.GetString() ?? "")
            : "";

        if (string.IsNullOrWhiteSpace(which))
            return (false, "missing 'which' (e.g. Load, New, Coop, Options, Exit)");

        title.performButtonAction(which);
        return (true, $"title button '{which}' invoked");
    }


    /// <summary>Skips the title-screen intro animation so buttons become clickable.</summary>
    private static (bool, string) SkipIntro()
    {
        if (Game1.activeClickableMenu is not TitleMenu title)
            return (false, "no TitleMenu open");

        title.skipToTitleButtons();
        return (true, "title intro skipped");
    }


    /// <summary>
    /// Creates a brand new save: opens the character creation screen, fills in the name boxes,
    /// picks a farm type and confirms. Used so the agent plays its own farm, never the player's.
    /// </summary>
    private static (bool, string) NewGame(JsonElement root)
    {
        string name = GetString(root, "name");
        string farmName = GetString(root, "farm_name");
        string favoriteThing = GetString(root, "favorite_thing");
        string farmType = GetString(root, "farm_type");

        bool skipIntro = !root.TryGetProperty("skip_intro", out JsonElement skipEl)
                      || skipEl.ValueKind != JsonValueKind.False;

        // Open character creation from the title screen if it is not open yet. TitleMenu only
        // reveals the submenu on the following tick, so remember the request and finish it then.
        IClickableMenu? menu = UiStateBuilder.GetEffectiveMenu();
        if (menu is TitleMenu title)
        {
            _pendingNewGameJson = root.GetRawText();
            _pendingNewGameTicks = 0;
            title.performButtonAction("New");
            return (true, "character creation opened; it will be filled in and confirmed automatically");
        }

        if (menu is not CharacterCustomization customization)
            return (false, $"character creation is not open (current menu: {menu?.GetType().Name ?? "none"})");

        // nameBox / farmnameBox / favThingBox / skipIntro are private, so reach them reflectively.
        TextBox? nameBox = ReadField<TextBox>(customization, "nameBox");
        TextBox? farmBox = ReadField<TextBox>(customization, "farmnameBox");
        TextBox? favBox = ReadField<TextBox>(customization, "favThingBox");

        if (nameBox == null)
            return (false, "could not read the name box from the character creation menu");

        if (!string.IsNullOrWhiteSpace(name))
            nameBox.Text = ClampField(name);

        if (!string.IsNullOrWhiteSpace(farmName) && farmBox != null)
            farmBox.Text = ClampField(farmName);

        if (!string.IsNullOrWhiteSpace(favoriteThing) && favBox != null)
            favBox.Text = ClampField(favoriteThing);

        WriteField(customization, "skipIntro", skipIntro);

        // Optional appearance randomisation: the streamer AI chooses how many times to roll.
        int randomClicks = 0;
        if (root.TryGetProperty("random_appearance_clicks", out JsonElement randomEl)
            && randomEl.TryGetInt32(out int randomValue))
        {
            randomClicks = Math.Clamp(randomValue, 0, 20);
        }

        ClickableTextureComponent? randomButton = ReadField<ClickableTextureComponent>(customization, "randomButton");
        for (int i = 0; i < randomClicks && randomButton != null && randomButton.bounds.Width > 0; i++)
        {
            ClickComponent(customization, randomButton);
        }

        string gender = GetString(root, "gender").ToLowerInvariant();
        string genderNote = gender switch
        {
            "male" or "m" or "man" or "boy" => ClickGender(customization, 0),
            "female" or "f" or "woman" or "girl" => ClickGender(customization, 1),
            _ => "",
        };

        string typeNote = "default";
        if (!string.IsNullOrWhiteSpace(farmType))
        {
            int index = -1;
            if (int.TryParse(farmType, out int parsed))
            {
                index = parsed;
            }
            else
            {
                List<ClickableTextureComponent> types = customization.farmTypeButtons;
                for (int i = 0; i < types.Count; i++)
                {
                    if (string.Equals(types[i]?.name, farmType, StringComparison.OrdinalIgnoreCase))
                    {
                        index = i;
                        break;
                    }
                }
            }

            if (index >= 0 && customization.farmTypeButtons != null && index < customization.farmTypeButtons.Count)
            {
                ClickableTextureComponent button = customization.farmTypeButtons[index];
                ClickComponent(customization, button);
                typeNote = button.name;
            }
            else
            {
                typeNote = $"'{farmType}' not found, kept default";
            }
        }

        ClickableTextureComponent ok = customization.okButton;
        if (ok == null || ok.bounds.Width <= 0)
            return (false, "OK button is not ready yet");

        // Confirming must wait one tick so the menu has applied the edits above.
        QueueClick(customization, ok);

        return (true, $"new game started: name='{nameBox.Text}' farm='{farmBox?.Text}' " +                      $"type={typeNote}{(genderNote.Length > 0 ? $" gender={genderNote}" : "")} " +
                      $"appearance_rolls={randomClicks} skip_intro={skipIntro}");
    }


    /// <summary>genderButtons[0] is male, [1] is female.</summary>
    private static string ClickGender(CharacterCustomization customization, int index)
    {
        List<ClickableComponent>? buttons = ReadField<List<ClickableComponent>>(customization, "genderButtons");
        if (buttons == null || index < 0 || index >= buttons.Count || buttons[index] == null)
            return "";

        ClickableComponent button = buttons[index];
        ClickComponent(customization, button);
        return index == 0 ? "male" : "female";
    }


    /// <summary>
    /// Clicks a component owned by <paramref name="owner"/>.
    ///
    /// SDV 1.6 keeps TitleMenu submenus (LoadGameMenu, CharacterCustomization, ...) in a static
    /// field while Game1.activeClickableMenu stays the TitleMenu. Clicks must be delivered to the
    /// top-level menu, which forwards them into the submenu; calling the submenu directly is
    /// silently ignored.
    /// </summary>
    private static void ClickComponent(IClickableMenu owner, ClickableComponent component)
    {
        IClickableMenu? active = Game1.activeClickableMenu;
        IClickableMenu receiver = active != null && !ReferenceEquals(owner, active) ? active : owner;
        receiver.receiveLeftClick(component.bounds.Center.X, component.bounds.Center.Y, true);
    }


    private static readonly List<(IClickableMenu Owner, ClickableComponent Component)> PendingClicks = new();

    /// <summary>
    /// A create_character request that arrived while the character creation screen was still
    /// opening. Replayed once the menu exists; cleared after too many ticks.
    /// </summary>
    private static string? _pendingNewGameJson;
    private static int _pendingNewGameTicks;

    private const int PendingNewGameTimeoutTicks = 180;


    /// <summary>
    /// Finishes a create_character request that had to wait for the character creation screen.
    /// Called once per tick from UpdateTicked.
    /// </summary>
    internal static void ProcessPendingNewGame(IMonitor monitor)
    {
        if (_pendingNewGameJson == null)
            return;

        if (UiStateBuilder.GetEffectiveMenu() is not CharacterCustomization)
        {
            if (++_pendingNewGameTicks > PendingNewGameTimeoutTicks)
            {
                monitor.Log("deferred create_character gave up: character creation never opened", LogLevel.Warn);
                _pendingNewGameJson = null;
            }
            return;
        }

        string request = _pendingNewGameJson;
        _pendingNewGameJson = null;
        _pendingNewGameTicks = 0;

        (bool ok, string detail, _) = Execute(request, actionsEnabled: true, monitor);
        monitor.Log($"deferred create_character: ok={ok} | {detail}", LogLevel.Info);
    }

    /// <summary>
    /// Queues a click for the next game tick.
    ///
    /// Character creation edits (name boxes, gender, farm type) only become valid once the menu
    /// has run its own update, so confirming in the same tick the fields were written is ignored
    /// by the game. Everything else can be clicked immediately.
    /// </summary>
    private static void QueueClick(IClickableMenu owner, ClickableComponent component)
    {
        PendingClicks.Add((owner, component));
    }


    /// <summary>Runs clicks queued on the previous tick. Called from UpdateTicked before task processing.</summary>
    internal static void ProcessPendingClicks(IMonitor monitor)
    {
        if (PendingClicks.Count == 0)
            return;

        var due = PendingClicks.ToArray();
        PendingClicks.Clear();

        foreach ((IClickableMenu owner, ClickableComponent component) in due)
        {
            try
            {
                ClickComponent(owner, component);
            }
            catch (Exception ex)
            {
                monitor.Log($"queued click failed: {ex.Message}", LogLevel.Warn);
            }
        }
    }


    /// <summary>Reads a private/protected instance field by name, walking up the type hierarchy.</summary>
    private static T? ReadField<T>(object target, string fieldName) where T : class
    {
        for (Type? type = target.GetType(); type != null; type = type.BaseType)
        {
            FieldInfo? field = type.GetField(fieldName, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (field != null)
                return field.GetValue(target) as T;
        }
        return null;
    }


    /// <summary>Writes a private/protected instance field by name, walking up the type hierarchy.</summary>
    private static void WriteField(object target, string fieldName, object value)
    {
        for (Type? type = target.GetType(); type != null; type = type.BaseType)
        {
            FieldInfo? field = type.GetField(fieldName, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (field != null)
            {
                field.SetValue(target, value);
                return;
            }
        }
    }


    /// <summary>Sets one of the character-creation text boxes.</summary>
    private static (bool, string) SetText(JsonElement root)
    {
        if (UiStateBuilder.GetEffectiveMenu() is not CharacterCustomization customization)
            return (false, "no character creation menu open");

        string field = GetString(root, "field").ToLowerInvariant();
        string value = GetString(root, "value");

        TextBox? box = field switch
        {
            "name" or "character_name" => ReadField<TextBox>(customization, "nameBox"),
            "farm_name" or "farm" => ReadField<TextBox>(customization, "farmnameBox"),
            "favorite_thing" or "favorite" or "fav" => ReadField<TextBox>(customization, "favThingBox"),
            _ => null,
        };

        if (box == null)
            return (false, $"unknown field '{field}' (use name, farm_name, favorite_thing)");

        box.Text = ClampField(value);
        return (true, $"{field} = '{box.Text}'");
    }


    /// <summary>
    /// Adjusts the look of the character being designed. The streamer AI picks these values;
    /// they are applied to the menu's display farmer, which is what character creation saves.
    /// </summary>
    private static (bool, string) SetAppearance(JsonElement root)
    {
        if (UiStateBuilder.GetEffectiveMenu() is not CharacterCustomization customization)
            return (false, "no character creation menu open");

        Farmer? farmer;
        try { farmer = customization.GetOrCreateDisplayFarmer(); }
        catch (Exception ex) { return (false, $"could not get the display farmer: {ex.Message}"); }

        if (farmer == null)
            return (false, "the display farmer is not available yet");

        var applied = new List<string>();

        if (TryGetText(root, "gender", out string gender))
        {
            string g = gender.ToLowerInvariant();
            if (g.StartsWith('m')) { farmer.changeGender(true); applied.Add("gender=male"); }
            else if (g.StartsWith('f')) { farmer.changeGender(false); applied.Add("gender=female"); }
        }

        if (TryGetInt(root, "skin", out int skin)) { farmer.changeSkinColor(skin, true); applied.Add($"skin={skin}"); }
        if (TryGetInt(root, "hair_style", out int hair)) { farmer.changeHairStyle(hair); applied.Add($"hair_style={hair}"); }
        if (TryGetInt(root, "accessory", out int accessory)) { farmer.changeAccessory(accessory); applied.Add($"accessory={accessory}"); }

        if (TryGetText(root, "shirt", out string shirt)) { farmer.changeShirt(shirt); applied.Add($"shirt={shirt}"); }
        if (TryGetText(root, "pants", out string pants)) { farmer.changePantStyle(pants); applied.Add($"pants={pants}"); }

        if (TryGetColor(root, "hair_color", out Color hairColor)) { farmer.changeHairColor(hairColor); applied.Add($"hair_color={Rgb(hairColor)}"); }
        if (TryGetColor(root, "eye_color", out Color eyeColor)) { farmer.changeEyeColor(eyeColor); applied.Add($"eye_color={Rgb(eyeColor)}"); }
        if (TryGetColor(root, "pants_color", out Color pantsColor)) { farmer.changePantsColor(pantsColor); applied.Add($"pants_color={Rgb(pantsColor)}"); }

        return (true, applied.Count == 0
            ? "no appearance fields supplied"
            : "applied: " + string.Join(", ", applied));
    }


    private static string Rgb(Color c) => $"{c.R},{c.G},{c.B}";


    private static bool TryGetInt(JsonElement root, string name, out int value)
    {
        value = 0;
        return root.TryGetProperty(name, out JsonElement el)
            && el.ValueKind == JsonValueKind.Number
            && el.TryGetInt32(out value);
    }


    private static bool TryGetText(JsonElement root, string name, out string value)
    {
        value = "";
        if (root.TryGetProperty(name, out JsonElement el) && el.ValueKind == JsonValueKind.String)
        {
            value = el.GetString() ?? "";
            return value.Length > 0;
        }
        return false;
    }


    /// <summary>Accepts [r,g,b] or "r,g,b".</summary>
    private static bool TryGetColor(JsonElement root, string name, out Color color)
    {
        color = Color.White;

        if (!root.TryGetProperty(name, out JsonElement el))
            return false;

        if (el.ValueKind == JsonValueKind.Array && el.GetArrayLength() == 3)
        {
            color = new Color(
                Math.Clamp(el[0].GetInt32(), 0, 255),
                Math.Clamp(el[1].GetInt32(), 0, 255),
                Math.Clamp(el[2].GetInt32(), 0, 255));
            return true;
        }

        if (el.ValueKind == JsonValueKind.String)
        {
            string[] parts = (el.GetString() ?? "").Split(',');
            if (parts.Length == 3
                && byte.TryParse(parts[0], out byte r)
                && byte.TryParse(parts[1], out byte g)
                && byte.TryParse(parts[2], out byte b))
            {
                color = new Color(r, g, b);
                return true;
            }
        }

        return false;
    }


    /// <summary>Character names are capped in the UI; keep programmatic input inside the same limit.</summary>
    private static string ClampField(string value)
    {
        string trimmed = value.Trim();
        return trimmed.Length <= 15 ? trimmed : trimmed[..15];
    }


    private static string GetString(JsonElement root, string property)
    {
        return root.TryGetProperty(property, out JsonElement el) && el.ValueKind == JsonValueKind.String
            ? el.GetString() ?? ""
            : "";
    }


    /// <summary>Selects one of the responses on a question dialogue.</summary>
    private static (bool, string) DialogueChoose(JsonElement root)
    {
        if (Game1.activeClickableMenu is not DialogueBox box)
            return (false, "no dialogue box open");

        EnsureWindowActive();

        if (!root.TryGetProperty("index", out JsonElement indexEl))
            return (false, "missing 'index'");

        int index = indexEl.GetInt32();
        Response[]? responses = box.responses;
        if (responses == null || responses.Length == 0)
            return (false, "this dialogue has no responses to choose");

        if (index < 0 || index >= responses.Length)
            return (false, $"index {index} out of range (0..{responses.Length - 1})");

        box.selectedResponse = index;

        // Prefer clicking the response's own component; fall back to confirming with Enter.
        List<ClickableComponent>? responseComponents = box.responseCC;
        if (responseComponents != null && index < responseComponents.Count && responseComponents[index] != null)
        {
            int cx = responseComponents[index].bounds.Center.X;
            int cy = responseComponents[index].bounds.Center.Y;
            box.receiveLeftClick(cx, cy, true);
        }
        else
        {
            box.receiveKeyPress(Microsoft.Xna.Framework.Input.Keys.Enter);
        }

        return (true, $"chose response {index}: {responses[index].responseText}");
    }


    /// <summary>Advances or closes a non-question dialogue.</summary>
    private static (bool, string) DialogueAdvance()
    {
        if (Game1.activeClickableMenu is not DialogueBox box)
            return (false, "no dialogue box open");

        EnsureWindowActive();

        if (box.responses is { Length: > 0 })
            return (false, "dialogue is waiting for a choice; use dialogue_choose {index}");

        box.receiveKeyPress(Microsoft.Xna.Framework.Input.Keys.Enter);

        // If the key press did not move it along, click inside the box.
        if (ReferenceEquals(Game1.activeClickableMenu, box))
        {
            int cx = box.x + box.width / 2;
            int cy = box.y + box.height - 24;
            box.receiveLeftClick(cx, cy, true);
        }

        return (true, "dialogue advanced");
    }


    private static (bool, string) RequireFreePlayer()
    {
        if (Game1.player == null)
            return (false, "no active save");
        if (GameQuery.IsPlayerFree())
            return (true, "");

        return (false, $"player not free: {GameQuery.BlockedReason()}");
    }
}


/// <summary>
/// Steps the farmer every game tick while a move action is active.
/// We set the movement direction and let the farmer's own movement code run one step,
/// so collision, water and animation all behave normally.
/// </summary>
public static class MovementController
{
    private static bool _active;
    private static long _stopAtTick;
    private static int _direction; // 0=up 1=right 2=down 3=left

    public static bool IsActive => _active;

    public static void Start(string direction, int durationMs)
    {
        _direction = direction switch
        {
            "up" => 0,
            "right" => 1,
            "down" => 2,
            "left" => 3,
            _ => 2,
        };

        long now = Game1.currentGameTime?.TotalGameTime.Ticks ?? 0;
        _stopAtTick = now + (durationMs * TimeSpan.TicksPerMillisecond);
        _active = true;
    }

    public static void Stop()
    {
        _active = false;
        ClearFlags();
    }

    public static void OnUpdateTicked()
    {
        if (!_active)
            return;

        if (!GameQuery.IsPlayerFree())
        {
            Stop();
            return;
        }

        long now = Game1.currentGameTime?.TotalGameTime.Ticks ?? 0;
        if (now >= _stopAtTick)
        {
            Stop();
            return;
        }

        Farmer f = Game1.player;
        GameTime? time = Game1.currentGameTime;
        GameLocation? loc = Game1.currentLocation;
        if (f == null || time == null || loc == null)
        {
            Stop();
            return;
        }

        SetDirection(f, true);
        try
        {
            f.MovePosition(time, Game1.viewport, loc);
        }
        finally
        {
            ClearFlags(f);
        }
    }

    private static void SetDirection(Farmer f, bool value)
    {
        switch (_direction)
        {
            case 0: f.SetMovingUp(value); break;
            case 1: f.SetMovingRight(value); break;
            case 2: f.SetMovingDown(value); break;
            case 3: f.SetMovingLeft(value); break;
        }
    }

    private static void ClearFlags(Farmer f)
    {
        f.SetMovingUp(false);
        f.SetMovingRight(false);
        f.SetMovingDown(false);
        f.SetMovingLeft(false);
    }

    private static void ClearFlags()
    {
        if (Game1.player != null)
            ClearFlags(Game1.player);
    }
}


/// <summary>Maps friendly key names to Win32 virtual-key codes and injects them.</summary>
public static class NativeInput
{
    private const uint KEYEVENTF_KEYUP = 0x0002;

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    private static readonly Dictionary<string, byte> KeyMap = new(StringComparer.OrdinalIgnoreCase)
    {
        ["escape"] = 0x1B, ["esc"] = 0x1B,
        ["space"] = 0x20,
        ["enter"] = 0x0D, ["return"] = 0x0D,
        ["tab"] = 0x09,
        ["backspace"] = 0x08,
        ["shift"] = 0x10,
        ["control"] = 0x11, ["ctrl"] = 0x11,
        ["up"] = 0x26, ["down"] = 0x28, ["left"] = 0x25, ["right"] = 0x27,
        ["e"] = 0x45, ["f"] = 0x46, ["q"] = 0x51, ["r"] = 0x52, ["w"] = 0x57,
        ["a"] = 0x41, ["s"] = 0x53, ["d"] = 0x44, ["c"] = 0x43, ["b"] = 0x42,
        ["y"] = 0x59, ["n"] = 0x4E, ["m"] = 0x4D, ["i"] = 0x49, ["j"] = 0x4A,
        ["k"] = 0x4B, ["l"] = 0x4C, ["o"] = 0x4F, ["p"] = 0x50, ["t"] = 0x54,
        ["u"] = 0x55, ["v"] = 0x56, ["x"] = 0x58, ["g"] = 0x47, ["h"] = 0x48,
        ["1"] = 0x31, ["2"] = 0x32, ["3"] = 0x33, ["4"] = 0x34, ["5"] = 0x35,
        ["6"] = 0x36, ["7"] = 0x37, ["8"] = 0x38, ["9"] = 0x39, ["0"] = 0x30,
    };

    public static bool TryGetVirtualKey(string key, out byte vk) => KeyMap.TryGetValue(key, out vk);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetForegroundWindow();

    /// <summary>Whether the game window is the one the user's input would go to.</summary>
    public static bool IsGameWindowActive()
    {
        IntPtr handle = GameWindowHandle();
        return handle != IntPtr.Zero && GetForegroundWindow() == handle;
    }

    private const int SW_MAXIMIZE = 3;
    private const int SW_RESTORE = 9;

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private const uint WM_SYSCOMMAND = 0x0112;
    private const int SC_MAXIMIZE = 0xF030;
    private const int GWL_STYLE = -16;
    private const long WS_THICKFRAME = 0x00040000L;
    private const long WS_MAXIMIZEBOX = 0x00010000L;

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtrW(IntPtr hWnd, int nIndex);

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern IntPtr GetWindowLongW(IntPtr hWnd, int nIndex);

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtrW(IntPtr hWnd, int nIndex, IntPtr value);

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern IntPtr SetWindowLongW(IntPtr hWnd, int nIndex, IntPtr value);

    private static IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex)
        => IntPtr.Size == 8 ? GetWindowLongPtrW(hWnd, nIndex) : GetWindowLongW(hWnd, nIndex);

    private static IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr value)
        => IntPtr.Size == 8 ? SetWindowLongPtrW(hWnd, nIndex, value) : SetWindowLongW(hWnd, nIndex, value);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsZoomed(IntPtr hWnd);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsIconic(IntPtr hWnd);

    private const uint SPI_GETWORKAREA = 0x0030;
    private const uint MONITORINFOF_PRIMARY = 0x0001;

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool SystemParametersInfo(uint action, uint param, ref RECT rect, uint winIni);

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr hWnd, out RECT rect);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct MONITORINFO
    {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
    }

    private delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc callback, IntPtr data);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO info);

    private static RECT _bestArea;
    private static bool _haveBestArea;

    private static bool PickMonitor(IntPtr monitor, IntPtr hdc, ref RECT rect, IntPtr data)
    {
        var info = new MONITORINFO();
        info.cbSize = System.Runtime.InteropServices.Marshal.SizeOf<MONITORINFO>();

        if (!GetMonitorInfo(monitor, ref info))
            return true;

        bool isPrimary = (info.dwFlags & MONITORINFOF_PRIMARY) != 0;

        // The second screen is where the game belongs: the first one is where the person is working.
        // (Tried forcing the primary while chasing the mouse; the person's requirement is the secondary,
        // so the window stays here and the input problem is solved where it actually is.)
        if (!_haveBestArea || !isPrimary)
        {
            _bestArea = info.rcWork;
            _haveBestArea = true;
        }

        return true;
    }

    /// <summary>The work area of the screen the game should fill - the secondary one if there is one.</summary>
    private static bool PreferredWorkArea(out RECT area)
    {
        _haveBestArea = false;
        _bestArea = default;

        try
        {
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, PickMonitor, IntPtr.Zero);
        }
        catch { }

        area = _bestArea;
        return _haveBestArea;
    }

    /// <summary>
    /// Makes the game a window that fills the work area of the second screen - a window, with its
    /// title bar, not a fullscreen takeover.
    ///
    /// Two things this gets right that the naive version did not. First, it does not fight the
    /// window: Stardew re-fits and re-positions its own window during start-up, so resizing it from
    /// outside is undone moments later - telling Stardew what size it wants is the only thing that
    /// sticks. Second, `preferredResolution` is the *client* area, so asking for the whole work area
    /// gives a window that is a title bar and a border larger than the screen. The decoration is
    /// measured and subtracted.
    /// </summary>
    public static bool FillWorkingArea()
    {
        try
        {
            if (!PreferredWorkArea(out RECT area) || area.Right <= area.Left || area.Bottom <= area.Top)
            {
                var mode = Microsoft.Xna.Framework.Graphics.GraphicsAdapter.DefaultAdapter.CurrentDisplayMode;
                area = new RECT { Left = 0, Top = 0, Right = mode.Width, Bottom = mode.Height };
            }

            int outerWidth = area.Right - area.Left;
            int outerHeight = area.Bottom - area.Top;

            // How much of the window is not game: title bar plus borders. Measured rather than guessed.
            int decorX = 0, decorY = 0;
            IntPtr handle = GameWindowHandle();
            if (handle != IntPtr.Zero && GetClientRect(handle, out RECT client) && GetWindowRect(handle, out RECT outer))
            {
                decorX = Math.Max(0, (outer.Right - outer.Left) - (client.Right - client.Left));
                decorY = Math.Max(0, (outer.Bottom - outer.Top) - (client.Bottom - client.Top));
            }

            int clientWidth = Math.Max(640, outerWidth - decorX);
            int clientHeight = Math.Max(480, outerHeight - decorY);

            Game1.options.fullscreen = false;
            Game1.options.windowedBorderlessFullscreen = false;
            Game1.options.preferredResolutionX = clientWidth;
            Game1.options.preferredResolutionY = clientHeight;

            Game1.graphics.IsFullScreen = false;
            Game1.graphics.PreferredBackBufferWidth = clientWidth;
            Game1.graphics.PreferredBackBufferHeight = clientHeight;
            Game1.graphics.ApplyChanges();

            if (handle != IntPtr.Zero)
            {
                const uint SWP_NOZORDER = 0x0004;
                const uint SWP_NOACTIVATE = 0x0010;
                SetWindowPos(handle, IntPtr.Zero, area.Left, area.Top, 0, 0,
                             SWP_NOZORDER | SWP_NOACTIVATE | 0x0001);   // 0x0001 = NOSIZE
            }

            return true;
        }
        catch
        {
            return false;
        }
    }


    /// <summary>
    /// Maximises the window the way the title-bar button does, on the screen it is supposed to live on.
    ///
    /// Sizing the window from outside (backbuffer + SetWindowPos) produces something that only LOOKS
    /// full: the window manager still treats it as a floating window with walls around it, so the
    /// maximise button still offers to maximise, and anything that re-fits the window afterwards
    /// undoes it. Asking Windows to maximise - WM_SYSCOMMAND/SC_MAXIMIZE, which is literally what the
    /// button sends - is the only version that stays maximised.
    ///
    /// Two things have to be true first, and both were being missed:
    ///
    ///   * the window has to be ON the target monitor, or Windows maximises it on whichever monitor
    ///     it happens to be sitting on (the primary one, every time);
    ///   * the window needs WS_MAXIMIZEBOX. Without it the request is ignored and the symptom is a
    ///     maximised-looking window whose button does nothing - "it still does not work".
    /// </summary>
    public static bool MaximizeOnTargetMonitor() => MaximizeOnTargetMonitor(out _);


    /// <summary>
    /// Same, but says WHY when it fails.
    ///
    /// The first version swallowed every exception, which made "did it work?" unanswerable: a failed
    /// maximise and a successful one both returned quietly. That is the same defect this project keeps
    /// finding in its agents - a claim with nothing to check it - and it was sitting in my own code.
    /// A window operation that cannot say what happened is not evidence of anything.
    /// </summary>
    public static bool MaximizeOnTargetMonitor(out string why)
    {
        why = "";

        IntPtr handle = GameWindowHandle();
        if (handle == IntPtr.Zero)
        {
            why = "no game window handle";
            return false;
        }

        try
        {
            // 1. Make sure the window is allowed to be maximised at all - but only if the style could
            //    actually be READ. A zero here means the read failed, and OR-ing our wishes onto that
            //    zero and writing it back would strip the window's real style.
            long style = (long)GetWindowLongPtr(handle, GWL_STYLE);
            if (style != 0)
            {
                long wanted = style | WS_MAXIMIZEBOX | WS_THICKFRAME;
                if (wanted != style)
                    SetWindowLongPtr(handle, GWL_STYLE, (IntPtr)wanted);
            }

            if (IsZoomed(handle))
                return true;

            // 2. Move it onto the target screen first. Maximising is per-monitor, so the order matters.
            if (PreferredWorkArea(out RECT area) && area.Right > area.Left && area.Bottom > area.Top)
            {
                const uint SWP_NOZORDER = 0x0004;
                const uint SWP_NOACTIVATE = 0x0010;
                const uint SWP_NOSIZE = 0x0001;
                SetWindowPos(handle, IntPtr.Zero, area.Left + 40, area.Top + 40, 0, 0,
                             SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE);
            }

            // 3. The thing the maximise button actually does.
            SendMessage(handle, WM_SYSCOMMAND, (IntPtr)SC_MAXIMIZE, IntPtr.Zero);

            if (IsZoomed(handle))
                return true;

            // NOT writing Game1.options.preferredResolution here.
            //
            // It was there to survive the re-fit that happens when a save loads: put the maximised
            // client size into the game's options so a re-apply would reproduce it. But an apply is
            // still a plain RESIZE, so what it produced was a window flagged as maximised whose rect
            // was the small restored size - "zoomed" and small at the same time, which is worse than
            // either. Since the watchdog now re-maximises continuously, the game does not need to be
            // told anything about size at all. Let Windows own the geometry.

            long after = (long)GetWindowLongPtr(handle, GWL_STYLE);
            why = $"SC_MAXIMIZE was sent but IsZoomed is still false "
                + $"(style=0x{after:X}, maximizebox={(after & WS_MAXIMIZEBOX) != 0}, "
                + $"thickframe={(after & WS_THICKFRAME) != 0})";
            return false;
        }
        catch (Exception ex)
        {
            why = $"{ex.GetType().Name}: {ex.Message}";
            return false;
        }
    }


    /// <summary>
    /// Tells the game that the size it should ask for is the size it just got.
    ///
    /// MonoGame re-applies `preferredResolution` when a save loads or the window changes, and an
    /// apply is a plain resize - which quietly un-maximises the window a moment after we maximised
    /// it. Writing the maximised client size back means a re-apply reproduces that window instead of
    /// shrinking it back to whatever the options file remembered from last time.
    /// </summary>
    private static void RememberMaximizedClientSize(IntPtr handle)
    {
        try
        {
            if (!GetClientRect(handle, out RECT client))
                return;

            int width = client.Right - client.Left;
            int height = client.Bottom - client.Top;
            if (width <= 0 || height <= 0)
                return;

            Game1.options.fullscreen = false;
            Game1.options.windowedBorderlessFullscreen = false;
            Game1.options.preferredResolutionX = width;
            Game1.options.preferredResolutionY = height;
        }
        catch { /* remembering is a nicety; failing must not break the maximise */ }
    }


    /// <summary>Kept for call sites that still say "maximise"; now means the real one.</summary>
    public static bool MaximizeGameWindow() => MaximizeOnTargetMonitor();


    /// <summary>
    /// The handle of the actual game window.
    ///
    /// Process.MainWindowHandle is the obvious thing to reach for and the wrong one: SMAPI runs the
    /// game with its own console window, and the process will happily hand back that instead - which
    /// maximises a black text box and leaves the game exactly where it was. XNA knows the right one.
    /// </summary>
    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsWindow(IntPtr hWnd);

    public static IntPtr GameWindowHandle()
    {
        try
        {
            if (Game1.game1?.Window?.Handle is IntPtr fromXna && IsUsableWindow(fromXna))
                return fromXna;
        }
        catch { }

        IntPtr fromProcess = System.Diagnostics.Process.GetCurrentProcess().MainWindowHandle;
        if (IsUsableWindow(fromProcess))
            return fromProcess;

        return IntPtr.Zero;
    }


    /// <summary>
    /// A handle is only usable once Windows agrees it is a window.
    ///
    /// This check exists because of a real failure, not a precaution. `GetWindowLongPtr` on the handle
    /// the game reports came back ZERO - meaning that as far as user32 was concerned it was not a
    /// window at all - and the code then took that zero, OR-ed the styles it wanted onto it, and wrote
    /// the result back. Deriving a window style from a failed read and applying it is how a window
    /// loses its caption, or vanishes. A read that failed must never become a write.
    /// </summary>
    private static bool IsUsableWindow(IntPtr handle)
    {
        if (handle == IntPtr.Zero)
            return false;

        try
        {
            return IsWindow(handle) && (long)GetWindowLongPtr(handle, GWL_STYLE) != 0;
        }
        catch { return false; }
    }

    /// <summary>Brings the game window to the front, which keyboard input needs.</summary>
    private static IntPtr _focusToRestore = IntPtr.Zero;
    private static long _focusRestoreAt;

    public static bool FocusGameWindow()
    {
        IntPtr handle = GameWindowHandle();
        if (handle == IntPtr.Zero)
            return false;

        // Already focused? Then leave it alone. This is called before every injected input, and an
        // agent that re-asserts focus several times a minute drags the window in front of whatever the
        // person at the machine is doing.
        if (IsGameWindowActive())
            return true;

        // Remember who had it, so it can be handed back.
        //
        // The bridge needs the game in front for the INSTANT an input is injected - it does not need
        // to KEEP it. Taking it and keeping it is what makes the machine unusable while the agent
        // plays: the window you are actually working in keeps losing focus to a game. So focus is
        // borrowed here and returned from the tick, a moment later.
        try
        {
            IntPtr previous = GetForegroundWindow();
            if (previous != IntPtr.Zero && previous != handle)
            {
                _focusToRestore = previous;
                _focusRestoreAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                                  + BorrowedFocusMs;
            }
        }
        catch { }

        // Un-minimise ONLY if it is actually minimised.
        //
        // This used to call ShowWindow(SW_RESTORE) unconditionally, and on a MAXIMISED window
        // SW_RESTORE means "restore to the previous size" - so every single action un-maximised the
        // window, minutes after it had been maximised, with nothing in the log to explain it.
        if (IsIconic(handle))
            ShowWindow(handle, SW_RESTORE);

        // SetForegroundWindow is REFUSED when the calling thread is not already the foreground thread:
        // the window flashes in the taskbar and nothing else happens. From the outside that is
        // indistinguishable from "the game ignored my click", and it is the other half of why the real
        // mouse did nothing while keyboard input sometimes seemed to work. Attaching to the foreground
        // thread for the length of the call is the standard way past the restriction.
        try
        {
            IntPtr foreground = GetForegroundWindow();
            uint ours = GetCurrentThreadId();
            uint theirs = foreground == IntPtr.Zero
                ? 0
                : GetWindowThreadProcessId(foreground, IntPtr.Zero);

            bool attached = theirs != 0 && ours != theirs
                            && AttachThreadInput(ours, theirs, true);
            try
            {
                return SetForegroundWindow(handle);
            }
            finally
            {
                if (attached)
                    AttachThreadInput(ours, theirs, false);
            }
        }
        catch
        {
            return SetForegroundWindow(handle);
        }
    }


    [System.Runtime.InteropServices.DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint attach, uint attachTo, bool attachFlag);


    /// <summary>How long the game may hold focus that it borrowed. One injected input's worth.</summary>
    private const int BorrowedFocusMs = 250;


    /// <summary>Hands focus back to whoever had it, once the injected input has landed.</summary>
    internal static void ReturnBorrowedFocus()
    {
        IntPtr target = _focusToRestore;
        if (target == IntPtr.Zero)
            return;

        if (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < _focusRestoreAt)
            return;

        _focusToRestore = IntPtr.Zero;
        try { SetForegroundWindow(target); } catch { }
    }

    public static void PressAndRelease(byte vk)
    {
        keybd_event(vk, 0, 0, UIntPtr.Zero);
        Thread.Sleep(30);
        keybd_event(vk, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
    }


    // -- the mouse ---------------------------------------------------------
    //
    // Everything that went wrong with the shop came from doing this by hand: calling a menu's own
    // receiveLeftClick(x, y) meant knowing WHICH menu, supplying the hover and the release around it,
    // and understanding what it does with an item in its hand. Every fix was a reverse-engineered
    // guess, and each one produced the next bug - the wrong menu, an empty receiveLeftClick, a menu
    // that would not close.
    //
    // A player does not do any of that. A player moves the mouse over the thing and clicks, and the
    // game does the rest: it dispatches to the right menu, hovers, presses, releases, draws the item
    // following the cursor, and puts it down where it belongs. So this does the same thing.

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct POINT
    {
        public int X;
        public int Y;
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool GetCursorPos(ref POINT point);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool ClientToScreen(IntPtr hWnd, ref POINT point);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    private const uint MOUSEEVENTF_LEFTUP = 0x0004;

    /// <summary>The screen position of the game window's own (0,0), or null when it cannot be read.</summary>
    private static POINT? ClientOrigin()
    {
        IntPtr handle = GameWindowHandle();
        if (handle == IntPtr.Zero)
            return null;

        var origin = new POINT();
        try
        {
            return ClientToScreen(handle, ref origin) ? origin : null;
        }
        catch { return null; }
    }

    /// <summary>
    /// Moves the real cursor over a point in the game's own coordinates and clicks it.
    ///
    /// The coordinates are the same ones the menus use for their components (bounds), which are window
    /// client pixels - so this is where a player's mouse would be. The window is brought forward for
    /// the instant of the click and handed back afterwards, exactly as keyboard input already does.
    /// </summary>
    private static string _lastClick = "";

    /// <summary>The numbers behind the last real click, for the witness. A mismatch here is a diagnosis.</summary>
    public static string LastClickReport() => _lastClick;

    public static bool ClickAt(int clientX, int clientY)
    {
        POINT? origin = ClientOrigin();
        if (origin == null)
        {
            _lastClick = "no screen origin";
            return false;
        }

        if (!IsGameWindowActive())
            FocusGameWindow();

        float scale = UiScale();
        int screenX = origin.Value.X + (int)Math.Round(clientX * scale);
        int screenY = origin.Value.Y + (int)Math.Round(clientY * scale);

        int clientW = 0, clientH = 0;
        try
        {
            if (GetClientRect(GameWindowHandle(), out RECT rect))
            {
                clientW = rect.Right;
                clientH = rect.Bottom;
            }
        }
        catch { }

        int uiW = 0;
        try { uiW = Game1.uiViewport.Width; } catch { }

        try
        {
            SetCursorPos(screenX, screenY);

            // A small wiggle, because a JUMP may not be an event.
            //
            // The OS cursor arrives (OS_cursor == screen) and the game's reading never follows. SDL
            // reports the mouse from the motion events it receives, so a single teleport can leave it
            // believing the pointer is where it was; two small moves give it motion to report. If this
            // fixes it, the click was always in the right place and only the game's idea of "where the
            // mouse is" was stale; if it does not, the game is reading the mouse from something other
            // than the cursor and the next suspect is its own input layer.
            SetCursorPos(screenX + 2, screenY + 2);
            Thread.Sleep(15);
            SetCursorPos(screenX, screenY);

            // WAIT UNTIL THE GAME AGREES, then click.
            int waited = 0;
            while (waited < 400 && !MouseAgrees(clientX, clientY))
            {
                Thread.Sleep(20);
                waited += 20;
            }

            bool agreed = MouseAgrees(clientX, clientY);

            // Where the OS actually put the cursor. This is the number that settles a class of mystery:
            // if the OS cursor is NOT at the point we asked for, the move was clamped (a window on a
            // display that is not live, or a coordinate off the virtual desktop) and no amount of
            // waiting or clicking will help. If the OS cursor IS there and the game disagrees, then the
            // game reads the mouse in a different space than its own component bounds - which would be
            // this code's assumption to fix, not the game's.
            var osPoint = new POINT();
            GetCursorPos(ref osPoint);

            _lastClick = $"screen=({screenX},{screenY}) aim=({clientX},{clientY}) "
                         + $"origin=({origin.Value.X},{origin.Value.Y}) client=({clientW}x{clientH}) "
                         + $"ui={uiW} scale={scale:0.###} agreed={agreed} waited={waited}ms "
                         + $"OS_cursor=({osPoint.X},{osPoint.Y})";

            mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
            Thread.Sleep(50);
            mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
            Thread.Sleep(30);
            return true;
        }
        catch { return false; }
    }


    /// <summary>Whether the game's own mouse reading has caught up with the point we moved it to.</summary>
    private static bool MouseAgrees(int clientX, int clientY)
    {
        try
        {
            return Math.Abs(Game1.getMouseX() - clientX) <= 2 && Math.Abs(Game1.getMouseY() - clientY) <= 2;
        }
        catch { return true; }
    }


    /// <summary>
    /// Window pixels per unit of the game's UI coordinate.
    ///
    /// A menu's component bounds are in the game's OWN UI space (`Game1.uiViewport`), not in window
    /// pixels - they are only the same thing when the window happens to match the internal resolution
    /// one to one. Move the mouse by the UI coordinates alone and every click lands at the wrong
    /// place, at a fraction of the distance, which is exactly what "the real click changed nothing"
    /// turned out to mean for every single click.
    /// </summary>
    private static float UiScale()
    {
        try
        {
            IntPtr handle = GameWindowHandle();
            if (handle == IntPtr.Zero || !GetClientRect(handle, out RECT client))
                return 1f;

            int uiWidth = Game1.uiViewport.Width;
            if (uiWidth <= 0 || client.Right <= 0)
                return 1f;

            return (float)client.Right / uiWidth;
        }
        catch { return 1f; }
    }


    /// <summary>
    /// Where the GAME thinks the mouse is, in the same coordinates a menu uses.
    ///
    /// For the click witness: after moving the real cursor it must agree with the point we aimed at,
    /// and if it does not, the two numbers are the diagnosis instead of another guess.
    /// </summary>
    public static string MouseSeen()
    {
        try { return $"({Game1.getMouseX()},{Game1.getMouseY()})"; }
        catch { return "?"; }
    }
}
