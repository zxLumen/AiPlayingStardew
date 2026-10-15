using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Pathfinding;
using StardewValley.TerrainFeatures;
using xTile.Dimensions;

namespace StateBridge.Utils;

/// <summary>
/// Runs a whole chore at game speed instead of spending one model decision per tool swing.
///
/// This is the reflex layer, and it is the difference between "the farmer stands still for three
/// seconds between swings" and "the farmer just clears the patch". A real player decides once -
/// "clear this area" - and then their hands execute for twenty seconds without conscious thought.
/// The expensive model is only consulted for the intent.
///
/// A target is finished when the WORLD says it changed, not when the swing claimed success.
///
/// Must only be called on the game thread.
/// </summary>
public static class ChoreController
{
    private const int ChoreTimeoutMs = 300_000;   // give up on a chore that cannot finish
    private const int SwingIntervalMs = 400;      // roughly one tool animation
    private const int MaxAttemptsPerTile = 8;     // before giving up on a stubborn tile

    private sealed class Chore
    {
        public string Kind = "";
        public List<Vector2> Targets = new();
        public int Index;
        public int Done;
        public int Failed;
        public int Attempts;
        public long Deadline;
        public long NextSwingAt;
        public string LastNote = "";
    }

    private static Chore? _chore;

    public static bool IsRunning => _chore != null;

    public static string Describe()
    {
        Chore? chore = _chore;
        return chore == null
            ? "idle"
            : $"{chore.Kind}: {chore.Index}/{chore.Targets.Count} visited, {chore.Done} finished, {chore.Failed} given up {chore.LastNote}";
    }

    public static (bool, string) Start(string kind, List<Vector2> targets)
    {
        if (_chore != null)
            return (false, $"already busy - {Describe()}");

        if (targets.Count == 0)
            return (false, $"nothing to do for {kind} - nothing nearby needs that work");

        // Nearest first, so movement looks natural instead of zig-zagging the map.
        if (Game1.player != null)
        {
            Vector2 from = Game1.player.Tile;
            targets.Sort((a, b) => Distance(from, a).CompareTo(Distance(from, b)));
        }

        _chore = new Chore
        {
            Kind = kind,
            Targets = targets,
            Deadline = Now() + ChoreTimeoutMs * TimeSpan.TicksPerMillisecond,
        };

        return (true, $"{kind}: {targets.Count} tile(s) queued - it will work through them by itself");
    }

    public static (bool, string) Cancel()
    {
        if (_chore == null)
            return (false, "no chore is running");

        string was = Describe();
        _chore = null;
        MovementController.Stop();
        return (true, $"cancelled ({was})");
    }

    public static string Status() => Describe();

    public static void OnUpdateTicked()
    {
        Chore? chore = _chore;
        if (chore == null)
            return;

        Farmer? farmer = Game1.player;
        GameLocation? location = Game1.currentLocation;
        if (farmer == null || location == null)
        {
            _chore = null;
            return;
        }

        if (Now() > chore.Deadline)
        {
            _chore = null;
            return;
        }

        if (farmer.controller != null) return;        // still walking to the target
        if (!GameQuery.IsPlayerFree()) return;        // a menu or event is up
        if (Now() < chore.NextSwingAt) return;        // the swing animation is still playing

        if (chore.Index >= chore.Targets.Count)
        {
            _chore = null;
            return;                                    // finished
        }

        Vector2 target = chore.Targets[chore.Index];
        int tx = (int)target.X;
        int ty = (int)target.Y;

        // Finished when the world says so - and each chore has its own idea of "finished".
        // "The tile is not 'nothing' any more" is wrong: clearing a weed leaves tilled-able
        // ground behind, which is not "nothing", so the chore would chew at the same tile forever.
        if (IsComplete(chore.Kind, location, tx, ty))
        {
            chore.Index++;
            chore.Done++;
            chore.Attempts = 0;
            return;
        }

        (string kind, Type? toolType) = ActionProcessor.ClassifyTarget(location, tx, ty);

        if (ActionProcessor.IsAdjacent(farmer, tx, ty))
        {
            if (chore.Attempts >= MaxAttemptsPerTile)
            {
                chore.LastNote = $"({kind} at ({tx},{ty}) would not budge)";
                chore.Index++;
                chore.Failed++;
                chore.Attempts = 0;
                return;
            }

            ActionProcessor.FaceTowards(farmer, tx, ty);

            if (chore.Kind == "plant_all")
            {
                // Planting does not go through checkAction or a tool swing - see PlantAt.
                ActionProcessor.PlantAt(location, tx, ty, farmer);
            }
            else if (toolType == null)
            {
                // Harvest, forage or a machine: interacting is the whole job.
                location.checkAction(new Location(tx, ty), Game1.viewport, farmer);
            }
            else if (ActionProcessor.HoldTool(farmer, toolType))
            {
                if (farmer.CurrentItem is StardewValley.Tool tool)
                    ActionProcessor.SwingTool(tool, location, tx, ty, farmer);
            }
            else
            {
                chore.LastNote = $"(no {FriendlyName(toolType)} for {kind})";
                chore.Index++;                         // we do not carry that tool
                chore.Failed++;
                chore.Attempts = 0;
                return;
            }

            chore.Attempts++;
            chore.NextSwingAt = Now() + SwingIntervalMs * TimeSpan.TicksPerMillisecond;
            return;
        }

        // Out of reach: walk to a free tile beside it.
        Point? stand = ActionProcessor.FindAdjacentWalkable(location, tx, ty);
        if (stand == null)
        {
            chore.LastNote = $"(nowhere to stand by ({tx},{ty}))";
            chore.Index++;
            chore.Failed++;
            chore.Attempts = 0;
            return;
        }

        try
        {
            var controller = new PathFindController(farmer, location, stand.Value, 2);
            farmer.controller = controller;

            if (controller.pathToEndPoint == null || controller.pathToEndPoint.Count == 0)
            {
                farmer.controller = null;
                chore.LastNote = $"(no path to ({tx},{ty}))";
                chore.Index++;
                chore.Failed++;
                chore.Attempts = 0;
            }
        }
        catch
        {
            chore.LastNote = $"(pathing threw for ({tx},{ty}))";
            chore.Index++;
            chore.Failed++;
            chore.Attempts = 0;
        }
    }

    // -- what "finished" means, per chore ----------------------------------

    private static bool IsComplete(string choreKind, GameLocation location, int x, int y)
    {
        switch (choreKind)
        {
            case "clear_area":
                return !HasDebris(location, x, y);

            case "water_all":
            {
                if (FindFeature(location, x, y) is not HoeDirt dirt || dirt.crop == null)
                    return true;
                try { return dirt.isWatered(); } catch { return true; }
            }

            case "harvest_all":
            {
                if (FindFeature(location, x, y) is not HoeDirt dirt || dirt.crop == null)
                    return true;
                try { return !dirt.readyForHarvest(); } catch { return true; }
            }

            case "till_area":
                return FindFeature(location, x, y) is HoeDirt;

            case "plant_all":
            {
                if (FindFeature(location, x, y) is not HoeDirt dirt)
                    return true;
                return dirt.crop != null;
            }

            default:
                return false;
        }
    }

    private static bool HasDebris(GameLocation location, int x, int y)
    {
        try
        {
            if (location.objects.TryGetValue(new Vector2(x, y), out StardewValley.Object? obj) && obj != null)
                return obj.IsWeeds() || obj.IsTwig() || obj.IsBreakableStone();
        }
        catch { }

        return false;
    }

    private static TerrainFeature? FindFeature(GameLocation location, int x, int y)
    {
        var wanted = new Vector2(x, y);
        foreach ((Vector2 tile, TerrainFeature feature) in TerrainFeatures(location))
        {
            if (tile == wanted)
                return feature;
        }
        return null;
    }

    // -- target collection -------------------------------------------------

    public static List<Vector2> CollectDebris(GameLocation location, int cx, int cy, int radius)
    {
        var list = new List<Vector2>();

        try
        {
            foreach (KeyValuePair<Vector2, StardewValley.Object> pair in location.objects.Pairs)
            {
                StardewValley.Object? obj = pair.Value;
                if (obj == null || !obj.IsWeeds() && !obj.IsTwig() && !obj.IsBreakableStone())
                    continue;

                Vector2 tile = pair.Key;
                if (Math.Abs((int)tile.X - cx) > radius || Math.Abs((int)tile.Y - cy) > radius)
                    continue;

                list.Add(tile);
            }
        }
        catch { }

        return list;
    }

    public static List<Vector2> CollectThirstyCrops(GameLocation location, int cx, int cy, int radius)
    {
        var list = new List<Vector2>();

        foreach ((Vector2 tile, TerrainFeature feature) in TerrainFeatures(location))
        {
            if (feature is not HoeDirt dirt || dirt.crop == null)
                continue;
            if (Math.Abs((int)tile.X - cx) > radius || Math.Abs((int)tile.Y - cy) > radius)
                continue;

            bool watered;
            try { watered = dirt.isWatered(); }
            catch { continue; }

            if (!watered)
                list.Add(tile);
        }

        return list;
    }

    public static List<Vector2> CollectReadyCrops(GameLocation location, int cx, int cy, int radius)
    {
        var list = new List<Vector2>();

        foreach ((Vector2 tile, TerrainFeature feature) in TerrainFeatures(location))
        {
            if (feature is not HoeDirt dirt || dirt.crop == null)
                continue;
            if (Math.Abs((int)tile.X - cx) > radius || Math.Abs((int)tile.Y - cy) > radius)
                continue;

            bool ready;
            try { ready = dirt.readyForHarvest(); }
            catch { continue; }

            if (ready)
                list.Add(tile);
        }

        return list;
    }

    /// <summary>Diggable, uncluttered tiles that are not tilled yet.</summary>
    public static List<Vector2> CollectTillable(GameLocation location, int cx, int cy, int radius)
    {
        var list = new List<Vector2>();
        var layer = location.Map?.Layers.FirstOrDefault();
        if (layer == null)
            return list;

        // Tilled tiles, gathered once rather than looked up per candidate - a whole-map scan is
        // thousands of tiles and FindFeature walks every terrain feature.
        var tilled = new HashSet<Vector2>();
        foreach ((Vector2 tile, TerrainFeature feature) in TerrainFeatures(location))
        {
            if (feature is HoeDirt)
                tilled.Add(tile);
        }

        int minX = Math.Max(0, cx - radius);
        int maxX = Math.Min(layer.LayerWidth - 1, cx + radius);
        int minY = Math.Max(0, cy - radius);
        int maxY = Math.Min(layer.LayerHeight - 1, cy + radius);

        for (int x = minX; x <= maxX; x++)
        {
            for (int y = minY; y <= maxY; y++)
            {
                if (tilled.Contains(new Vector2(x, y)))     // already tilled
                    continue;
                if (HasDebris(location, x, y))              // clear it first
                    continue;

                try
                {
                    if (location.doesTileHaveProperty(x, y, "Diggable", "Back", false) != null)
                        list.Add(new Vector2(x, y));
                }
                catch { }
            }
        }

        return list;
    }


    /// <summary>Tilled tiles with nothing planted in them.</summary>
    public static List<Vector2> CollectEmptySoil(GameLocation location, int cx, int cy, int radius)
    {
        var list = new List<Vector2>();

        foreach ((Vector2 tile, TerrainFeature feature) in TerrainFeatures(location))
        {
            if (feature is not HoeDirt dirt || dirt.crop != null)
                continue;
            if (Math.Abs((int)tile.X - cx) > radius || Math.Abs((int)tile.Y - cy) > radius)
                continue;

            list.Add(tile);
        }

        return list;
    }


    /// <summary>Flattens the chunked terrain feature dictionary into tile/feature pairs.</summary>
    private static IEnumerable<(Vector2 Tile, TerrainFeature Feature)> TerrainFeatures(GameLocation location)
    {
        if (location.terrainFeatures == null)
            yield break;

        foreach (SerializableDictionary<Vector2, TerrainFeature> chunk in location.terrainFeatures)
        {
            if (chunk == null)
                continue;

            foreach (KeyValuePair<Vector2, TerrainFeature> pair in chunk)
            {
                if (pair.Value != null)
                    yield return (pair.Key, pair.Value);
            }
        }
    }

    private static float Distance(Vector2 a, Vector2 b) =>
        Math.Abs(a.X - b.X) + Math.Abs(a.Y - b.Y);

    private static string FriendlyName(Type toolType) =>
        toolType == typeof(StardewValley.Tools.MeleeWeapon) ? "scythe" : toolType.Name;

    private static long Now() => Game1.currentGameTime?.TotalGameTime.Ticks ?? 0;
}
