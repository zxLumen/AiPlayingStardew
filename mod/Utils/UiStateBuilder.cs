using System.Collections;
using System.Reflection;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StateBridge.Models;

namespace StateBridge.Utils;

/// <summary>
/// Reads the open menu / dialogue so an external agent can drive it.
///
/// Rather than special-casing every menu class, we reflect over the menu's fields and
/// collect every <see cref="ClickableComponent"/> we can find. That covers TitleMenu.buttons,
/// LoadGameMenu.slotButtons/backButton, and most other vanilla menus in one go.
///
/// The same collector is used by <see cref="ActionProcessor"/> to resolve a click by name,
/// so the names reported in /state are exactly the names accepted by menu_click.
///
/// Must only be called on the game thread.
/// </summary>
public static class UiStateBuilder
{
    /// <summary>Upper bound on reported buttons, to keep payloads small on busy menus.</summary>
    private const int MaxButtons = 60;

    /// <summary>A clickable element plus where it was found.</summary>
    public readonly record struct MenuComponent(string Source, int IndexInCollection, ClickableComponent Component)
    {
        /// <summary>Stable identifier: the component's own name, else "source[index]", else the field name.</summary>
        public string DisplayName =>
            !string.IsNullOrWhiteSpace(Component.name) ? Component.name
            : IndexInCollection >= 0 ? $"{Source}[{IndexInCollection}]"
            : Source;
    }


    /// <summary>
    /// The menu the player is actually interacting with. SDV 1.6's TitleMenu keeps its
    /// submenus (LoadGameMenu, options, ...) in a static field rather than in
    /// <see cref="Game1.activeClickableMenu"/>, so state and clicks must descend into it.
    /// </summary>
    public static IClickableMenu? GetEffectiveMenu()
    {
        IClickableMenu? active = Game1.activeClickableMenu;
        if (active is TitleMenu && TitleMenu.subMenu != null)
            return TitleMenu.subMenu;

        return active;
    }


    public static UiInfo Build(IMonitor monitor)
    {
        var ui = new UiInfo();

        IClickableMenu? active = Game1.activeClickableMenu;
        IClickableMenu? menu = GetEffectiveMenu();

        if (menu != null)
        {
            ui.MenuType = menu.GetType().FullName ?? menu.GetType().Name;
            ui.MenuName = menu.GetType().Name;

            if (active != null && !ReferenceEquals(menu, active))
                ui.ParentMenu = active.GetType().Name;

            try
            {
                foreach ((string name, MenuComponent mc) in DescribeMenu(menu))
                {
                    if (ui.Buttons.Count >= MaxButtons)
                        break;

                    ClickableComponent c = mc.Component;
                    ui.Buttons.Add(new MenuButtonInfo
                    {
                        Name = name,
                        Source = mc.Source,
                        Label = c.label ?? "",
                        X = c.bounds.X,
                        Y = c.bounds.Y,
                        Width = c.bounds.Width,
                        Height = c.bounds.Height,
                        Visible = c.visible,
                    });
                }
            }
            catch (Exception ex)
            {
                monitor.Log($"UiStateBuilder buttons failed: {ex.Message}", LogLevel.Warn);
            }
        }

        try { ui.Dialogue = BuildDialogue(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder dialogue failed: {ex.Message}", LogLevel.Warn); }

        try { ui.TextFields = CollectTextBoxes(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder text fields failed: {ex.Message}", LogLevel.Warn); }

        try { ui.SaveSlots = BuildSaveSlots(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder save slots failed: {ex.Message}", LogLevel.Warn); }

        try { ui.CharacterDesign = BuildCharacterDesign(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder character design failed: {ex.Message}", LogLevel.Warn); }

        return ui;
    }


    /// <summary>
    /// Reads the character currently being designed on the creation screen, so an agent can see
    /// what it is choosing (skin, hair, colors, ...) instead of guessing.
    /// </summary>
    private static CharacterDesignInfo? BuildCharacterDesign(IClickableMenu? menu)
    {
        if (menu is not CharacterCustomization customization)
            return null;

        Farmer? farmer;
        try { farmer = customization.GetOrCreateDisplayFarmer(); }
        catch { return null; }

        if (farmer == null)
            return null;

        var design = new CharacterDesignInfo();

        try { design.Gender = farmer.IsMale ? "male" : "female"; } catch { }
        try { design.Skin = farmer.skin.Value; } catch { }
        try { design.HairStyle = farmer.hair.Value; } catch { }
        try { design.Accessory = farmer.accessory.Value; } catch { }
        try { design.Shirt = farmer.shirt.Value ?? ""; } catch { }
        try { design.Pants = farmer.pants.Value ?? ""; } catch { }
        try { design.FavoriteThing = farmer.favoriteThing.Value ?? ""; } catch { }
        try { design.FarmName = farmer.farmName.Value ?? ""; } catch { }

        try { design.HairColor = ToRgb(farmer.hairstyleColor.Value); } catch { }
        try { design.EyeColor = ToRgb(farmer.newEyeColor.Value); } catch { }
        try { design.PantsColor = ToRgb(farmer.pantsColor.Value); } catch { }

        try
        {
            foreach (ClickableTextureComponent button in customization.farmTypeButtons)
            {
                if (button != null && !string.IsNullOrWhiteSpace(button.name))
                    design.AvailableFarmTypes.Add(button.name);
            }
        }
        catch { /* farm type list unavailable; leave empty */ }

        return design;
    }


    private static int[] ToRgb(Color color) => new[] { (int)color.R, (int)color.G, (int)color.B };


    /// <summary>Finds every TextBox on the menu (name box, farm name box, favorite thing box, ...).</summary>
    private static List<TextFieldInfo> CollectTextBoxes(IClickableMenu? menu)
    {
        var list = new List<TextFieldInfo>();
        if (menu == null)
            return list;

        Type? type = menu.GetType();
        while (type != null && type != typeof(object))
        {
            FieldInfo[] fields;
            try { fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { fields = Array.Empty<FieldInfo>(); }

            foreach (FieldInfo field in fields)
            {
                if (!typeof(TextBox).IsAssignableFrom(field.FieldType))
                    continue;

                object? value;
                try { value = field.GetValue(menu); }
                catch { continue; }

                if (value is not TextBox box || box.PasswordBox)
                    continue;

                list.Add(new TextFieldInfo
                {
                    Field = field.Name,
                    Value = box.Text ?? "",
                });
            }

            type = type.BaseType;
        }

        return list;
    }


    /// <summary>
    /// Walks the menu's fields (including inherited ones) and returns every ClickableComponent found,
    /// directly or inside a collection. Duplicates by reference are dropped.
    /// </summary>
    public static List<MenuComponent> CollectMenuComponents(IClickableMenu menu)
    {
        var result = new List<MenuComponent>();
        var seen = new HashSet<ClickableComponent>(ReferenceEqualityComparer.Instance);

        Type? type = menu.GetType();
        while (type != null && type != typeof(object))
        {
            FieldInfo[] fields;
            try { fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { fields = Array.Empty<FieldInfo>(); }

            foreach (FieldInfo field in fields)
            {
                object? value;
                try { value = field.GetValue(menu); }
                catch { continue; }
                if (value == null)
                    continue;

                if (value is ClickableComponent single)
                {
                    if (seen.Add(single))
                        result.Add(new MenuComponent(field.Name, -1, single));
                    continue;
                }

                if (value is string)
                    continue;

                if (value is not IEnumerable sequence)
                    continue;

                int index = 0;
                try
                {
                    foreach (object? entry in sequence)
                    {
                        if (entry is ClickableComponent item && seen.Add(item))
                            result.Add(new MenuComponent(field.Name, index, item));
                        index++;
                    }
                }
                catch { /* some collections throw while enumerating; skip the rest of this field */ }
            }

            type = type.BaseType;
        }

        return result;
    }


    /// <summary>
    /// The menu's clickable elements with the exact names reported in /state.
    ///
    /// Names are the component's own name; when the same name appears more than once
    /// (character creation has both leftSelectionButtons.Hair and rightSelectionButtons.Hair)
    /// every duplicate is prefixed with its source field so each is independently clickable.
    /// </summary>
    public static List<(string Name, MenuComponent Component)> DescribeMenu(IClickableMenu menu)
    {
        List<MenuComponent> components = CollectMenuComponents(menu);

        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (MenuComponent mc in components)
        {
            string key = mc.DisplayName;
            counts[key] = counts.TryGetValue(key, out int seen) ? seen + 1 : 1;
        }

        var described = new List<(string, MenuComponent)>(components.Count);
        foreach (MenuComponent mc in components)
        {
            string name = counts[mc.DisplayName] > 1 ? $"{mc.Source}.{mc.DisplayName}" : mc.DisplayName;
            described.Add((name, mc));
        }

        return described;
    }


    /// <summary>Resolves a button by the name reported in /state (or its raw name/label). Null when not found.</summary>
    public static ClickableComponent? FindComponent(IClickableMenu menu, string name)
    {
        foreach ((string reported, MenuComponent mc) in DescribeMenu(menu))
        {
            bool match = string.Equals(reported, name, StringComparison.OrdinalIgnoreCase)
                      || string.Equals(mc.DisplayName, name, StringComparison.OrdinalIgnoreCase)
                      || string.Equals(mc.Source, name, StringComparison.OrdinalIgnoreCase)
                      || (!string.IsNullOrWhiteSpace(mc.Component.name)
                          && string.Equals(mc.Component.name, name, StringComparison.OrdinalIgnoreCase))
                      || (!string.IsNullOrWhiteSpace(mc.Component.label)
                          && string.Equals(mc.Component.label, name, StringComparison.OrdinalIgnoreCase));

            if (match)
                return mc.Component;
        }
        return null;
    }


    /// <summary>Names currently clickable, for error messages and prompts.</summary>
    public static List<string> AvailableNames(IClickableMenu menu)
    {
        var names = new List<string>();
        foreach ((string reported, _) in DescribeMenu(menu))
        {
            if (!string.IsNullOrWhiteSpace(reported) && !names.Contains(reported))
                names.Add(reported);
        }
        return names;
    }


    private static DialogueInfo? BuildDialogue(IClickableMenu? menu)
    {
        if (menu is not DialogueBox box)
            return null;

        var info = new DialogueInfo();

        try { info.Text = box.getCurrentString() ?? ""; } catch { info.Text = ""; }
        try { info.SelectedIndex = box.selectedResponse; } catch { }
        info.Speaker = TryReadSpeaker(box);

        Response[]? responses = box.responses;
        if (responses != null)
        {
            for (int i = 0; i < responses.Length; i++)
            {
                Response? r = responses[i];
                if (r == null)
                    continue;

                info.Responses.Add(new DialogueResponseInfo
                {
                    Index = i,
                    Key = r.responseKey ?? "",
                    Text = r.responseText ?? "",
                });
            }
        }

        return info;
    }


    /// <summary>Reads the speaker name defensively (the Dialogue field layout has changed between versions).</summary>
    private static string TryReadSpeaker(DialogueBox box)
    {
        try
        {
            object? dialogue = box.characterDialogue;
            if (dialogue == null)
                return "";

            FieldInfo? speakerField = dialogue.GetType().GetField("speaker");
            object? speaker = speakerField?.GetValue(dialogue);
            if (speaker is NPC npc)
                return npc.displayName ?? npc.Name ?? "";

            return "";
        }
        catch
        {
            return "";
        }
    }


    private static List<SaveSlotInfo> BuildSaveSlots(IClickableMenu? menu)
    {
        var list = new List<SaveSlotInfo>();
        if (menu is not LoadGameMenu loadGameMenu)
            return list;

        List<LoadGameMenu.MenuSlot>? slots = loadGameMenu.MenuSlots;
        if (slots == null)
            return list;

        for (int i = 0; i < slots.Count; i++)
        {
            if (slots[i] is not LoadGameMenu.SaveFileSlot slot)
                continue;

            string slotName = InvokeStringMethod(slot, "slotName");
            string slotSubName = InvokeStringMethod(slot, "slotSubName");

            list.Add(new SaveSlotInfo
            {
                Index = i,
                FarmerName = slot.Farmer?.Name ?? "",
                SlotName = slotName,
                SlotSubName = slotSubName,
            });
        }

        return list;
    }


    /// <summary>slotName()/slotSubName() are private on SaveFileSlot, so call them reflectively.</summary>
    private static string InvokeStringMethod(object target, string methodName)
    {
        try
        {
            MethodInfo? method = target.GetType().GetMethod(
                methodName,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);

            return method?.Invoke(target, null) as string ?? "";
        }
        catch
        {
            return "";
        }
    }
}
