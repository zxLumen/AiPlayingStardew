using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewValley;
using StateBridge.Models;

namespace StateBridge.Utils;

/// <summary>
/// The agents' activity, down the left of the screen.
///
/// This exists because the interesting thing about this project is not the farm - it is the argument
/// happening between the agents working out how to play it. The planner deciding, the streamer
/// acting, the watcher catching something, the referee scoring, the doctor diagnosing. None of that
/// is visible in the game itself, so it is drawn on top of it.
///
/// Lines are pushed in by the agent side over the API; the panel is a plain ring buffer, so it never
/// grows and never costs anything when nobody is sending.
/// </summary>
public static class OverlayPanel
{
    private sealed record Line(string Tag, string Text, Color Ink, long At);

    private const int MaxKept = 400;

    private static readonly List<Line> Lines = new();
    private static readonly object Gate = new();

    /// <summary>Colours per agent, so a glance tells you who is talking.</summary>
    private static Color InkFor(string tag) => tag switch
    {
        "planner" => new Color(126, 200, 255),
        "streamer" => new Color(238, 238, 238),
        "supervisor" => new Color(255, 190, 120),
        "referee" => new Color(214, 160, 255),
        "memory" => new Color(150, 230, 170),
        "learning" => new Color(150, 230, 170),
        "gatekeeper" => new Color(255, 220, 120),
        "watcher" => new Color(120, 240, 240),
        "doctor" => new Color(255, 235, 130),
        "pipeline" => new Color(170, 170, 180),
        "outcome" => new Color(255, 140, 140),
        "gaps" => new Color(255, 130, 130),
        "defect" => new Color(255, 130, 130),
        _ => new Color(200, 200, 200),
    };

    public static void Push(IEnumerable<KeyValuePair<string, string>> items)
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

        lock (Gate)
        {
            foreach (KeyValuePair<string, string> item in items)
            {
                string text = (item.Value ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
                if (text.Length == 0)
                    continue;

                string tag = (item.Key ?? "").Trim().ToLowerInvariant();
                Lines.Add(new Line(tag, text, InkFor(tag), now));
            }

            if (Lines.Count > MaxKept)
                Lines.RemoveRange(0, Lines.Count - MaxKept);
        }
    }

    public static void Clear()
    {
        lock (Gate)
            Lines.Clear();
    }

    public static int Count
    {
        get { lock (Gate) return Lines.Count; }
    }

    /// <summary>Draws the panel. Safe to call every frame; does nothing when empty or switched off.</summary>
    public static void Draw(SpriteBatch b, ModConfig config)
    {
        if (!config.ShowOverlay)
            return;

        List<Line> snapshot;
        lock (Gate)
        {
            if (Lines.Count == 0)
                return;

            int want = Math.Clamp(config.OverlayLines, 4, 60);
            snapshot = Lines.Skip(Math.Max(0, Lines.Count - want)).ToList();
        }

        int width = Math.Clamp(config.OverlayWidth, 240, 900);
        int lineHeight = (int)Game1.smallFont.MeasureString("Ag").Y;
        int pad = 10;
        int headerHeight = (int)Game1.smallFont.MeasureString("Ag").Y + 6;

        int height = pad * 2 + headerHeight + snapshot.Count * lineHeight;
        int x = 12;
        int y = Math.Max(12, (Game1.viewport.Height - height) / 2 - 40);

        var panel = new Rectangle(x, y, width, height);

        // A dark, slightly transparent slab: readable over a bright farm, not hiding it.
        b.Draw(Game1.staminaRect, panel, new Color(10, 12, 20) * 0.72f);
        DrawBorder(b, panel, new Color(120, 140, 180) * 0.9f);

        var cursor = new Vector2(panel.X + pad, panel.Y + pad);
        string title = $"AGENTS   ({snapshot.Count})";
        b.DrawString(Game1.smallFont, title, cursor, new Color(255, 255, 255) * 0.95f);
        cursor.Y += headerHeight;

        foreach (Line line in snapshot)
        {
            string prefix = line.Tag.Length > 0 ? $"{line.Tag,-10} " : "";
            string text = Fit(prefix + line.Text, width - pad * 2, line.Tag.Length > 0);
            b.DrawString(Game1.smallFont, text, cursor, line.Ink);
            cursor.Y += lineHeight;
        }
    }

    /// <summary>Trims a line to the panel width, keeping the tag if there is one.</summary>
    private static string Fit(string text, int maxWidth, bool hasTag)
    {
        if (Game1.smallFont.MeasureString(text).X <= maxWidth)
            return text;

        string cut = text;
        while (cut.Length > 4 && Game1.smallFont.MeasureString(cut + "…").X > maxWidth)
            cut = cut[..^1];

        return cut + "…";
    }

    private static void DrawBorder(SpriteBatch b, Rectangle r, Color color)
    {
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Bottom - 1, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, 1, r.Height), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.Right - 1, r.Y, 1, r.Height), color);
    }
}
