using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using StardewModdingAPI;
using StardewValley;

namespace StateBridge.Utils;

/// <summary>
/// Opens the game's own data to an agent.
///
/// The game already contains every rule it plays by - crop growth, shop stock, gift tastes, NPC
/// schedules, bundle requirements - across a few hundred content assets. Rather than hand-writing a
/// list of the "useful" ones (which is how a rule ends up hardcoded in a mod instead of read from
/// the game), this discovers them by scanning the content directory and works out each asset's
/// value type by trial, caching what sticks.
///
/// Nothing here knows anything about Stardew Valley's rules.
/// </summary>
public static class GameContent
{
    private static List<string>? _catalog;
    private static string? _root;
    private static Type[]? _dataTypes;
    private static MethodInfo? _loader;
    private static readonly Dictionary<string, Type> ResolvedType = new();
    private static readonly Dictionary<string, string> ResolvedName = new();

    /// <summary>Every data asset the game ships, e.g. "Data/Crops", "Strings/WorldMap".</summary>
    public static List<string> Catalog(IMonitor monitor)
    {
        if (_catalog != null)
            return _catalog;

        var found = new List<string>();
        string? root = ContentRoot(monitor);

        if (root != null)
        {
            try
            {
                foreach (string folder in new[] { "Data", "Strings" })
                {
                    string dir = Path.Combine(root, folder);
                    if (!Directory.Exists(dir))
                        continue;

                    foreach (string file in Directory.EnumerateFiles(dir, "*.xnb", SearchOption.AllDirectories))
                    {
                        string rel = file[root.Length..].TrimStart('\\', '/').Replace('\\', '/');
                        if (rel.EndsWith(".xnb", StringComparison.OrdinalIgnoreCase))
                            rel = rel[..^4];

                        // Skip localized variants (Data/Crops.zh-CN) - the base asset carries the shape.
                        if (rel.Split('/')[^1].Contains('.'))
                            continue;

                        found.Add(rel);
                    }
                }
            }
            catch (Exception ex)
            {
                monitor.Log($"content catalog scan failed: {ex.Message}", LogLevel.Warn);
            }
        }

        found.Sort(StringComparer.OrdinalIgnoreCase);
        _catalog = found;
        monitor.Log($"game content catalog: {found.Count} assets from '{root ?? "?"}'", LogLevel.Trace);
        return found;
    }


    /// <summary>
    /// The folder the game's content files live in. The content manager's root is often relative,
    /// so it is resolved against the game's install folder.
    /// </summary>
    private static string? ContentRoot(IMonitor monitor)
    {
        if (_root != null)
            return _root;

        string install = Path.GetDirectoryName(typeof(Game1).Assembly.Location) ?? ".";
        string declared = Game1.content.RootDirectory;

        var candidates = new List<string>();

        if (!string.IsNullOrWhiteSpace(declared))
        {
            candidates.Add(Path.IsPathRooted(declared) ? declared : Path.Combine(install, declared));
            candidates.Add(Path.Combine(install, "Content"));
        }

        candidates.Add(Path.Combine(install, "Content"));

        foreach (string candidate in candidates)
        {
            try
            {
                if (Directory.Exists(Path.Combine(candidate, "Data")))
                {
                    _root = Path.GetFullPath(candidate).TrimEnd('\\', '/');
                    return _root;
                }
            }
            catch { }
        }

        monitor.Log($"could not find the game's Content folder (declared '{declared}', install '{install}')", LogLevel.Warn);
        return null;
    }

    /// <summary>Loads one asset, working out both its value type and its container shape the first time.</summary>
    public static (object? Data, string TypeName) Load(string asset, IMonitor monitor)
    {
        if (ResolvedType.TryGetValue(asset, out Type? known))
            return (Invoke(asset, known), ResolvedName[asset]);

        string segment = asset.Split('/')[^1];
        string singular = segment.EndsWith("s", StringComparison.OrdinalIgnoreCase) ? segment[..^1] : segment;

        var similar = new List<Type>();
        var rest = new List<Type>();

        foreach (Type type in DataTypes(monitor))
        {
            bool matches =
                type.Name.StartsWith(segment, StringComparison.OrdinalIgnoreCase) ||
                type.Name.StartsWith(singular, StringComparison.OrdinalIgnoreCase) ||
                (type.Namespace?.Split('.').Last().Equals(segment, StringComparison.OrdinalIgnoreCase) ?? false);

            (matches ? similar : rest).Add(type);
        }

        // A matching name is just a hint; the load itself is the proof, so every shape gets tried
        // against every candidate until one sticks. Plain strings go last: a dictionary of strings is
        // the shape most likely to load successfully for the wrong reason.
        foreach (Type valueType in similar.Concat(rest).Concat(new[] { typeof(string) }))
        {
            foreach (Type shape in Shapes(valueType))
            {
                object? data = Invoke(asset, shape);
                if (data == null)
                    continue;

                ResolvedType[asset] = shape;
                ResolvedName[asset] = shape.IsGenericType ? shape.GetGenericArguments()[^1].Name : shape.Name;
                return (data, ResolvedName[asset]);
            }
        }

        return (null, "?");
    }


    /// <summary>The container shapes a content asset may use, most common first.</summary>
    private static IEnumerable<Type> Shapes(Type valueType)
    {
        yield return typeof(Dictionary<,>).MakeGenericType(typeof(string), valueType);
        yield return typeof(Dictionary<,>).MakeGenericType(typeof(int), valueType);
        yield return typeof(List<>).MakeGenericType(valueType);

        // Some assets are neither: the file is the object itself (Data/Weddings, Data/GarbageCans).
        yield return valueType;
    }


    /// <summary>Why the last Load failed, for reporting back to the agent.</summary>
    public static string LastError { get; private set; } = "";


    private static readonly JsonSerializerOptions TokenJson = new() { IncludeFields = true };


    /// <summary>
    /// Resolves every text token in a value tree, e.g.
    /// "[LocalizedText Strings\StringsFromCSFiles:MapPage.cs.11080]" -> "营业时间 9:00 - 21:00".
    ///
    /// The game stores very little text in its data files; it stores references to text and resolves
    /// them when it draws. Reading the table without resolving them looks exactly like missing data -
    /// which is how a shop's opening hours can be in plain sight on the map and invisible to an agent.
    /// </summary>
    public static object? ExpandTokens(object? value)
    {
        JsonNode? tree;
        try
        {
            tree = JsonSerializer.SerializeToNode(value, TokenJson);
        }
        catch (Exception ex)
        {
            LastError = ex.Message;
            return value;
        }

        Expand(tree);
        return tree;
    }


    /// <summary>Walks a JSON tree and replaces token strings with the text they stand for.</summary>
    private static void Expand(JsonNode? node)
    {
        if (node is JsonObject obj)
        {
            foreach (string key in obj.Select(pair => pair.Key).ToList())
            {
                JsonNode? child = obj[key];

                if (child is JsonValue scalar && scalar.TryGetValue(out string? text) && text != null && text.Contains('['))
                {
                    JsonValue? resolved = Resolve(text);
                    if (resolved != null)
                        obj[key] = resolved;
                }
                else if (child is not null)
                {
                    Expand(child);
                }
            }

            return;
        }

        if (node is JsonArray array)
        {
            for (int i = 0; i < array.Count; i++)
            {
                JsonNode? item = array[i];

                if (item is JsonValue scalar && scalar.TryGetValue(out string? text) && text != null && text.Contains('['))
                {
                    JsonValue? resolved = Resolve(text);
                    if (resolved != null)
                        array[i] = resolved;
                }
                else if (item is not null)
                {
                    Expand(item);
                }
            }
        }
    }


    /// <summary>The text a token stands for, or null when it is already plain or cannot be resolved.</summary>
    private static JsonValue? Resolve(string text)
    {
        try
        {
            string resolved = StardewValley.TokenizableStrings.TokenParser.ParseText(
                text, Game1.random, null, Game1.player);

            return resolved == text ? null : JsonValue.Create(resolved);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>What an asset looks like, without loading all of it.</summary>
    public static string Describe(string asset, IMonitor monitor)
    {
        (object? data, string typeName) = Load(asset, monitor);
        return data is System.Collections.IDictionary dictionary
            ? $"{typeName} x{Probe(dictionary)}"
            : typeName;
    }

    private static int Probe(System.Collections.IDictionary dictionary)
    {
        try { return dictionary.Count; }
        catch { return -1; }
    }

    private static Type[] DataTypes(IMonitor monitor)
    {
        if (_dataTypes != null)
            return _dataTypes;

        var found = new List<Type>();

        foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
        {
            if (assembly.GetName().Name != "StardewValley.GameData")
                continue;

            try { found.AddRange(assembly.GetTypes().Where(t => t.IsClass && !t.IsAbstract)); }
            catch { }
        }

        _dataTypes = found.ToArray();
        monitor.Log($"game data types available: {found.Count}", LogLevel.Trace);
        return _dataTypes;
    }

    private static object? Invoke(string asset, Type dictionaryType)
    {
        try
        {
            _loader ??= typeof(LocalizedContentManager)
                .GetMethods()
                .FirstOrDefault(m => m.Name == "Load" && m.IsGenericMethodDefinition && m.GetParameters().Length == 1);

            return _loader?.MakeGenericMethod(dictionaryType).Invoke(Game1.content, new object[] { asset });
        }
        catch (Exception ex)
        {
            LastError = ex.InnerException?.Message ?? ex.Message;
            return null;
        }
    }
}
