using System.Text.Json.Serialization;

namespace StateBridge.Models;

/// <summary>Mod settings read from config.json.</summary>
public class ModConfig
{
    /// <summary>Local TCP port for the HTTP API.</summary>
    public int Port { get; set; } = 8787;

    /// <summary>Allow POST /action to mutate the game (disable for read-only testing).</summary>
    public bool EnableActions { get; set; } = true;

    /// <summary>Bind address. 127.0.0.1 keeps it local-only; 0.0.0.0 exposes it to LAN.</summary>
    public string BindAddress { get; set; } = "127.0.0.1";

    /// <summary>Draw the agents' activity down the left of the screen, for people watching.</summary>
    public bool ShowOverlay { get; set; } = true;

    /// <summary>How many activity lines the overlay keeps on screen at once.</summary>
    public int OverlayLines { get; set; } = 26;

    /// <summary>Overlay panel width in pixels.</summary>
    public int OverlayWidth { get; set; } = 470;

    /// <summary>How fast a too-long line slides, in pixels per second. Slow on purpose.</summary>
    public int OverlayScrollSpeed { get; set; } = 34;

    /// <summary>Left edge of the panel. 0 is the screen edge.</summary>
    public int OverlayLeft { get; set; } = 12;

    /// <summary>
    /// Top edge of the panel. Sits under the progress strip, in the top-left corner.
    /// </summary>
    public int OverlayTop { get; set; } = 40;

    /// <summary>Seconds before an overlay line fades out. 0 keeps everything until it scrolls off.</summary>
    public int OverlayFadeSeconds { get; set; } = 0;

    /// <summary>Shows the streamer's spoken line as a subtitle at the bottom of the screen.</summary>
    public bool ShowSubtitle { get; set; } = true;

    /// <summary>Seconds a subtitle stays before fading. 0 keeps it until the next one replaces it.</summary>
    public int SubtitleSeconds { get; set; } = 12;

    /// <summary>Shows the room's chat down the right of the screen.</summary>
    public bool ShowChat { get; set; } = true;

    /// <summary>How many chat messages stay on screen at once.</summary>
    public int ChatLines { get; set; } = 8;

    /// <summary>Shows the day/quest/money strip across the top.</summary>
    public bool ShowProgress { get; set; } = true;

    /// <summary>Scales every panel at once. Raise it for a small stream window, lower it to see more.</summary>
    public float PanelScale { get; set; } = 1.0f;

    /// <summary>
    /// Fill the screen with the game window on launch, keeping it a window - not fullscreen.
    /// </summary>
    public bool MaximizeWindow { get; set; } = true;

    /// <summary>How many nearby tiles to scan for objects/crops when building state.</summary>
    public int ScanRadius { get; set; } = 12;

    /// <summary>Hard cap on nearby object entries (keeps payloads small).</summary>
    public int MaxNearbyEntries { get; set; } = 60;

    /// <summary>
    /// Half-size of the ASCII character map sent in /state. 10 means a 21x21 grid.
    /// Bigger sees further but costs more tokens every single step.
    /// </summary>
    public int AsciiMapRadius { get; set; } = 10;
}


/// <summary>Root payload returned by GET /state.</summary>
public class GameStateSnapshot
{
    [JsonPropertyName("tick")] public long Tick { get; set; }
    [JsonPropertyName("game")] public GameInfo Game { get; set; } = new();
    [JsonPropertyName("location")] public LocationInfo? Location { get; set; }
    [JsonPropertyName("player")] public PlayerInfo Player { get; set; } = new();
    [JsonPropertyName("inventory")] public List<InventorySlot> Inventory { get; set; } = new();
    [JsonPropertyName("nearby_characters")] public List<CharacterInfo> NearbyCharacters { get; set; } = new();
    [JsonPropertyName("nearby_objects")] public List<WorldObjectInfo> NearbyObjects { get; set; } = new();
    [JsonPropertyName("crops")] public List<CropInfo> Crops { get; set; } = new();
    [JsonPropertyName("quests")] public List<QuestInfo> Quests { get; set; } = new();

    /// <summary>Status of the running chore (reflex macro), or "idle". While one runs, don't interfere.</summary>
    [JsonPropertyName("chore")] public string Chore { get; set; } = "idle";

    /// <summary>
    /// What the last finished chore actually achieved - "water_all: 0 done, 33 given up of 33 in
    /// 134s". A chore that did nothing used to be indistinguishable from one that did everything.
    /// </summary>
    [JsonPropertyName("chore_last")] public string? ChoreLast { get; set; }

    [JsonPropertyName("ui")] public UiInfo? Ui { get; set; }
    [JsonPropertyName("display")] public DisplayInfo? Display { get; set; }
}


public class GameInfo
{
    [JsonPropertyName("day_of_month")] public int DayOfMonth { get; set; }
    [JsonPropertyName("season")] public string Season { get; set; } = "";
    [JsonPropertyName("year")] public int Year { get; set; }
    [JsonPropertyName("time_of_day")] public int TimeOfDay { get; set; }

    /// <summary>The game's own weekday, straight from its calendar (shops close on certain days).</summary>
    [JsonPropertyName("day_of_week")] public string DayOfWeek { get; set; } = "";

    /// <summary>Total days elapsed since the save began - useful for "how far in are we".</summary>
    [JsonPropertyName("total_days")] public int TotalDays { get; set; }
    [JsonPropertyName("is_raining")] public bool IsRaining { get; set; }
    [JsonPropertyName("is_lightning")] public bool IsLightning { get; set; }
    [JsonPropertyName("is_snowing")] public bool IsSnowing { get; set; }
    [JsonPropertyName("is_debris_weather")] public bool IsDebrisWeather { get; set; }
    [JsonPropertyName("is_festival")] public bool IsFestival { get; set; }
    [JsonPropertyName("is_wedding")] public bool IsWedding { get; set; }
    [JsonPropertyName("is_event_up")] public bool IsEventUp { get; set; }
    [JsonPropertyName("is_dialogue_up")] public bool IsDialogueUp { get; set; }
    [JsonPropertyName("is_menu_open")] public bool IsMenuOpen { get; set; }
    [JsonPropertyName("is_paused")] public bool IsPaused { get; set; }
    [JsonPropertyName("is_game_paused")] public bool IsGamePaused { get; set; }
    [JsonPropertyName("is_player_free")] public bool IsPlayerFree { get; set; }
    [JsonPropertyName("has_save")] public bool HasSave { get; set; }

    /// <summary>In-game minutes left before the 2:00 AM collapse. Tells an agent when to sleep.</summary>
    [JsonPropertyName("minutes_until_morning")] public int MinutesUntilMorning { get; set; }

    [JsonPropertyName("blocked_reason")] public string BlockedReason { get; set; } = "";
}


public class LocationInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("is_outdoors")] public bool IsOutdoors { get; set; }
    [JsonPropertyName("is_farm")] public bool IsFarm { get; set; }
    [JsonPropertyName("is_farm_building_interior")] public bool IsFarmBuildingInterior { get; set; }
    [JsonPropertyName("tile_x")] public int TileX { get; set; }
    [JsonPropertyName("tile_y")] public int TileY { get; set; }
    [JsonPropertyName("character_count")] public int NearbyCharacterCount { get; set; }
    [JsonPropertyName("object_count")] public int NearbyObjectCount { get; set; }

    /// <summary>A character grid of the area around the player (@ = you). See the legend in the prompt.</summary>
    [JsonPropertyName("ascii_map")] public string AsciiMap { get; set; } = "";

    /// <summary>Every exit and door here, with where it leads. Lets an agent pick the right door.</summary>
    [JsonPropertyName("warps")] public List<WarpInfo> Warps { get; set; } = new();

    /// <summary>
    /// What work exists in this whole location, with example tiles. The agent cannot plan farm
    /// work without knowing where the farmable ground actually is - guessing coordinates is how it
    /// ends up hoeing a path outside the farmhouse for three days.
    /// </summary>
    [JsonPropertyName("work")] public WorkSummaryInfo? Work { get; set; }

    /// <summary>
    /// Shops and buildings whose door shuts outside opening hours, with the hours the door itself
    /// declares. See ShopDoorInfo - the hours are on the door tile, and nothing used to read them.
    /// </summary>
    [JsonPropertyName("shops")] public List<ShopDoorInfo> Shops { get; set; } = new();

    /// <summary>
    /// Every KIND of tile-action this place has, each with the nearest tile that does it: a letter box,
    /// a notice board, a bed, a shipping bin, a door.
    ///
    /// A person walking through town sees the board on the wall and reads it. The agent saw nothing at
    /// all: the state only ever described the tiles it was already standing on, so mail sat unread in
    /// the box and quests pinned to the board were never once taken. This is the game's own "Action"
    /// property, grouped by verb, so the agent can see what this place offers before walking anywhere.
    /// </summary>
    [JsonPropertyName("place_actions")] public List<PlaceActionInfo> PlaceActions { get; set; } = new();
}


/// <summary>One kind of tile-action present in a location, with the nearest example of it.</summary>
public class PlaceActionInfo
{
    /// <summary>The action's first word, as the game writes it: "Mailbox", "Board", "Bed", ...</summary>
    [JsonPropertyName("verb")] public string Verb { get; set; } = "";

    /// <summary>The whole action string written on that tile.</summary>
    [JsonPropertyName("action")] public string Action { get; set; } = "";

    /// <summary>The nearest tile with this action: [x, y].</summary>
    [JsonPropertyName("at")] public List<int> At { get; set; } = new();

    /// <summary>How many tiles here carry this action - a doorstep and a row of thirty doors differ.</summary>
    [JsonPropertyName("count")] public int Count { get; set; }

    /// <summary>Extra arguments the action declares after the verb (e.g. a warp's destination).</summary>
    [JsonPropertyName("args")] public string Args { get; set; } = "";
}


/// <summary>
/// A door that is shut outside opening hours, with the hours taken from the door tile itself.
///
/// Stardew puts the answer on the tile: `Action: LockedDoorWarp &lt;tile&gt; &lt;dest&gt; &lt;Location&gt;
/// &lt;openTime&gt; &lt;closeTime&gt;` (StardewValley.GameLocation:9539), and when you knock it says so out
/// loud - `Strings/Locations:LockedDoor_OpenRange` renders as "上锁了……（营业时间：9:00~21:00）".
/// The agent knocked on Pierre's door seventeen times in one day with that sentence on screen, so
/// this turns the sentence into a number it can plan around.
/// </summary>
public class ShopDoorInfo
{
    /// <summary>The door tile: [x, y].</summary>
    [JsonPropertyName("at")] public List<int> At { get; set; } = new();

    /// <summary>The location this door leads into, e.g. "SeedShop".</summary>
    [JsonPropertyName("leads_to")] public string LeadsTo { get; set; } = "";

    /// <summary>Opening and closing time on the game clock (900 = 9:00), as written on the tile.</summary>
    [JsonPropertyName("opens_at")] public int OpensAt { get; set; }
    [JsonPropertyName("closes_at")] public int ClosesAt { get; set; }

    /// <summary>The same two times, written the way the game writes them ("9:00am").</summary>
    [JsonPropertyName("opens_text")] public string OpensText { get; set; } = "";
    [JsonPropertyName("closes_text")] public string ClosesText { get; set; } = "";

    /// <summary>Whether the clock is inside the door's own range right now. The action is the authority.</summary>
    [JsonPropertyName("open_now")] public bool OpenNow { get; set; }

    /// <summary>Extra reason the door is shut today, in the game's own words (e.g. SeedShop shuts on Wednesdays).</summary>
    [JsonPropertyName("note")] public string Note { get; set; } = "";
}


public class WorkSummaryInfo
{
    [JsonPropertyName("tillable")] public int Tillable { get; set; }
    [JsonPropertyName("empty_soil")] public int EmptySoil { get; set; }
    [JsonPropertyName("debris")] public int Debris { get; set; }
    [JsonPropertyName("thirsty_crops")] public int ThirstyCrops { get; set; }
    [JsonPropertyName("ready_crops")] public int ReadyCrops { get; set; }
    [JsonPropertyName("tillable_samples")] public List<int[]> TillableSamples { get; set; } = new();
    [JsonPropertyName("empty_soil_samples")] public List<int[]> EmptySoilSamples { get; set; } = new();
}


public class WarpInfo
{
    [JsonPropertyName("x")] public int X { get; set; }
    [JsonPropertyName("y")] public int Y { get; set; }
    [JsonPropertyName("target")] public string Target { get; set; } = "";
    [JsonPropertyName("target_x")] public int TargetX { get; set; }
    [JsonPropertyName("target_y")] public int TargetY { get; set; }
    [JsonPropertyName("is_door")] public bool IsDoor { get; set; }
}


/// <summary>The single tile the player is facing - what an action would act on.</summary>
public class TileInFrontInfo
{
    [JsonPropertyName("x")] public int X { get; set; }
    [JsonPropertyName("y")] public int Y { get; set; }
    [JsonPropertyName("passable")] public bool Passable { get; set; }
    [JsonPropertyName("water")] public bool Water { get; set; }
    [JsonPropertyName("diggable")] public bool Diggable { get; set; }
    [JsonPropertyName("object")] public string Object { get; set; } = "";
    [JsonPropertyName("ground")] public string Ground { get; set; } = "";
    [JsonPropertyName("crop")] public string Crop { get; set; } = "";
    [JsonPropertyName("crop_ready")] public bool? CropReady { get; set; }
    [JsonPropertyName("watered")] public bool? Watered { get; set; }
    [JsonPropertyName("character")] public string Character { get; set; } = "";
    [JsonPropertyName("can_interact")] public bool CanInteract { get; set; }
}


public class PlayerInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("money")] public int Money { get; set; }
    [JsonPropertyName("health")] public int Health { get; set; }
    [JsonPropertyName("max_health")] public int MaxHealth { get; set; }
    [JsonPropertyName("stamina")] public float Stamina { get; set; }
    [JsonPropertyName("max_stamina")] public float MaxStamina { get; set; }
    [JsonPropertyName("tile_x")] public int TileX { get; set; }
    [JsonPropertyName("tile_y")] public int TileY { get; set; }
    [JsonPropertyName("facing_direction")] public int FacingDirection { get; set; }
    [JsonPropertyName("facing")] public string Facing { get; set; } = "";
    [JsonPropertyName("current_location")] public string CurrentLocation { get; set; } = "";
    [JsonPropertyName("current_location_display")] public string CurrentLocationDisplay { get; set; } = "";
    [JsonPropertyName("is_riding_horse")] public bool IsRidingHorse { get; set; }
    [JsonPropertyName("selected_slot_index")] public int SelectedSlotIndex { get; set; }
    [JsonPropertyName("is_auto_moving")] public bool IsAutoMoving { get; set; }
    [JsonPropertyName("is_moving")] public bool IsMoving { get; set; }

    /// <summary>Where go_to is heading, and how far along the planned route we are.</summary>
    [JsonPropertyName("path_target_x")] public int? PathTargetX { get; set; }
    [JsonPropertyName("path_target_y")] public int? PathTargetY { get; set; }
    [JsonPropertyName("path_progress")] public int? PathProgress { get; set; }
    [JsonPropertyName("path_length")] public int? PathLength { get; set; }

    /// <summary>What the player is facing right now - the tile an action would act on.</summary>
    [JsonPropertyName("tile_in_front")] public TileInFrontInfo? TileInFront { get; set; }

    /// <summary>Where the player's bed is, so an agent knows where to go to end the day.</summary>
    [JsonPropertyName("bed_tile")] public int[] BedTile { get; set; } = Array.Empty<int>();

    /// <summary>
    /// How many letters the game is holding for this player (Farmer.mailbox). Nothing exposed it, so
    /// the mailbox was never once opened and everything the game sends by post - quests, recipes, the
    /// letters that move the story - stayed in the box.
    /// </summary>
    [JsonPropertyName("mail_waiting")] public int MailWaiting { get; set; }

    [JsonPropertyName("current_item")] public ItemInfo? CurrentItem { get; set; }

    /// <summary>
    /// The stack the player is carrying on the mouse cursor.
    ///
    /// This is NOT "current_item" - that is the toolbar selection. A farmer can be walking around
    /// with a whole stack stuck to the cursor and the old payload said nothing about it, so an item
    /// that was bought but never landed in the bag was simply invisible: the shop looked like it
    /// would not close "for no reason". A player sees this instantly; the agent had no way to.
    /// </summary>
    [JsonPropertyName("cursor_item")] public ItemInfo? CursorItem { get; set; }
}


public class ItemInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("id")] public string ItemId { get; set; } = "";
    [JsonPropertyName("stack")] public int Stack { get; set; }
    [JsonPropertyName("quality")] public int Quality { get; set; }
    [JsonPropertyName("category")] public int Category { get; set; }

    /// <summary>
    /// What the thing IS, in the game's own words: 种子 / 蔬菜 / 水果 / 采集品 / 工具.
    ///
    /// A name and a count are not enough. A harvested parsnip and a packet of parsnip seeds are both
    /// called "防风草..." and sit in the same bag, and an agent that had just bought seeds read the
    /// seven parsnips it was carrying as those seeds and went off to sow them. The game already keeps
    /// the two in different categories; this is that category, said out loud.
    /// </summary>
    [JsonPropertyName("category_name")] public string CategoryName { get; set; } = "";

    [JsonPropertyName("kind")] public string Kind { get; set; } = "item";
    [JsonPropertyName("description")] public string Description { get; set; } = "";

    /// <summary>
    /// How much the tool still holds, for tools that run out (the watering can). -1 means the tool
    /// does not hold anything. A watering can with 0 in it looks identical to a full one from
    /// outside, which is how an agent ends up swinging an empty can all day.
    /// </summary>
    [JsonPropertyName("charges")] public int Charges { get; set; } = -1;

    /// <summary>What "charges" counts up to, or -1 when the tool does not run out.</summary>
    [JsonPropertyName("charges_max")] public int ChargesMax { get; set; } = -1;

    /// <summary>The tool's upgrade level, or 0 for a plain item.</summary>
    [JsonPropertyName("upgrade_level")] public int UpgradeLevel { get; set; }
}


public class InventorySlot
{
    [JsonPropertyName("slot")] public int Slot { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("id")] public string ItemId { get; set; } = "";
    [JsonPropertyName("stack")] public int Stack { get; set; }
    [JsonPropertyName("quality")] public int Quality { get; set; }
    [JsonPropertyName("category")] public int Category { get; set; }
    [JsonPropertyName("category_name")] public string CategoryName { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "item";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("charges")] public int Charges { get; set; } = -1;
    [JsonPropertyName("charges_max")] public int ChargesMax { get; set; } = -1;
    [JsonPropertyName("upgrade_level")] public int UpgradeLevel { get; set; }
}


public class CharacterInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("tile_x")] public int TileX { get; set; }
    [JsonPropertyName("tile_y")] public int TileY { get; set; }
    [JsonPropertyName("is_villager")] public bool IsVillager { get; set; }
    [JsonPropertyName("friendship_hearts")] public int? FriendshipHearts { get; set; }
}


public class WorldObjectInfo
{
    [JsonPropertyName("tile_x")] public int TileX { get; set; }
    [JsonPropertyName("tile_y")] public int TileY { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("display_name")] public string DisplayName { get; set; } = "";
    [JsonPropertyName("big_craftable")] public bool BigCraftable { get; set; }
    [JsonPropertyName("is_ready")] public bool IsReady { get; set; }
}


public class CropInfo
{
    [JsonPropertyName("tile_x")] public int TileX { get; set; }
    [JsonPropertyName("tile_y")] public int TileY { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("phase")] public int Phase { get; set; }
    [JsonPropertyName("days_until_next_phase")] public int DaysUntilNextPhase { get; set; }
    [JsonPropertyName("is_ready")] public bool IsReady { get; set; }
    [JsonPropertyName("is_watered")] public bool IsWatered { get; set; }
    [JsonPropertyName("is_fully_grown")] public bool IsFullyGrown { get; set; }
    [JsonPropertyName("is_dead")] public bool IsDead { get; set; }
    [JsonPropertyName("is_in_season")] public bool IsInSeason { get; set; }
}


public class QuestInfo
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("objective")] public string Objective { get; set; } = "";
    [JsonPropertyName("is_completed")] public bool IsCompleted { get; set; }
    [JsonPropertyName("accepted")] public bool Accepted { get; set; }
    [JsonPropertyName("is_daily")] public bool IsDaily { get; set; }
    [JsonPropertyName("days_left")] public int DaysLeft { get; set; }

    /// <summary>True for the first unfinished quest - the one the player should be working on.</summary>
    [JsonPropertyName("is_primary")] public bool IsPrimary { get; set; }
}


/// <summary>A clickable element found in the open menu, addressable by name.</summary>
public class MenuButtonInfo
{
    /// <summary>
    /// The handle that names exactly one element. Send THIS to menu_click.
    ///
    /// The friendly name could point at two things - an ItemGrabMenu has a bag row and a container row
    /// that both answer to "inventory.12" - and the bridge silently clicked whichever came first. A
    /// handle is allowed to be ugly; it is not allowed to be a coin flip.
    /// </summary>
    [JsonPropertyName("id")] public string Id { get; set; } = "";

    /// <summary>Name to pass back to the menu_click action (kept for backwards compatibility).</summary>
    [JsonPropertyName("name")] public string Name { get; set; } = "";

    /// <summary>Menu field the element was read from, with its path (e.g. ItemsToGrabMenu.inventory).</summary>
    [JsonPropertyName("source")] public string Source { get; set; } = "";

    /// <summary>Which side of the menu: bag / container / shop / control.</summary>
    [JsonPropertyName("grid")] public string Grid { get; set; } = "";

    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("x")] public int X { get; set; }
    [JsonPropertyName("y")] public int Y { get; set; }
    [JsonPropertyName("width")] public int Width { get; set; }
    [JsonPropertyName("height")] public int Height { get; set; }
    [JsonPropertyName("visible")] public bool Visible { get; set; }

    /// <summary>
    /// What this control is SHOWING, when the game draws something on it.
    ///
    /// A shop's panel is a grid of buttons named "4", "12", "inventory.0" - names are all the agent
    /// had, and a name is not a thing. It asked to "go and sell the parsnips" and had nowhere to click,
    /// because nothing anywhere said that the slot showing a parsnip is a parsnip. The shelf rows were
    /// joined onto their goods long ago for exactly this reason; the player's own half of the same
    /// screen was never done.
    /// </summary>
    [JsonPropertyName("item")] public string Item { get; set; } = "";

    /// <summary>
    /// Whether the GAME would accept this item from this control right now.
    ///
    /// Asked rather than assumed: a shop sets the highlight function that decides which of your slots
    /// it will buy, so this is the shop's own answer for this slot at this moment. False means the
    /// click would do nothing, which is worth knowing before spending a step on it.
    /// </summary>
    [JsonPropertyName("sellable")] public bool Sellable { get; set; }

    /// <summary>How many are stacked on this control, when the game draws an item here.</summary>
    [JsonPropertyName("count")] public int Count { get; set; }

    /// <summary>tool / item, when the game draws something here - an axe is not a parsnip.</summary>
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
}


public class DialogueResponseInfo
{
    [JsonPropertyName("index")] public int Index { get; set; }
    [JsonPropertyName("key")] public string Key { get; set; } = "";
    [JsonPropertyName("text")] public string Text { get; set; } = "";
}


public class DialogueInfo
{
    [JsonPropertyName("speaker")] public string Speaker { get; set; } = "";
    [JsonPropertyName("text")] public string Text { get; set; } = "";
    [JsonPropertyName("selected_index")] public int SelectedIndex { get; set; }
    [JsonPropertyName("responses")] public List<DialogueResponseInfo> Responses { get; set; } = new();
}


public class SaveSlotInfo
{
    [JsonPropertyName("index")] public int Index { get; set; }
    [JsonPropertyName("farmer_name")] public string FarmerName { get; set; } = "";
    [JsonPropertyName("slot_name")] public string SlotName { get; set; } = "";
    [JsonPropertyName("slot_sub_name")] public string SlotSubName { get; set; } = "";
}


/// <summary>A text input found in the open menu (name box, farm name box, ...).</summary>
public class TextFieldInfo
{
    [JsonPropertyName("field")] public string Field { get; set; } = "";
    [JsonPropertyName("value")] public string Value { get; set; } = "";
}


/// <summary>The character being designed on the character creation screen, as real values.</summary>
public class CharacterDesignInfo
{
    [JsonPropertyName("gender")] public string Gender { get; set; } = "";
    [JsonPropertyName("skin")] public int Skin { get; set; }
    [JsonPropertyName("hair_style")] public int HairStyle { get; set; }
    [JsonPropertyName("accessory")] public int Accessory { get; set; }
    [JsonPropertyName("hair_color")] public int[] HairColor { get; set; } = Array.Empty<int>();
    [JsonPropertyName("eye_color")] public int[] EyeColor { get; set; } = Array.Empty<int>();
    [JsonPropertyName("pants_color")] public int[] PantsColor { get; set; } = Array.Empty<int>();
    [JsonPropertyName("shirt")] public string Shirt { get; set; } = "";
    [JsonPropertyName("pants")] public string Pants { get; set; } = "";
    [JsonPropertyName("favorite_thing")] public string FavoriteThing { get; set; } = "";
    [JsonPropertyName("farm_name")] public string FarmName { get; set; } = "";

    /// <summary>Farm layouts this game/mod offers, so the agent knows its options.</summary>
    [JsonPropertyName("available_farm_types")] public List<string> AvailableFarmTypes { get; set; } = new();
}


/// <summary>Everything the agent needs to drive menus and dialogue.</summary>
public class UiInfo
{
    [JsonPropertyName("menu_name")] public string MenuName { get; set; } = "";
    [JsonPropertyName("menu_type")] public string MenuType { get; set; } = "";

    /// <summary>Set when this menu is a submenu of another (e.g. LoadGameMenu under TitleMenu).</summary>
    [JsonPropertyName("parent_menu")] public string ParentMenu { get; set; } = "";

    [JsonPropertyName("buttons")] public List<MenuButtonInfo> Buttons { get; set; } = new();
    [JsonPropertyName("text_fields")] public List<TextFieldInfo> TextFields { get; set; } = new();
    [JsonPropertyName("dialogue")] public DialogueInfo? Dialogue { get; set; }
    [JsonPropertyName("save_slots")] public List<SaveSlotInfo> SaveSlots { get; set; } = new();
    [JsonPropertyName("character_design")] public CharacterDesignInfo? CharacterDesign { get; set; }

    /// <summary>What is actually in the open CONTAINER (a chest, a fridge, the shipping bin).</summary>
    [JsonPropertyName("container")] public List<InventorySlot> Container { get; set; } = new();

    /// <summary>What is actually on the shelves, joined onto the slot names menu_click takes.</summary>
    [JsonPropertyName("shop")] public List<ShopItemInfo> Shop { get; set; } = new();

    /// <summary>
    /// What the open MENU itself is holding (<c>IClickableMenu.heldItem</c>), if anything.
    ///
    /// A second place an item can be "in hand", and the one nobody looked at. While a menu holds an
    /// item, Stardew's own <c>readyToClose()</c> is false - and both the close button and the escape
    /// key go through it. So the shop "refuses to close for no reason" and every later click is
    /// swallowed, while the payload said nothing about a hand that was full.
    /// </summary>
    [JsonPropertyName("held_item")] public string HeldItem { get; set; } = "";
}


/// <summary>
/// One row of an open shop.
///
/// The shop's clickable rows carry no label at all: the reflection collector reports them as a
/// column of blank rectangles named "0", "1", "2", ..., which is why an agent can only click them
/// blind and cannot tell a seed from a scythe. The item, the price and the stock live in two other
/// lists on the same menu, so this is where they get joined back onto the row.
/// </summary>
public class ShopItemInfo
{
    /// <summary>The name to hand to menu_click (e.g. "0").</summary>
    [JsonPropertyName("slot")] public string Slot { get; set; } = "";

    [JsonPropertyName("item")] public string Item { get; set; } = "";
    [JsonPropertyName("item_id")] public string ItemId { get; set; } = "";
    [JsonPropertyName("category")] public int Category { get; set; }

    /// <summary>种子 / 蔬菜 / 工具 ... - so a shelf row can be read as what it is, not just its name.</summary>
    [JsonPropertyName("category_name")] public string CategoryName { get; set; } = "";

    [JsonPropertyName("price")] public int Price { get; set; }

    /// <summary>How many are left, or -1 when the shop never runs out.</summary>
    [JsonPropertyName("stock")] public int Stock { get; set; } = -1;

    [JsonPropertyName("affordable")] public bool Affordable { get; set; }
}


/// <summary>How the game is being rendered, so an agent (or its operator) can reason about it.</summary>
public class DisplayInfo
{
    [JsonPropertyName("zoom_level")] public float ZoomLevel { get; set; }
    [JsonPropertyName("desired_zoom_level")] public float DesiredZoomLevel { get; set; }
    [JsonPropertyName("ui_scale")] public float UiScale { get; set; }

    /// <summary>
    /// Whether the game window is the ACTIVE window right now. SDV ignores menu input while it is not,
    /// and Windows eats the click that activates a background window - so this is the difference
    /// between "the game refused" and "the game never heard you".
    /// </summary>
    [JsonPropertyName("is_active")] public bool IsActive { get; set; }

    /// <summary>The game's own setting: does it stop updating while the window is not focused.</summary>
    [JsonPropertyName("pause_when_unfocused")] public bool PauseWhenUnfocused { get; set; }
    [JsonPropertyName("desired_ui_scale")] public float DesiredUiScale { get; set; }
    [JsonPropertyName("render_width")] public int RenderWidth { get; set; }
    [JsonPropertyName("render_height")] public int RenderHeight { get; set; }
    [JsonPropertyName("preferred_resolution_x")] public int PreferredResolutionX { get; set; }
    [JsonPropertyName("preferred_resolution_y")] public int PreferredResolutionY { get; set; }
    [JsonPropertyName("fullscreen")] public bool Fullscreen { get; set; }
    [JsonPropertyName("borderless")] public bool Borderless { get; set; }
}


/// <summary>Response envelope for POST /action and errors.</summary>
public class ActionResult
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("action")] public string Action { get; set; } = "";
    [JsonPropertyName("detail")] public string Detail { get; set; } = "";

    /// <summary>
    /// Machine-readable failure class, so an agent can learn from mistakes instead of re-reading
    /// prose. Empty on success. Examples: NO_PATH, WRONG_TOOL, NOT_ADJACENT, MENU_OPEN.
    /// </summary>
    [JsonPropertyName("reason_code")] public string ReasonCode { get; set; } = "";

    /// <summary>Structured payload for actions that return information (e.g. observe).</summary>
    [JsonPropertyName("data")] public Dictionary<string, object?>? Data { get; set; }
}
