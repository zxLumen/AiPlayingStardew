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


    /// <summary>
    /// The menu the GAME ITSELF would hand a click to.
    ///
    /// Deliberately not "the menu that owns the thing being clicked", and that distinction cost a
    /// field of parsnips. SDV's own dispatch (<c>Game1.updateActiveMenu</c>) starts at
    /// <see cref="Game1.activeClickableMenu"/>, walks <c>GetChildMenu()</c> to the deepest child, and
    /// gives THAT one the hover, the update and the click.
    ///
    /// A shop owns its inventory panel as a nested <c>InventoryMenu</c> - but that nested menu's
    /// <c>receiveLeftClick</c> is an empty method, and the code that puts a purchase down lives in the
    /// SHOP's own <c>receiveLeftClick</c> (<c>heldItem = inventory.leftClick(x, y, heldItem)</c>).
    /// Sending the click to the owner instead of to the game's target made it vanish with no error:
    /// received, logged, and nothing at all happened.
    ///
    /// This mirrors the game's loop exactly. If the game's dispatch ever changes, this is the one
    /// place that has to change with it.
    /// </summary>
    public static IClickableMenu? ClickRouter()
    {
        IClickableMenu? menu = Game1.activeClickableMenu;
        while (menu?.GetChildMenu() is IClickableMenu child)
            menu = child;

        return menu;
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

        try { ui.Shop = BuildShop(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder shop failed: {ex.Message}", LogLevel.Warn); }

        try { ui.HeldItem = DescribeHeld(menu); }
        catch (Exception ex) { monitor.Log($"UiStateBuilder held item failed: {ex.Message}", LogLevel.Warn); }

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
    ///
    /// It also descends ONE level into a nested menu. That is not tidiness: a shop keeps its inventory
    /// panel in a nested menu object, so the slots you click to put a purchase into your bag are one
    /// level down and the old walk never saw them. The result was a farmer who bought four parsnip
    /// seeds, had them stuck on the menu's hand, and had no way to put them down - because the bridge
    /// could not name the thing that would have done it.
    /// </summary>
    public static List<MenuComponent> CollectMenuComponents(IClickableMenu menu)
    {
        var result = new List<MenuComponent>();
        var seen = new HashSet<ClickableComponent>(ReferenceEqualityComparer.Instance);
        CollectFrom(menu, 1, seen, result);
        return result;
    }


    /// <summary>Upper bound on collected components, so a deep menu cannot produce a huge payload.</summary>
    private const int MaxComponents = 160;


    private static void CollectFrom(object menu, int depth, HashSet<ClickableComponent> seen,
                                    List<MenuComponent> result)
    {
        Type? type = menu.GetType();
        while (type != null && type != typeof(object))
        {
            FieldInfo[] fields;
            try { fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { fields = Array.Empty<FieldInfo>(); }

            foreach (FieldInfo field in fields)
            {
                if (result.Count >= MaxComponents)
                    return;

                object? value;
                try { value = field.GetValue(menu); }
                catch { continue; }
                if (value == null || value is string)
                    continue;

                if (value is ClickableComponent single)
                {
                    if (seen.Add(single))
                        result.Add(new MenuComponent(field.Name, -1, single));
                    continue;
                }

                if (value is IEnumerable sequence)
                {
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
                    continue;
                }

                if (depth > 0 && value is IClickableMenu nested)
                    CollectFrom(nested, depth - 1, seen, result);
            }

            type = type.BaseType;
        }
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


    /// <summary>
    /// The text of the dialogue box that is open right now, or "" when there is none.
    ///
    /// Used to ask the GAME what just happened rather than inferring it: a shut door paints its own
    /// sentence ("上锁了……（营业时间：9:00~21:00）") and an open one fades the screen instead.
    /// </summary>
    public static string OpenDialogueText()
    {
        try
        {
            return GetEffectiveMenu() is DialogueBox box ? (box.getCurrentString() ?? "") : "";
        }
        catch { return ""; }
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


    /// <summary>
    /// Joins a shop's clickable rows onto the things actually for sale.
    ///
    /// ShopMenu keeps three parallel structures - the rows (`forSaleButtons`), the ordered goods
    /// (`forSale`, offset by `currentItemIndex` as the shelf scrolls) and a price/stock table keyed
    /// by item (`itemPriceAndStock`). The rows themselves carry no label whatsoever, so without
    /// this join the agent is handed a column of blank rectangles named "0", "1", "2", ... and has
    /// no way to tell a parsnip seed from a scythe. That is not a reason to skip the purchase; it is
    /// a reason to guess, which is how money gets spent on the wrong thing.
    ///
    /// Where the join cannot be trusted the row is left BLANK on purpose: a shop row with no name
    /// reads as "unknown" and stops an agent from acting on a confident lie.
    /// </summary>
    private static List<ShopItemInfo> BuildShop(IClickableMenu? menu)
    {
        var rows = new List<ShopItemInfo>();
        if (menu is not ShopMenu)
            return rows;

        // Find the shelf, the prices and the clickable rows by SHAPE, not by name.
        //
        // The names were guessed once (forSale / itemPriceAndStock) and were wrong, which produced a
        // shelf of blanks. Blanks are safe - they cannot make an agent buy the wrong thing - but they
        // are useless, and a name that drifts turns a working bridge into a lying one with no warning.
        // Shapes do not drift: the goods are the sequence of ISalable, the prices are the int[] table
        // keyed by them, and the rows are the clickable components that carry a number for a name.
        List<object?> shelf = new();
        List<object?> rowsFound = new();
        IDictionary? prices = null;

        foreach (FieldInfo field in AllFieldsOn(menu))
        {
            object? value;
            try { value = field.GetValue(menu); }
            catch { continue; }
            if (value == null || value is string)
                continue;

            if (prices == null && value is IDictionary table && LooksLikePrices(table))
            {
                prices = table;
                continue;
            }

            if (value is not System.Collections.IEnumerable sequence)
                continue;

            List<object?> items = ToList(sequence);
            if (items.Count == 0)
                continue;

            if (shelf.Count == 0 && AllAre(items, x => x is StardewValley.ISalable))
                shelf = items;
            else if (rowsFound.Count == 0 && AllAre(items, x => x is ClickableComponent))
                rowsFound = items;
        }

        if (prices == null || shelf.Count == 0 || rowsFound.Count == 0)
            return rows;        // nothing trustworthy to join: blank rows, never guesses

        // The shelf scrolls, so the visible rows are an offset into the goods. That offset is not
        // guessed either - the price table settles it, because a correct join finds its item in it
        // and a wrong one does not.
        int offset = ChooseOffset(menu, rowsFound, shelf, prices);
        int money = Game1.player?.Money ?? 0;

        // The name to click is whatever DescribeMenu reports, NOT the component's own name.
        //
        // They are usually the same and sometimes not: once the nested inventory slots started being
        // collected, the bag slots also called themselves "0".."3", the collision made DescribeMenu
        // prefix both sides, and the shop rows became "forSaleButtons.0". Reporting the raw "0" here
        // would hand the agent an ambiguous name that can resolve to a BAG slot instead of the goods.
        // A slot name that might mean two things is worse than no name at all.
        var clickable = new Dictionary<ClickableComponent, string>(ReferenceEqualityComparer.Instance);
        foreach ((string reported, MenuComponent mc) in DescribeMenu(menu))
            clickable.TryAdd(mc.Component, reported);

        for (int i = 0; i < rowsFound.Count; i++)
        {
            if (rowsFound[i] is not ClickableComponent button)
                continue;

            var row = new ShopItemInfo
            {
                Slot = clickable.TryGetValue(button, out string? reportedName)
                    ? reportedName
                    : (string.IsNullOrWhiteSpace(button.name) ? i.ToString() : button.name),
            };

            int index = offset + i;
            if (index >= 0 && index < shelf.Count && shelf[index] is { } item)
            {
                try
                {
                    if (prices.Contains(item))
                    {
                        (int price, int stock) = ReadPrice(prices[item]);
                        if (price > 0)
                        {
                            row.Item = Describe(item);
                            row.ItemId = IdOf(item);
                            row.Price = price;
                            row.Stock = stock;
                            row.Affordable = money >= row.Price;
                        }
                    }
                }
                catch { /* leave the row blank rather than guess */ }
            }

            rows.Add(row);
        }

        return rows;
    }


    /// <summary>Which slice of the shelf the visible rows are showing. The price table decides.</summary>
    private static int ChooseOffset(IClickableMenu menu, List<object?> rows, List<object?> shelf,
                                    IDictionary prices)
    {
        var candidates = new List<int> { 0 };
        int current = ReadInt(menu, "currentItemIndex");
        if (current > 0)
            candidates.Add(current);

        int best = candidates[0];
        int bestHits = -1;

        foreach (int candidate in candidates)
        {
            int hits = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                int index = candidate + i;
                if (index < 0 || index >= shelf.Count || shelf[index] is not { } item)
                    continue;
                try { if (prices.Contains(item)) hits++; }
                catch { }
            }

            if (hits > bestHits)
            {
                bestHits = hits;
                best = candidate;
            }
        }

        return best;
    }


    private static bool LooksLikePrices(IDictionary table)
    {
        try
        {
            foreach (DictionaryEntry entry in table)
            {
                object? value = entry.Value;
                return entry.Key is StardewValley.ISalable
                       && (value is int[] || (value != null && ReadMember(value, "Price") != null));
            }
        }
        catch { }

        return false;
    }


    /// <summary>
    /// Price and stock out of one entry of the shop's price table.
    ///
    /// The value type is NOT stable across versions, and assuming it was cost real time: the first
    /// version looked the table up by name and then demanded an int[]; the "shape-based" rewrite did
    /// the same thing by a different route. In 1.6 the value is an ItemStockInformation, so both
    /// found nothing and the shelf reported a column of blanks.
    ///
    /// So read BOTH shapes, and read by MEANING - `Price` and `Stock` - instead of by index, so a
    /// third shape cannot quietly produce a wrong price.
    /// </summary>
    private static (int Price, int Stock) ReadPrice(object? entry)
    {
        if (entry == null)
            return (0, -1);

        if (entry is int[] info && info.Length > 0)
            return (info[0], info.Length > 1 && info[1] != int.MaxValue ? info[1] : -1);

        int price = ToInt(ReadMember(entry, "Price"));
        int stock = ToInt(ReadMember(entry, "Stock"));
        if (stock == int.MaxValue || stock < 0)
            stock = -1;

        return (price, stock);
    }


    private static int ToInt(object? value)
    {
        try { return value == null ? 0 : Convert.ToInt32(value); }
        catch { return 0; }
    }


    private static List<FieldInfo> AllFieldsOn(object target)
    {
        var fields = new List<FieldInfo>();
        Type? type = target.GetType();
        while (type != null && type != typeof(object))
        {
            try { fields.AddRange(type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)); }
            catch { }
            type = type.BaseType;
        }
        return fields;
    }


    private static bool AllAre(List<object?> items, Func<object?, bool> predicate)
    {
        foreach (object? item in items)
        {
            if (!predicate(item))
                return false;
        }
        return true;
    }


    /// <summary>
    /// What the open menu is holding, if it is holding anything.
    ///
    /// A SECOND place an item can be "in hand", and the one nobody looked at: a menu can hold an item
    /// itself (<c>IClickableMenu.heldItem</c>) rather than the player holding it on the cursor. While
    /// it does, Stardew's <c>readyToClose()</c> returns false - and both the close button and the
    /// escape key go through that - so the menu "refuses to close for no reason" and swallows every
    /// later click too. A player sees the item in their hand; the payload said nothing about it.
    ///
    /// Also read by the click witness, for the same reason: a click that picks something up and a
    /// click that puts it down look identical if you only watch money and the cursor.
    /// </summary>
    internal static string DescribeHeld(IClickableMenu? menu)
    {
        if (menu == null)
            return "";

        object? held = ReadMember(menu, "heldItem");
        return held == null ? "" : Describe(held);
    }


    private static string Describe(object item)
    {
        if (item is not StardewValley.ISalable salable)
            return "";

        string name = salable.DisplayName ?? "";
        if (salable.Stack > 1)
            name = $"{name} x{salable.Stack}";
        return name;
    }


    private static string IdOf(object item)
        => item is StardewValley.ISalable salable ? (salable.QualifiedItemId ?? "") : "";


    private static int ReadInt(object target, string name)
    {
        object? raw = ReadMember(target, name);
        return raw is int value ? value : 0;
    }


    /// <summary>Reads a field (or property) by name up the type hierarchy, or null.</summary>
    private static object? ReadMember(object target, string name)
    {
        Type? type = target.GetType();
        while (type != null && type != typeof(object))
        {
            try
            {
                FieldInfo? field = type.GetField(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (field != null)
                    return field.GetValue(target);

                PropertyInfo? prop = type.GetProperty(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (prop != null && prop.GetIndexParameters().Length == 0)
                    return prop.GetValue(target);
            }
            catch { return null; }

            type = type.BaseType;
        }

        return null;
    }


    private static List<object?> ToList(object? sequence)
    {
        var list = new List<object?>();
        if (sequence is not IEnumerable items)
            return list;

        try
        {
            foreach (object? entry in items)
                list.Add(entry);
        }
        catch { /* a collection that throws mid-enumeration gives what we got so far */ }

        return list;
    }
}
