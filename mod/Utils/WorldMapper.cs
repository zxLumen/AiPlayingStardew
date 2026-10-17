using System.Linq;
using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.Monsters;
using StardewValley.TerrainFeatures;
using StateBridge.Models;
using xTile.Dimensions;
using xTile.Layers;

namespace StateBridge.Utils;

/// <summary>
/// Turns the local world into things an agent can actually reason about.
///
/// The character map is the important one: an agent that only gets a list of coordinates cannot
/// see that a wall is between it and its target, which is exactly how it ends up walking into
/// things. The idea comes from Hunter-Thompson/stardew-mcp (main branch); this is a rewrite.
///
/// Legend:
///   @ you      . walkable      # wall / building / out of bounds
///   ~ water    T tree / bush   O object: stone, twig, weed, machine, clump
///   C crop     H tilled soil   " grass
///   > door / warp              ! NPC            M monster
///
/// Must only be called on the game thread.
/// </summary>
public static class WorldMapper
{
    public static string BuildAsciiMap(GameLocation location, int centerX, int centerY, int radius)
    {
        if (location?.Map == null || radius <= 0)
            return "";

        Layer? layer = location.Map.Layers.FirstOrDefault();
        if (layer == null)
            return "";

        int size = radius * 2 + 1;
        var grid = new char[size, size];

        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
                grid[y, x] = '.';

        for (int dy = -radius; dy <= radius; dy++)
        {
            for (int dx = -radius; dx <= radius; dx++)
            {
                int wx = centerX + dx;
                int wy = centerY + dy;
                int gx = dx + radius;
                int gy = dy + radius;

                if (wx < 0 || wy < 0 || wx >= layer.LayerWidth || wy >= layer.LayerHeight)
                {
                    grid[gy, gx] = '#';
                    continue;
                }

                var tile = new Vector2(wx, wy);
                var tileLocation = new Location(wx, wy);
                char ch = '.';

                try { if (location.isWaterTile(wx, wy)) ch = '~'; } catch { }
                if (ch == '.')
                {
                    try { if (!location.isTilePassable(tileLocation, Game1.viewport)) ch = '#'; } catch { }
                }

                try
                {
                    bool isWarp = location.warps.Any(w => w.X == wx && w.Y == wy)
                               || location.doors.ContainsKey(new Point(wx, wy));
                    if (isWarp)
                        ch = '>';
                }
                catch { }

                try
                {
                    if (location.terrainFeatures.TryGetValue(tile, out TerrainFeature? feature) && feature != null)
                    {
                        ch = feature switch
                        {
                            Tree => 'T',
                            FruitTree => 'T',
                            HoeDirt dirt when dirt.crop != null => 'C',
                            HoeDirt => 'H',
                            Grass => '"',
                            Bush => 'T',
                            _ => feature.isPassable() ? ch : 'O',
                        };
                    }
                }
                catch { }

                try
                {
                    if (location.objects.TryGetValue(tile, out StardewValley.Object? obj) && obj != null && !obj.isPassable())
                        ch = 'O';
                }
                catch { }

                try
                {
                    foreach (ResourceClump clump in location.resourceClumps)
                    {
                        if (clump != null && clump.occupiesTile(wx, wy))
                        {
                            ch = 'O';
                            break;
                        }
                    }
                }
                catch { }

                try
                {
                    foreach (Building building in location.buildings)
                    {
                        if (building == null || !building.occupiesTile(tile))
                            continue;

                        int doorX = building.tileX.Value + building.humanDoor.X;
                        int doorY = building.tileY.Value + building.humanDoor.Y;
                        ch = (wx == doorX && wy == doorY) ? '>' : '#';
                        break;
                    }
                }
                catch { }

                grid[gy, gx] = ch;
            }
        }

        try
        {
            foreach (NPC npc in location.characters)
            {
                if (npc == null)
                    continue;

                int gx = (int)npc.Tile.X - centerX + radius;
                int gy = (int)npc.Tile.Y - centerY + radius;

                if (gx >= 0 && gx < size && gy >= 0 && gy < size)
                    grid[gy, gx] = npc is Monster ? 'M' : '!';
            }
        }
        catch { }

        grid[radius, radius] = '@';

        var builder = new System.Text.StringBuilder(size * (size + 1));
        for (int y = 0; y < size; y++)
        {
            for (int x = 0; x < size; x++)
                builder.Append(grid[y, x]);
            if (y < size - 1)
                builder.Append('\n');
        }

        return builder.ToString();
    }


    /// <summary>Describes the single tile the farmer is facing - what an action would act on.</summary>
    public static TileInFrontInfo BuildTileInFront(GameLocation location, Farmer farmer)
    {
        int fx = (int)farmer.Tile.X;
        int fy = (int)farmer.Tile.Y;

        switch (farmer.FacingDirection)
        {
            case 0: fy--; break;
            case 1: fx++; break;
            case 2: fy++; break;
            default: fx--; break;
        }

        var info = new TileInFrontInfo { X = fx, Y = fy };
        var tile = new Vector2(fx, fy);
        var tileLocation = new Location(fx, fy);

        try { info.Passable = location.isTilePassable(tileLocation, Game1.viewport); } catch { }
        try { info.Water = location.isWaterTile(fx, fy); } catch { }
        try { info.Diggable = location.doesTileHaveProperty(fx, fy, "Diggable", "Back", false) != null; } catch { }

        try
        {
            if (location.objects.TryGetValue(tile, out StardewValley.Object? obj) && obj != null)
            {
                info.Object = obj.DisplayName ?? obj.Name ?? "";
                info.CanInteract = true;
            }
        }
        catch { }

        try
        {
            if (location.terrainFeatures.TryGetValue(tile, out TerrainFeature? feature) && feature is HoeDirt dirt)
            {
                info.Ground = "hoe_dirt";
                info.Watered = dirt.isWatered();
                info.CanInteract = true;

                if (dirt.crop != null)
                {
                    info.CropReady = dirt.readyForHarvest();
                    try { info.Crop = ItemRegistry.GetDataOrErrorItem(dirt.crop.indexOfHarvest.Value).DisplayName; } catch { }
                }
            }
            else if (feature != null)
            {
                info.Ground = feature.GetType().Name;
                info.CanInteract = true;
            }
        }
        catch { }

        try
        {
            foreach (NPC npc in location.characters)
            {
                if (npc != null && (int)npc.Tile.X == fx && (int)npc.Tile.Y == fy)
                {
                    info.Character = npc.displayName ?? npc.Name ?? "";
                    info.CanInteract = true;
                    break;
                }
            }
        }
        catch { }

        return info;
    }


    /// <summary>Every exit and door in this location, with where it leads.</summary>
    public static List<WarpInfo> BuildWarps(GameLocation location)
    {
        var result = new List<WarpInfo>();
        if (location == null)
            return result;

        try
        {
            foreach (Warp warp in location.warps)
            {
                if (warp == null)
                    continue;

                result.Add(new WarpInfo
                {
                    X = warp.X,
                    Y = warp.Y,
                    Target = warp.TargetName ?? "",
                    TargetX = warp.TargetX,
                    TargetY = warp.TargetY,
                    IsDoor = false,
                });
            }
        }
        catch { }

        try
        {
            foreach (KeyValuePair<Point, string> door in location.doors.Pairs)
            {
                result.Add(new WarpInfo
                {
                    X = door.Key.X,
                    Y = door.Key.Y,
                    Target = door.Value ?? "",
                    TargetX = 0,
                    TargetY = 0,
                    IsDoor = true,
                });
            }
        }
        catch { }

        // A BUILDING's door is in neither list. It is synthesized on demand by
        // GameLocation.getWarpFromDoor (StardewValley.GameLocation:2194), so `location.warps` never
        // mentions the farmhouse, the coops, the barn or the shed. That is why the farm's exits read
        // "BusStop, Forest, Backwoods, Farm" with no house among them, why `enter FarmHouse` answered
        // "nothing here leads to it" while the farmer was standing on the farm, and why the model had
        // to find the door by trial and error.
        //
        // The game's own answer to "where is this building's door" is getPointForHumanDoor() and to
        // "what is this building's inside called" is GetIndoorsName(), so this asks it rather than
        // doing the arithmetic by hand - the same pair of calls GameLocation.getWarpFromDoor makes.
        try
        {
            foreach (Building building in location.buildings)
            {
                if (building == null || !building.HasIndoors())
                    continue;

                string indoors = building.GetIndoorsName() ?? "";
                if (string.IsNullOrWhiteSpace(indoors))
                    continue;

                var door = building.getPointForHumanDoor();

                result.Add(new WarpInfo
                {
                    X = door.X,
                    Y = door.Y,
                    Target = indoors,
                    TargetX = 0,
                    TargetY = 0,
                    IsDoor = true,
                });
            }
        }
        catch { }

        return result;
    }


    /// <summary>The raw "Action" property of a tile, or "" when it has none.</summary>
    public static string TileAction(GameLocation location, int x, int y)
    {
        // The game reads doors off the "Buildings" layer (GameLocation builds `location.doors` from
        // exactly that layer and that property). Back/Front are tried too because a map may put a
        // door property on either.
        foreach (string layer in new[] { "Buildings", "Back", "Front" })
        {
            try
            {
                string? action = location.doesTileHaveProperty(x, y, "Action", layer);
                if (!string.IsNullOrWhiteSpace(action))
                    return action;
            }
            catch { }
        }

        return "";
    }


    /// <summary>
    /// The opening hours of one door, read off that door's own tile, or null when it is not a door
    /// that shuts.
    ///
    /// Stardew writes the answer on the tile:
    ///
    ///     Action: LockedDoorWarp &lt;destX&gt; &lt;destY&gt; &lt;LocationName&gt; &lt;openTime&gt; &lt;closeTime&gt; [npc] [friendship]
    ///
    /// (GameLocation:9539 parses exactly these arguments and hands them to GameLocation.lockedDoorWarp.)
    /// Note that arg 1 is a POINT, which ArgUtility.TryGetPoint reads as two consecutive tokens - so
    /// the location name is arg 3, not arg 2.
    ///
    /// Nothing read this before, and that is the whole shop story: the hours were written on the door
    /// the agent was knocking on, and it knocked seventeen times in one day.
    /// </summary>
    public static ShopDoorInfo? LockedDoorAt(GameLocation location, int x, int y)
    {
        string action = TileAction(location, x, y);
        if (action.Length == 0)
            return null;

        string[] parts = action.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 6 || !parts[0].Equals("LockedDoorWarp", StringComparison.OrdinalIgnoreCase))
            return null;

        string leadsTo = parts[3];
        if (!int.TryParse(parts[4], out int opens) || !int.TryParse(parts[5], out int closes))
            return null;

        return new ShopDoorInfo
        {
            At = new List<int> { x, y },
            LeadsTo = leadsTo,
            OpensAt = opens,
            ClosesAt = closes,
            OpensText = ClockText(opens),
            ClosesText = ClockText(closes),
            OpenNow = OpensNow(location, leadsTo, opens, closes),
            Note = ClosedNote(leadsTo),
        };
    }


    /// <summary>Every door here that shuts outside opening hours, with its hours.</summary>
    public static List<ShopDoorInfo> BuildLockedDoors(GameLocation location)
    {
        var doors = new List<ShopDoorInfo>();
        if (location == null)
            return doors;

        try
        {
            foreach (KeyValuePair<Point, string> entry in location.doors.Pairs)
            {
                ShopDoorInfo? door = LockedDoorAt(location, entry.Key.X, entry.Key.Y);
                if (door != null)
                    doors.Add(door);
            }
        }
        catch { }

        return doors;
    }


    // Scanning a whole map tiles-by-tile is not something to repeat every state call, so the answer is
    // kept per location per day. A day is the right key: a board or a letter box does not move.
    private static readonly Dictionary<string, (int Day, List<PlaceActionInfo> List)> _placeCache = new();
    private static readonly HashSet<string> _placesLogged = new();


    /// <summary>
    /// Every kind of tile-action in this place, one entry per verb, each with the nearest example.
    ///
    /// This is what a person's eye does that the state never did: walking into town you SEE the notice
    /// board on the wall, and you know the box by your door is a letter box. The agent was told only
    /// about the tile it stood on, so it never read its mail and never took a job off the board -
    /// not because it refused, but because nothing ever mentioned they existed.
    /// </summary>
    public static List<PlaceActionInfo> BuildPlaceActions(GameLocation location, int px, int py,
                                                          Action<string>? report = null)
    {
        var empty = new List<PlaceActionInfo>();
        if (location?.map == null)
            return empty;

        string key = $"{location.Name}";
        int day = 0;
        try { day = Game1.Date.TotalDays; } catch { }

        if (_placeCache.TryGetValue(key, out var hit) && hit.Day == day)
            return hit.List;

        int width = 0;
        int height = 0;
        try
        {
            foreach (Layer layer in location.map.Layers)
            {
                width = Math.Max(width, layer.LayerWidth);
                height = Math.Max(height, layer.LayerHeight);
            }
        }
        catch { }

        if (width <= 0 || height <= 0)
            return empty;

        width = Math.Min(width, 400);
        height = Math.Min(height, 400);

        var byVerb = new Dictionary<string, PlaceActionInfo>(StringComparer.OrdinalIgnoreCase);

        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                string action = TileAction(location, x, y);
                if (action.Length == 0)
                    continue;

                string[] words = action.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (words.Length == 0)
                    continue;

                string verb = words[0];
                int distance = Math.Abs(x - px) + Math.Abs(y - py);

                if (byVerb.TryGetValue(verb, out PlaceActionInfo? found))
                {
                    found.Count++;
                    int best = found.At.Count >= 2
                        ? Math.Abs(found.At[0] - px) + Math.Abs(found.At[1] - py)
                        : int.MaxValue;

                    if (distance < best)
                    {
                        found.At = new List<int> { x, y };
                        found.Action = action;
                        found.Args = string.Join(" ", words.Skip(1));
                    }
                    continue;
                }

                byVerb[verb] = new PlaceActionInfo
                {
                    Verb = verb,
                    Action = action,
                    At = new List<int> { x, y },
                    Count = 1,
                    Args = string.Join(" ", words.Skip(1)),
                };
            }
        }

        List<PlaceActionInfo> list = byVerb.Values
            .OrderBy(a => Math.Abs(a.At[0] - px) + Math.Abs(a.At[1] - py))
            .ToList();

        _placeCache[key] = (day, list);

        // Say what was found, once per place. The verbs are the game's own words, and a verb we have
        // never seen before is exactly the sort of thing that should show up in a log rather than in a
        // guess about what a map contains.
        if (report != null && _placesLogged.Add(key))
            report($"place actions in {key}: " +
                   string.Join(", ", list.Select(a => $"{a.Verb} x{a.Count}@{a.At[0]},{a.At[1]}")));

        return list;
    }


    /// <summary>A game clock value written the way the game writes it (900 -> "9:00am").</summary>
    public static string ClockText(int time)
    {
        try { return Game1.getTimeOfDayString(time).Replace(" ", ""); }
        catch { return time.ToString(); }
    }


    /// <summary>
    /// The sentence the game itself says when this door is knocked on: LockedDoor_OpenRange renders
    /// as "上锁了……（营业时间：9:00~21:00）" (GameLocation:10368 builds it from exactly these two
    /// times). A shop that shuts for a reason that is not the hour carries the game's own words for
    /// that instead.
    /// </summary>
    public static string LockedSentence(ShopDoorInfo door)
    {
        if (!string.IsNullOrWhiteSpace(door.Note))
            return door.Note.Trim();

        try
        {
            return Game1.content.LoadString("Strings\\Locations:LockedDoor_OpenRange",
                                            door.OpensText, door.ClosesText);
        }
        catch { return ""; }
    }


    /// <summary>
    /// Whether the clock is inside the door's own range. This is NOT the last word on whether the
    /// door opens - friends-only doors and festival days are not about the hour - which is why the
    /// action remains the authority and reports LOCKED when the game refuses.
    /// </summary>
    private static bool OpensNow(GameLocation location, string leadsTo, int opens, int closes)
    {
        try
        {
            if (IsWeekdayClosed(leadsTo))
                return false;

            return Game1.timeOfDay >= opens && Game1.timeOfDay < closes;
        }
        catch { return true; }
    }


    /// <summary>
    /// The extra reasons a shop is shut that are not its hours, in the game's own words.
    ///
    /// This mirrors one branch of GameLocation.lockedDoorWarp (StardewValley.GameLocation:10327) - a
    /// single, long-standing special case for Pierre, who shuts on Wednesdays until you have seen
    /// event 191393 or bought the town key. Everything else is left to the action to report, rather
    /// than re-deriving game rules here and getting one wrong.
    /// </summary>
    private static bool IsWeekdayClosed(string leadsTo)
    {
        if (!leadsTo.Equals("SeedShop", StringComparison.OrdinalIgnoreCase))
            return false;

        try
        {
            return Game1.shortDayNameFromDayOfSeason(Game1.dayOfMonth).Equals("Wed")
                   && !Utility.HasAnyPlayerSeenEvent("191393")
                   && !Game1.player.HasTownKey;
        }
        catch { return false; }
    }


    private static string ClosedNote(string leadsTo)
    {
        if (!IsWeekdayClosed(leadsTo))
            return "";

        try { return Game1.content.LoadString("Strings\\Locations:SeedShop_LockedWed"); }
        catch { return ""; }
    }
}
