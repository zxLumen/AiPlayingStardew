using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Quests;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
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

    /// <summary>Whether the "keep running when unfocused" correction has been reported yet.</summary>
    private static bool _pauseOffReported;

    /// <summary>
    /// Where the per-place scan reports what it found. The line-by-line helpers have no monitor of
    /// their own, and a scan that finds no verb at all is exactly the news that needs to be readable.
    /// </summary>
    private static IMonitor? _monitor;

    public static string BuildJson(ModConfig cfg, IMonitor monitor)
    {
        _tick++;
        _monitor = monitor;
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

        try { snap.ChoreLast = ChoreController.DescribeLast(); }
        catch (Exception ex) { monitor.Log($"Chore last failed: {ex}", LogLevel.Error); }

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

        try { info.RenderWidth = Game1.graphics.GraphicsDevice.Viewport.Width; }
        catch { }
        try { info.RenderHeight = Game1.graphics.GraphicsDevice.Viewport.Height; }
        catch { }

        // THE PRECONDITION OF EVERY CLICK, said out loud.
        //
        // SDV ignores menu input while its window is not the active window, and Windows swallows the
        // click that activates a background window - so a farmer sitting behind a browser gets clicks
        // that are delivered, aimed correctly, and never heard. The bridge enforced this before
        // clicking but never reported it, so the failure read as "the game did not react" and looked
        // like the game's fault. It is a fact about the window, and it belongs in the state.
        try { info.IsActive = NativeInput.IsGameWindowActive(); } catch { }
        // THE GAME MUST KEEP RUNNING WHILE THE WINDOW IS BEHIND SOMEBODY'S WORK.
        //
        // SDV's "pause when window loses focus" does not merely make a background window ignore clicks:
        // it stops the game updating at all - no frame, no input handling, nothing to receive a click by
        // any route. That default is right for one person at one keyboard and wrong for an agent sharing
        // the machine: the farm has to keep being playable while the person works on the other screen.
        // It is a setting in the options menu; this is that setting, held off while the agent plays.
        try
        {
            if (Game1.options.pauseWhenOutOfFocus)
            {
                Game1.options.pauseWhenOutOfFocus = false;
                if (!_pauseOffReported)
                {
                    _pauseOffReported = true;
                    _monitor?.Log("pause-when-unfocused is now OFF: a background window must keep running, "
                                  + "or it cannot receive a click by any route (this is the game's own "
                                  + "options-menu setting, held off while the agent plays)", LogLevel.Info);
                }
            }

            info.PauseWhenUnfocused = Game1.options.pauseWhenOutOfFocus;
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

            // Letters the game is holding for this farmer. Nothing exposed it before, so the mailbox
            // was never once opened and everything the game sends by post stayed in the box.
            MailWaiting = MailCount(f),
        };

        // The stack on the cursor. Until this was exposed, a farmer could be carrying a whole
        // purchase around and the payload would not mention it - so "the shop will not close" and
        // "the seeds never arrived" both looked like the bridge lying rather than an item in hand.
        try { info.CursorItem = f.CursorSlotItem != null ? ToItemInfo(f.CursorSlotItem) : null; }
        catch { info.CursorItem = null; }

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
                ItemId = info.ItemId,
                Stack = info.Stack,
                Quality = info.Quality,
                Category = info.Category,
                CategoryName = info.CategoryName,
                Kind = info.Kind,
                Description = info.Description,
                Charges = info.Charges,
                ChargesMax = info.ChargesMax,
                UpgradeLevel = info.UpgradeLevel,
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

        // A tool that runs out looks exactly like a full one from the outside. Report what it holds,
        // so "swing did nothing" and "swing had nothing to work with" stop looking the same.
        int charges = -1;
        int chargesMax = -1;
        int upgradeLevel = 0;

        if (item is Tool tool)
        {
            upgradeLevel = tool.UpgradeLevel;

            try
            {
                if (tool is WateringCan can)
                {
                    charges = can.WaterLeft;
                    chargesMax = ActionProcessor.CanCapacity(can);
                }
            }
            catch { /* not every tool reports this */ }
        }

        return new ItemInfo
        {
            Name = item.DisplayName ?? "",
            ItemId = SafeItemId(item),
            Stack = item.Stack,
            Quality = item.Quality,
            Category = item.Category,
            CategoryName = CategoryWords(item.Category),
            Kind = kind,
            Description = description,
            Charges = charges,
            ChargesMax = chargesMax,
            UpgradeLevel = upgradeLevel,
        };
    }


    private static string SafeItemId(Item item)
    {
        try { return item.ItemId ?? ""; }
        catch { return ""; }
    }


    private static int MailCount(Farmer f)
    {
        try { return f.mailbox?.Count ?? 0; }
        catch { return 0; }
    }


    /// <summary>
    /// What a thing IS, in the game's own words: 种子 / 蔬菜 / 水果 / 采集品 / 工具.
    ///
    /// A name and a count are not enough to tell a harvested parsnip from a packet of parsnip seeds -
    /// both are "防风草[种子]" in the same bag - and an agent that had just bought seeds read the seven
    /// parsnips it was carrying as those seeds and went off to sow them. The game already files the two
    /// under different categories; this is that category, said out loud, so the answer travels with the
    /// item instead of having to be worked out.
    /// </summary>
    public static string CategoryWords(int category) => category switch
    {
        -74 => "种子",
        -75 => "蔬菜",
        -79 => "水果",
        -80 => "花",
        -81 => "采集品",
        -4 => "鱼",
        -5 => "蛋",
        -6 => "奶",
        -14 => "肉",
        -2 => "宝石",
        -12 => "矿物",
        -15 => "金属",
        -16 => "建材",
        -8 => "合成品",
        -7 => "料理",
        -9 => "家具",
        -20 => "杂物",
        -99 => "工具",
        _ => "",
    };


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

        // Shops whose door shuts outside opening hours, WITH the hours. Nothing read them before, so
        // the plan could only be "go to the shop and find out", and finding out cost seventeen
        // knocks in one day while the game was saying the hours out loud.
        try { info.Shops = WorldMapper.BuildLockedDoors(loc); }
        catch { info.Shops = new List<ShopDoorInfo>(); }

        // What this place OFFERS, one entry per kind of tile-action: a letter box, a notice board, a
        // bed, a bin. Nothing described them, so the mail was never read and the board outside the shop
        // was never once used - the agent had no way to know either existed.
        try
        {
            info.PlaceActions = WorldMapper.BuildPlaceActions(
                loc, info.TileX, info.TileY,
                msg => _monitor?.Log(msg, LogLevel.Info));
        }
        catch { info.PlaceActions = new List<PlaceActionInfo>(); }

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
        //
        // `accepted` was part of this test and it silently killed the marker: Stardew adds some
        // quests - "Introductions" on day one, for one - WITHOUT ever setting accepted, so the only
        // quest on the log was skipped and no quest at all was marked primary. The agent was told in
        // its instructions to follow the primary quest and handed a list with no primary in it. An
        // accepted quest is still preferred, because that is one the player chose; with none
        // accepted, the first unfinished one is the thread. Daily quests come last: they expire.
        for (int pass = 0; pass < 2; pass++)
        {
            foreach (QuestInfo info in result)
            {
                if (info.IsCompleted)
                    continue;
                if (pass == 0 && (info.IsDaily || !info.Accepted))
                    continue;
                if (pass == 1 && info.IsDaily)
                    continue;

                info.IsPrimary = true;
                return result;
            }
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
