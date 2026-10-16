using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewValley;
using StateBridge.Models;

namespace StateBridge.Utils;

/// <summary>
/// A live board of what the agents are doing, down the left of the screen.
///
/// One fixed row per agent, updated in place. It deliberately does not scroll: a scrolling log is
/// unreadable at a glance and its length changes constantly, so you can never tell who is doing
/// what. Here the shape of the board never changes - 规划AI is always the second row - and only the
/// words in a row move. A row that just changed is highlighted for a moment, which is what draws the
/// eye to what is happening now.
///
/// This exists because the interesting thing about this project is not the farm. It is the argument
/// between the agents working out how to play it, and none of that is visible in the game itself.
/// </summary>
public static class OverlayPanel
{
    private sealed record Entry(string Text, long At);

    /// <summary>Fixed rows, in fixed order. The board's height never changes.</summary>
    private static readonly string[] Order =
    {
        "planner", "streamer", "supervisor", "referee", "watcher",
        "doctor", "witness", "gatekeeper", "pipeline", "outcome", "gaps",
    };

    /// <summary>Chinese names for each stream, so the board reads as one thing.</summary>
    private static readonly Dictionary<string, string> Names = new()
    {
        ["planner"] = "规划AI",
        ["streamer"] = "主播AI",
        ["supervisor"] = "督导AI",
        ["referee"] = "裁判AI",
        ["watcher"] = "守望者",
        ["doctor"] = "诊断AI",
        ["witness"] = "见证层",
        ["gatekeeper"] = "守门器",
        ["pipeline"] = "流水线",
        ["outcome"] = "进展",
        ["gaps"] = "缺口",
    };

    /// <summary>Streams that belong on another row rather than one of their own.</summary>
    private static readonly Dictionary<string, string> Aliases = new()
    {
        ["run"] = "pipeline",
        ["prescription"] = "doctor",
        ["learning"] = "witness",
        ["defect"] = "outcome",
        ["memory"] = "outcome",
        ["observation"] = "witness",
        ["reflection"] = "outcome",
    };

    private static readonly Dictionary<string, Color> Inks = new()
    {
        ["planner"] = new Color(126, 200, 255),
        ["streamer"] = new Color(240, 240, 240),
        ["supervisor"] = new Color(255, 190, 120),
        ["referee"] = new Color(214, 160, 255),
        ["watcher"] = new Color(120, 240, 240),
        ["doctor"] = new Color(255, 235, 130),
        ["witness"] = new Color(150, 230, 170),
        ["gatekeeper"] = new Color(255, 220, 120),
        ["pipeline"] = new Color(178, 178, 190),
        ["outcome"] = new Color(255, 150, 150),
        ["gaps"] = new Color(255, 130, 130),
    };

    private static readonly Dictionary<string, Entry> Latest = new();
    private static readonly object Gate = new();

    /// <summary>How long a row stays highlighted after it changes.</summary>
    private const double FlashSeconds = 2.2;

    public static int Count
    {
        get { lock (Gate) return Latest.Count; }
    }

    public static void Push(IEnumerable<KeyValuePair<string, string>> items)
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        lock (Gate)
        {
            foreach (KeyValuePair<string, string> item in items)
            {
                string tag = (item.Key ?? "").Trim().ToLowerInvariant();
                if (Aliases.TryGetValue(tag, out string? alias))
                    tag = alias;

                if (!Names.ContainsKey(tag))
                    continue;                       // not a row on this board

                string text = (item.Value ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
                if (text.Length == 0)
                    continue;

                Latest[tag] = new Entry(text, now);
            }
        }
    }

    public static void Clear()
    {
        lock (Gate)
            Latest.Clear();
    }

    /// <summary>Draws the board. Safe every frame; does nothing when switched off.</summary>
    public static void Draw(SpriteBatch b, ModConfig config)
    {
        if (!config.ShowOverlay)
            return;

        var rows = new List<(string Name, string Text, Color Ink, bool Fresh)>();
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        lock (Gate)
        {
            foreach (string tag in Order)
            {
                string name = Names[tag];
                if (!Latest.TryGetValue(tag, out Entry? entry))
                {
                    rows.Add((name, "—", new Color(110, 110, 120), false));
                    continue;
                }

                bool fresh = now - entry.At < FlashSeconds * 1000;
                Color ink = Inks.TryGetValue(tag, out Color colour) ? colour : new Color(210, 210, 210);
                rows.Add((name, entry.Text, ink, fresh));
            }
        }

        int width = Math.Clamp(config.OverlayWidth, 260, 900);
        int lineHeight = (int)Game1.smallFont.MeasureString("Ag").Y + 2;
        int pad = 10;
        int header = (int)Game1.smallFont.MeasureString("Ag").Y + 8;

        int height = pad * 2 + header + rows.Count * lineHeight;
        int x = 12;
        int y = Math.Max(12, (Game1.viewport.Height - height) / 2 - 30);

        var panel = new Rectangle(x, y, width, height);

        b.Draw(Game1.staminaRect, panel, new Color(10, 12, 20) * 0.74f);
        Border(b, panel, new Color(120, 140, 180) * 0.9f);

        var cursor = new Vector2(panel.X + pad, panel.Y + pad);
        b.DrawString(Game1.smallFont, "智能体实况", cursor, new Color(255, 255, 255) * 0.95f);
        cursor.Y += header;

        foreach ((string name, string text, Color ink, bool fresh) in rows)
        {
            if (fresh)
            {
                // A brief bar behind the row: what changed, right now, is where the eye should go.
                b.Draw(Game1.staminaRect,
                       new Rectangle(panel.X + 3, (int)cursor.Y - 1, panel.Width - 6, lineHeight),
                       new Color(90, 120, 190) * 0.35f);
            }

            Color nameInk = fresh ? Color.White : ink * 0.92f;
            b.DrawString(Game1.smallFont, name, cursor, nameInk);

            var textAt = new Vector2(cursor.X + 62, cursor.Y);
            int room = panel.Right - pad - (int)textAt.X;
            b.DrawString(Game1.smallFont, Fit(text, room), textAt, ink);

            cursor.Y += lineHeight;
        }
    }

    /// <summary>Trims a line to the space available.</summary>
    private static string Fit(string text, int maxWidth)
    {
        if (maxWidth <= 8)
            return "";

        if (Game1.smallFont.MeasureString(text).X <= maxWidth)
            return text;

        string cut = text;
        while (cut.Length > 4 && Game1.smallFont.MeasureString(cut + "…").X > maxWidth)
            cut = cut[..^1];

        return cut + "…";
    }

    private static void Border(SpriteBatch b, Rectangle r, Color color)
    {
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Bottom - 1, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, 1, r.Height), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.Right - 1, r.Y, 1, r.Height), color);
    }
}
