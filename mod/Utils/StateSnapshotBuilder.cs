using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Quests;
using StardewValley.TerrainFeatures;
using StateBridge.Models;

namespace StateBridge.Utils;

/// <summary>
/// Builds the structured JSON snapshot of game state. Must only be called on the game thread.
/// Every section is guarded independently so one bad read can't kill the whole snapshot.
/// </summary>
public static class StateSnapshotBuilder
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };

    private const int DescriptionMaxLen = 140;

    private static long _tick;

    public static string BuildJson(ModConfig cfg, IMonitor monitor)
    {
        _tick++;
        var snap = new GameStateSnapshot { Tick = _tick };

        try { snap.Game = BuildGameInfo(); }
        catch (Exception ex) { monitor.Log($"BuildGameInfo failed: {ex}", LogLevel.Error); }

        try { snap.Player = BuildPlayerInfo(); }
        catch (Exception ex) { monitor.Log($"BuildPlayerInfo failed: {ex}", LogLevel.Error); }

        try { snap.Inventory = BuildInventory(); }
        catch (Exception ex) { monitor.Log($"BuildInventory failed: {ex}", LogLevel.Error); }

        GameLocation? loc = Game1.currentLocation;
        if (loc != null)
        {
            try { snap.NearbyCharacters = BuildCharacters(loc); }
            catch (Exception ex) { monitor.Log($"BuildCharacters failed: {ex}", LogLevel.Error); }

            try { snap.NearbyObjects = BuildNearbyObjects(loc, cfg); }
            catch (Exception ex) { monitor.Log($"BuildNearbyObjects failed: {ex}", LogLevel.Error); }

            try { snap.Crops = BuildCrops(loc, cfg); }
            catch (Exception ex) { monitor.Log($"BuildCrops failed: {ex}", LogLevel.Error); }

            try { snap.Location = BuildLocationInfo(loc, cfg); }
            catch (Exception ex) { monitor.Log($"BuildLocationInfo failed: {ex}", LogLevel.Error); }
        }

        try { snap.Quests = BuildQuests(); }
        catch (Exception ex) { monitor.Log($"BuildQuests failed: {ex}", LogLevel.Error); }

        try { snap.Chore = ChoreController.Status(); }
        catch (Exception ex) { monitor.Log($"Chore status failed: {ex}", LogLevel.Error); }

        try { snap.Ui = UiStateBuilder.Build(monitor); }
        catch (Exception ex) { monitor.Log($"BuildUi failed: {ex}", LogLevel.Error); }

        try { snap.Display = BuildDisplayInfo(); }
        catch (Exception ex) { monitor.Log($"BuildDisplayInfo failed: {ex}", LogLevel.Error); }

        return JsonSerializer.Serialize(snap, JsonOpts);
    }


    private static DisplayInfo BuildDisplayInfo()
    {
        Options options = Game1.options;
        var info = new DisplayInfo();

        try { info.ZoomLevel = options.zoomLevel; } catch { }
        try { info.DesiredZoomLevel = options.desiredBaseZoomLevel; } catch { }
        try { info.UiScale = options.uiScale; } catch { }
        try { info.DesiredUiScale = options.desiredUIScale; } catch { }
        try { info.PreferredResolutionX = options.preferredResolutionX; } catch { }
        try { info.PreferredResolutionY = options.preferredResolutionY; } catch { }
        try { info.Fullscreen = options.fullscreen; } catch { }
        try { info.Borderless = options.windowedBorderlessFullscreen; } catch { }

        try
        {
            info.RenderWidth = Game1.graphics.GraphicsDevice.Viewport.Width;
            info.RenderHeight = Game1.graphics.GraphicsDevice.Viewport.Height;
        }
        catch { }

        return info;
    }


    private static GameInfo BuildGameInfo()
    {
        return new GameInfo
        {
            DayOfMonth = Game1.dayOfMonth,
            Season = Game1.currentSeason,
            Year = Game1.year,
            TimeOfDay = Game1.timeOfDay,
            DayOfWeek = SafeDayOfWeek(),
            TotalDays = SafeTotalDays(),
            IsRaining = Game1.isRaining,
            IsLightning = Game1.isLightning,
            IsSnowing = Game1.isSnowing,
            IsDebrisWeather = Game1.isDebrisWeather,
            IsFestival = Game1.isFestival(),
            IsWedding = Game1.weddingToday,
            IsEventUp = Game1.eventUp,
            IsDialogueUp = Game1.dialogueUp,
            IsMenuOpen = Game1.activeClickableMenu != null,
            IsPaused = Game1.isTimePaused,
            IsGamePaused = Game1.paused,
            IsPlayerFree = GameQuery.IsPlayerFree(),
            HasSave = Game1.player != null && Game1.currentLocation != null,
            MinutesUntilMorning = MinutesUntilTwoAm(),
            BlockedReason = GameQuery.BlockedReason(),
        };
    }


    /// <summary>The game's own weekday. Read from its calendar rather than computed, so mods that shift the calendar stay correct.</summary>
    private static string SafeDayOfWeek()
    {
        try { return Game1.Date.DayOfWeek.ToString(); }
        catch { return ""; }
    }


    private static int SafeTotalDays()
    {
        try { return Game1.Date.TotalDays; }
        catch { return 0; }
    }


    /// <summary>In-game minutes left before the 2:00 AM collapse.</summary>
    private static int MinutesUntilTwoAm()
    {
        int now = Game1.timeOfDay;
        if (now < 600)
            return 0;

        const int collapse = 2600;
        int left = collapse - now;
        if (left <= 0)
            return 0;

        return (left / 100) * 60 + (left % 100);
    }


    private static PlayerInfo BuildPlayerInfo()
    {
        Farmer f = Game1.player;
        if (f == null)
            return new PlayerInfo();

        (int tx, int ty) = GameQuery.TileOf(f);

        var info = new PlayerInfo
        {
            Name = f.Name ?? "",
            Money = f.Money,
            Health = f.health,
            MaxHealth = f.maxHealth,
            Stamina = (float)Math.Round((double)f.Stamina, 1),
            MaxStamina = f.MaxStamina,
            TileX = tx,
            TileY = ty,
            FacingDirection = f.FacingDirection,
            Facing = FacingName(f.FacingDirection),
            CurrentLocation = Game1.currentLocation?.Name ?? "",
            CurrentLocationDisplay = Game1.currentLocation?.DisplayName ?? "",
            IsRidingHorse = f.isRidingHorse(),
            SelectedSlotIndex = f.CurrentToolIndex,
            IsAutoMoving = f.controller != null,
            IsMoving = f.isMoving(),
            BedTile = new[] { (int)f.mostRecentBed.X, (int)f.mostRecentBed.Y },
            CurrentItem = f.CurrentItem != null ? ToItemInfo(f.CurrentItem) : null,
        };

        // The tile the farmer is facing right now - what an action would act on.
        GameLocation? here = Game1.currentLocation;
        if (here != null)
        {
            try { info.TileInFront = WorldMapper.BuildTileInFront(here, f); }
            catch { info.TileInFront = null; }
        }

        // Pathfinding progress. A progress value that stops rising is how an agent tells
        // "stuck on something" apart from "still walking".
        if (f.controller != null && ActionProcessor.PlannedPath is { } route)
        {
            int remaining = f.controller.pathToEndPoint?.Count ?? 0;
            info.PathTargetX = route.TargetX;
            info.PathTargetY = route.TargetY;
            info.PathLength = route.PlannedSteps;
            info.PathProgress = Math.Max(0, route.PlannedSteps - remaining);
        }

        return info;
    }


    private static List<InventorySlot> BuildInventory()
    {
        var result = new List<InventorySlot>();
        Farmer? f = Game1.player;
        if (f?.Items == null)
            return result;

        for (int i = 0; i < f.Items.Count; i++)
        {
            Item? item = f.Items[i];
            if (item == null)
                continue;

            ItemInfo info = ToItemInfo(item);
            result.Add(new InventorySlot
            {
                Slot = i,
                Name = info.Name,
                Stack = info.Stack,
                Quality = info.Quality,
                Category = info.Category,
                Kind = info.Kind,
                Description = info.Description,
            });
        }
        return result;
    }


    private static ItemInfo ToItemInfo(Item item)
    {
        string kind = "item";
        bool bigCraftable = false;

        if (item is Tool)
            kind = "tool";
        else if (item is StardewValley.Object obj)
        {
            bigCraftable = obj.bigCraftable.Value;
            kind = bigCraftable ? "big_craftable" : "object";
        }

        string description = "";
        try { description = Truncate(item.getDescription(), DescriptionMaxLen); }
        catch { /* some items throw; description stays empty */ }

        return new ItemInfo
        {
            Name = item.DisplayName ?? "",
            Stack = item.Stack,
            Quality = item.Quality,
            Category = item.Category,
            Kind = kind,
            Description = description,
        };
    }


    private static LocationInfo BuildLocationInfo(GameLocation loc, ModConfig cfg)
    {
        var info = new LocationInfo
        {
            Name = loc.Name ?? "",
            DisplayName = loc.DisplayName ?? "",
            IsOutdoors = loc.IsOutdoors,
            IsFarm = loc.IsFarm,
            IsFarmBuildingInterior = loc.isFarmBuildingInterior(),
            TileX = Game1.player != null ? GameQuery.TileOf(Game1.player).x : 0,
            TileY = Game1.player != null ? GameQuery.TileOf(Game1.player).y : 0,
            NearbyCharacterCount = loc.characters?.Count ?? 0,
            NearbyObjectCount = loc.objects == null ? 0 : loc.objects.Count(),
        };

        if (Game1.player != null)
        {
            (int px, int py) = GameQuery.TileOf(Game1.player);

            try { info.AsciiMap = WorldMapper.BuildAsciiMap(loc, px, py, cfg.AsciiMapRadius); }
            catch { info.AsciiMap = ""; }
        }

        try { info.Warps = WorldMapper.BuildWarps(loc); }
        catch { info.Warps = new List<WarpInfo>(); }

        // Work available anywhere in this location, so planning does not have to guess coordinates.
        try
        {
            int cx = info.TileX;
            int cy = info.TileY;
            const int wholeMap = 200;   // the collectors clamp to the map bounds

            List<Vector2> tillable = ChoreController.CollectTillable(loc, cx, cy, wholeMap);
            List<Vector2> soil = ChoreController.CollectEmptySoil(loc, cx, cy, wholeMap);

            info.Work = new WorkSummaryInfo
            {
                Tillable = tillable.Count,
                EmptySoil = soil.Count,
                Debris = ChoreController.CollectDebris(loc, cx, cy, wholeMap).Count,
                ThirstyCrops = ChoreController.CollectThirstyCrops(loc, cx, cy, wholeMap).Count,
                ReadyCrops = ChoreController.CollectReadyCrops(loc, cx, cy, wholeMap).Count,
                TillableSamples = tillable
                    .OrderBy(t => Math.Abs((int)t.X - cx) + Math.Abs((int)t.Y - cy))
                    .Take(3).Select(t => new[] { (int)t.X, (int)t.Y }).ToList(),
                EmptySoilSamples = soil
                    .OrderBy(t => Math.Abs((int)t.X - cx) + Math.Abs((int)t.Y - cy))
                    .Take(3).Select(t => new[] { (int)t.X, (int)t.Y }).ToList(),
            };
        }
        catch { }

        return info;
    }


    private static List<CharacterInfo> BuildCharacters(GameLocation loc)
    {
        var result = new List<CharacterInfo>();
        Farmer me = Game1.player;
        if (loc.characters == null || me == null)
            return result;

        foreach (NPC npc in loc.characters)
        {
            if (npc == null)
                continue;

            int? hearts = null;
            try
            {
                if (npc.IsVillager)
                    hearts = me.getFriendshipHeartLevelForNPC(npc.Name);
            }
            catch { hearts = null; }

            (int tx, int ty) = ((int)npc.Tile.X, (int)npc.Tile.Y);

            result.Add(new CharacterInfo
            {
                Name = npc.Name ?? "",
                DisplayName = npc.displayName ?? "",
                TileX = tx,
                TileY = ty,
                IsVillager = npc.IsVillager,
                FriendshipHearts = hearts,
            });
        }
        return result;
    }


    private static List<WorldObjectInfo> BuildNearbyObjects(GameLocation loc, ModConfig cfg)
    {
        var result = new List<WorldObjectInfo>();
        if (loc.objects == null || Game1.player == null)
            return result;

        (int px, int py) = GameQuery.TileOf(Game1.player);
        int r = cfg.ScanRadius;

        foreach (KeyValuePair<Vector2, StardewValley.Object> pair in loc.objects.Pairs)
        {
            Vector2 tile = pair.Key;
            StardewValley.Object obj = pair.Value;
            if (obj == null)
                continue;

            int tx = (int)tile.X, ty = (int)tile.Y;
            if (Math.Abs(tx - px) > r || Math.Abs(ty - py) > r)
                continue;

            bool isReady;
            try { isReady = obj.IsSprinkler() || obj.readyForHarvest.Value; }
            catch { isReady = false; }

            result.Add(new WorldObjectInfo
            {
                TileX = tx,
                TileY = ty,
                Name = obj.Name ?? "",
                DisplayName = obj.DisplayName ?? "",
                BigCraftable = obj.bigCraftable.Value,
                IsReady = isReady,
            });

            if (result.Count >= cfg.MaxNearbyEntries)
                break;
        }
        return result;
    }


    private static List<CropInfo> BuildCrops(GameLocation loc, ModConfig cfg)
    {
        var result = new List<CropInfo>();
        if (loc.terrainFeatures == null || Game1.player == null)
            return result;

        (int px, int py) = GameQuery.TileOf(Game1.player);
        int r = cfg.ScanRadius;

        foreach (SerializableDictionary<Vector2, TerrainFeature> chunk in loc.terrainFeatures)
        {
            if (chunk == null)
                continue;

            foreach (KeyValuePair<Vector2, TerrainFeature> pair in chunk)
            {
                Vector2 tile = pair.Key;
                TerrainFeature feature = pair.Value;

                int tx = (int)tile.X, ty = (int)tile.Y;
                if (Math.Abs(tx - px) > r || Math.Abs(ty - py) > r)
                    continue;

                if (feature is not HoeDirt dirt || dirt.crop == null)
                    continue;

                result.Add(BuildCropInfo(tx, ty, dirt.crop, dirt, loc));

                if (result.Count >= cfg.MaxNearbyEntries)
                    return result;
            }
        }
        return result;
    }


    private static CropInfo BuildCropInfo(int tx, int ty, Crop crop, HoeDirt dirt, GameLocation loc)
    {
        string name = "";
        try { name = ItemRegistry.GetDataOrErrorItem(crop.indexOfHarvest.Value).DisplayName; }
        catch { name = crop.indexOfHarvest.Value ?? "?"; }

        bool isReady;
        try { isReady = dirt.readyForHarvest(); }
        catch { isReady = false; }

        bool inSeason;
        try { inSeason = crop.IsInSeason(loc); }
        catch { inSeason = false; }

        return new CropInfo
        {
            TileX = tx,
            TileY = ty,
            Name = name,
            Phase = crop.currentPhase.Value,
            DaysUntilNextPhase = crop.dayOfCurrentPhase.Value,
            IsReady = isReady,
            IsWatered = dirt.isWatered(),
            IsFullyGrown = crop.fullyGrown.Value,
            IsDead = crop.dead.Value,
            IsInSeason = inSeason,
        };
    }


    private static List<QuestInfo> BuildQuests()
    {
        var result = new List<QuestInfo>();
        var log = Game1.player?.questLog;
        if (log == null)
            return result;

        foreach (Quest quest in log)
        {
            if (quest == null)
                continue;

            string objective = "";
            try { objective = Truncate(quest.currentObjective, DescriptionMaxLen); }
            catch { objective = ""; }

            var info = new QuestInfo
            {
                Name = quest.GetName(),
                Objective = objective,
                IsCompleted = quest.completed.Value,
            };

            try { info.Id = quest.id?.Value ?? ""; } catch { }
            try { info.Accepted = quest.accepted.Value; } catch { }
            try { info.IsDaily = quest.dailyQuest.Value; } catch { }
            try { info.DaysLeft = quest.daysLeft.Value; } catch { }

            result.Add(info);
        }

        // The first unfinished quest is the one the player should be working on. Without this
        // marker an agent has no thread to follow and just wanders the farm.
        foreach (QuestInfo info in result)
        {
            if (info.IsCompleted || !info.Accepted)
                continue;

            info.IsPrimary = true;
            break;
        }

        return result;
    }


    private static string FacingName(int direction) => direction switch
    {
        0 => "up",
        1 => "right",
        2 => "down",
        3 => "left",
        _ => "unknown",
    };

    private static string Truncate(string? text, int max)
    {
        if (string.IsNullOrEmpty(text))
            return "";

        string flat = text.Replace("\n", " ").Replace("\r", " ").Trim();
        return flat.Length <= max ? flat : flat[..max] + "…";
    }
}
