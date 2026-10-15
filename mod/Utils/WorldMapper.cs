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

        return result;
    }
}
