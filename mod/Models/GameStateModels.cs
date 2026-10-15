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
    [JsonPropertyName("ui")] public UiInfo? Ui { get; set; }
    [JsonPropertyName("display")] public DisplayInfo? Display { get; set; }
}


public class GameInfo
{
    [JsonPropertyName("day_of_month")] public int DayOfMonth { get; set; }
    [JsonPropertyName("season")] public string Season { get; set; } = "";
    [JsonPropertyName("year")] public int Year { get; set; }
    [JsonPropertyName("time_of_day")] public int TimeOfDay { get; set; }
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

    [JsonPropertyName("current_item")] public ItemInfo? CurrentItem { get; set; }
}


public class ItemInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("stack")] public int Stack { get; set; }
    [JsonPropertyName("quality")] public int Quality { get; set; }
    [JsonPropertyName("category")] public int Category { get; set; }
    [JsonPropertyName("kind")] public string Kind { get; set; } = "item";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
}


public class InventorySlot
{
    [JsonPropertyName("slot")] public int Slot { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("stack")] public int Stack { get; set; }
    [JsonPropertyName("quality")] public int Quality { get; set; }
    [JsonPropertyName("category")] public int Category { get; set; }
    [JsonPropertyName("kind")] public string Kind { get; set; } = "item";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
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
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("objective")] public string Objective { get; set; } = "";
    [JsonPropertyName("is_completed")] public bool IsCompleted { get; set; }
}


/// <summary>A clickable element found in the open menu, addressable by name.</summary>
public class MenuButtonInfo
{
    /// <summary>Name to pass back to the menu_click action.</summary>
    [JsonPropertyName("name")] public string Name { get; set; } = "";

    /// <summary>Menu field the element was read from (e.g. buttons, slotButtons).</summary>
    [JsonPropertyName("source")] public string Source { get; set; } = "";

    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("x")] public int X { get; set; }
    [JsonPropertyName("y")] public int Y { get; set; }
    [JsonPropertyName("width")] public int Width { get; set; }
    [JsonPropertyName("height")] public int Height { get; set; }
    [JsonPropertyName("visible")] public bool Visible { get; set; }
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
}


/// <summary>How the game is being rendered, so an agent (or its operator) can reason about it.</summary>
public class DisplayInfo
{
    [JsonPropertyName("zoom_level")] public float ZoomLevel { get; set; }
    [JsonPropertyName("desired_zoom_level")] public float DesiredZoomLevel { get; set; }
    [JsonPropertyName("ui_scale")] public float UiScale { get; set; }
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
