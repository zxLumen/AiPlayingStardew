using System.Reflection;

// Usage:
//   apidump <gameDir> --type <TypeName> [keyword]
//   apidump <gameDir> --search <keyword>
if (args.Length < 2)
{
    Console.WriteLine("usage:");
    Console.WriteLine("  apidump <gameDir> --type <TypeName> [keyword]");
    Console.WriteLine("  apidump <gameDir> --search <keyword>");
    return 2;
}

string gameDir = args[0];
string mode = args[1];
string query = args.Length >= 3 ? args[2] : "";

if (!Directory.Exists(gameDir))
{
    Console.WriteLine($"game dir not found: {gameDir}");
    return 2;
}

string gameDll = Path.Combine(gameDir, "Stardew Valley.dll");
if (!File.Exists(gameDll))
{
    Console.WriteLine($"game dll not found: {gameDll}");
    return 2;
}

var paths = new List<string>(Directory.GetFiles(gameDir, "*.dll"));
paths.Add(typeof(object).Assembly.Location);

var resolver = new PathAssemblyResolver(paths.Distinct(StringComparer.OrdinalIgnoreCase));
using var mlc = new MetadataLoadContext(resolver, "mscorlib");
Assembly asm = mlc.LoadFromAssemblyPath(gameDll);

static IEnumerable<Type> SafeGetTypes(Assembly assembly)
{
    try
    {
        return assembly.GetTypes();
    }
    catch (ReflectionTypeLoadException ex)
    {
        return ex.Types.Where(t => t != null)!;
    }
}

if (mode == "--search")
{
    Console.WriteLine($"### search '{query}' across {Path.GetFileName(gameDll)}");

    foreach (Type type in SafeGetTypes(asm).OrderBy(t => t.FullName))
    {
        MemberInfo[] matches;
        try
        {
            matches = type
                .GetMembers(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly)
                .Where(m => m.Name.Contains(query, StringComparison.OrdinalIgnoreCase))
                .ToArray();
        }
        catch
        {
            continue;
        }

        if (matches.Length == 0)
            continue;

        Console.WriteLine($"## {type.FullName}");
        foreach (MemberInfo m in matches.OrderBy(m => m.Name))
            Console.WriteLine($"   {m.MemberType}: {Describe(m)}");
    }
    return 0;
}

if (mode == "--types")
{
    // List data-model type names, so we can map an asset path to its value type.
    foreach (string path in paths.Distinct(StringComparer.OrdinalIgnoreCase))
    {
        Assembly other;
        try { other = mlc.LoadFromAssemblyPath(path); }
        catch { continue; }

        Type[] all;
        try { all = SafeGetTypes(other).ToArray(); }
        catch { continue; }

        var hits = all
            .Where(t => t.FullName != null && t.FullName.Contains(query, StringComparison.OrdinalIgnoreCase))
            .OrderBy(t => t.FullName)
            .ToList();

        if (hits.Count == 0)
            continue;

        Console.WriteLine($"### {Path.GetFileName(path)}");
        foreach (Type t in hits)
            Console.WriteLine($"   {t.FullName}");
    }
    return 0;
}

if (mode == "--any")
{
    // Search every dll next to the game, not just Stardew Valley.dll - ShopData and other
    // GameData types live in StardewValley.GameData.dll.
    foreach (string path in paths.Distinct(StringComparer.OrdinalIgnoreCase))
    {
        Assembly other;
        try { other = mlc.LoadFromAssemblyPath(path); }
        catch { continue; }

        Type? hit = null;
        try { hit = other.GetType(query); } catch { }

        if (hit == null)
        {
            try
            {
                hit = SafeGetTypes(other)
                    .FirstOrDefault(t => t.Name.Equals(query, StringComparison.OrdinalIgnoreCase));
            }
            catch { }
        }

        if (hit == null)
            continue;

        Console.WriteLine($"### type {hit.FullName}   (in {Path.GetFileName(path)})");
        if (hit.BaseType != null)
            Console.WriteLine($"    base: {hit.BaseType.FullName}");

        const BindingFlags anyFlags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
        foreach (MemberInfo m in hit.GetMembers(anyFlags).OrderBy(m => m.Name).ThenBy(m => m.MemberType))
        {
            if (m is MethodBase mb && mb.IsPrivate)
                continue;
            Console.WriteLine($"   {m.MemberType}: {Describe(m)}");
        }
        return 0;
    }

    Console.WriteLine($"type '{query}' not found in any dll");
    return 1;
}

if (mode == "--type")
{
    Type? type = asm.GetType(query);

    if (type == null)
    {
        // fuzzy fallback: match by simple name
        var candidates = SafeGetTypes(asm)
            .Where(t => t.Name.Contains(query, StringComparison.OrdinalIgnoreCase))
            .OrderBy(t => t.FullName)
            .ToList();

        if (candidates.Count == 0)
        {
            Console.WriteLine($"type not found: {query}");
            return 1;
        }

        Console.WriteLine($"# no exact match for '{query}', candidates:");
        foreach (Type c in candidates)
            Console.WriteLine($"   {c.FullName}");
        return 1;
    }

    Console.WriteLine($"### type {type.FullName}");
    if (type.BaseType != null)
        Console.WriteLine($"    base: {type.BaseType.FullName}");

    try
    {
        foreach (Type i in type.GetInterfaces().OrderBy(i => i.FullName))
            Console.WriteLine($"    implements: {FormatType(i)}");
    }
    catch (Exception ex)
    {
        Console.WriteLine($"    implements: <unresolvable: {ex.Message}>");
    }

    const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;

    foreach (MemberInfo m in type.GetMembers(flags).OrderBy(m => m.Name).ThenBy(m => m.MemberType))
    {
        if (m is MethodBase mb && (mb.IsPrivate || mb.IsAssembly))
            continue;

        string line = $"   {m.MemberType}: {Describe(m)}";
        if (string.IsNullOrEmpty(query) == false && args.Length > 3)
        {
            if (line.Contains(args[3], StringComparison.OrdinalIgnoreCase))
                Console.WriteLine(line);
        }
        else
        {
            Console.WriteLine(line);
        }
    }
    return 0;
}

Console.WriteLine($"unknown mode: {mode}");
return 2;


static string Describe(MemberInfo m) => m switch
{
    MethodInfo mi => $"{(mi.IsStatic ? "static " : "")}{FormatType(mi.ReturnType)} {mi.Name}({string.Join(", ", mi.GetParameters().Select(p => FormatType(p.ParameterType) + " " + p.Name))})",
    ConstructorInfo ci => $"{ci.Name}({string.Join(", ", ci.GetParameters().Select(p => FormatType(p.ParameterType) + " " + p.Name))})",
    PropertyInfo pi => $"{FormatType(pi.PropertyType)} {pi.Name} {{ {(pi.GetMethod != null ? "get; " : "")}{(pi.SetMethod != null ? "set; " : "")}}}",
    FieldInfo fi => $"{(fi.IsStatic ? "static " : "")}{FormatType(fi.FieldType)} {fi.Name}",
    EventInfo ei => $"{ei.EventHandlerType?.Name} {ei.Name}",
    Type t => $"type {t.Name}",
    _ => m.ToString() ?? m.Name,
};

static string FormatType(Type type)
{
    if (!type.IsGenericType)
        return type.Name;

    string name = type.Name;
    int tick = name.IndexOf('`');
    if (tick >= 0)
        name = name[..tick];

    string args = string.Join(", ", type.GetGenericArguments().Select(FormatType));
    return $"{name}<{args}>";
}
