using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewValley;
using StateBridge.Models;

namespace StateBridge.Utils;

/// <summary>
/// Everything the audience sees, drawn over the game.
///
/// Four panels, each for a different reason:
///
///   进度   a strip across the top - day, season, quest, money, today's goal. Someone who just
///          walked into the stream can read the whole situation in one line.
///   智能体  the board down the left - one fixed row per agent, updated in place. The interesting
///          thing about this project is not the farm, it is the argument between the agents working
///          out how to play it.
///   聊天    the room, down the right, newest at the bottom.
///   字幕    what the streamer is saying out loud, at the bottom, where a viewer looks for it.
///
/// The board deliberately does not scroll vertically: a scrolling log is unreadable at a glance and
/// its length changes constantly, so you can never tell who is doing what. Its height is fixed and
/// only the words in a row move. A row too long to fit slides sideways instead of being cut off,
/// slowly, with a rest at each end so a viewer can read both the beginning and the end.
/// </summary>
public static class OverlayPanel
{
    // ---------------------------------------------------------------- state

    private sealed class Entry
    {
        public string Text = "";
        public long At;
        public long ShownAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    }

    private sealed class ChatLine
    {
        public string User = "";
        public string Text = "";
        public Color Ink = Color.White;
    }

    private static readonly Dictionary<string, Entry> Latest = new();
    private static readonly List<ChatLine> Chat = new();
    private static readonly Dictionary<string, float[]> WidthCache = new();
    private static readonly object Gate = new();

    private static string _progress = "";
    private static string _subtitleSpeaker = "";
    private static string _subtitle = "";
    private static long _subtitleAt;

    /// <summary>Fixed rows, in fixed order. The board's height never changes.</summary>
    private static readonly string[] Order =
    {
        "planner", "streamer", "supervisor", "referee", "watcher",
        "doctor", "witness", "gatekeeper", "pipeline", "outcome", "gaps",
    };

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

    private const double FlashSeconds = 2.2;

    // Marquee timing. Slow on purpose: a viewer who cannot finish the sentence has been shown
    // nothing. Roughly three Chinese characters a second, with a rest at each end.
    private const float ScrollSpeed = 34f;
    private const double HoldAtStart = 2.0;
    private const double HoldAtEnd = 1.6;
    private const float LoopGap = 96f;

    // ----------------------------------------------------------------- input

    public static int Count
    {
        get { lock (Gate) return Latest.Count; }
    }

    /// <summary>Agent activity for the board.</summary>
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

                string text = Scrub(item.Value);
                if (text.Length == 0)
                    continue;

                bool changed = !Latest.TryGetValue(tag, out Entry? old) || old.Text != text;
                Latest[tag] = new Entry
                {
                    Text = text,
                    At = now,
                    // A new sentence starts from the beginning, so it can be read from the start.
                    ShownAt = changed ? now : old?.ShownAt ?? now,
                };
            }
        }
    }

    /// <summary>The room's messages.</summary>
    public static void PushChat(IEnumerable<KeyValuePair<string, string>> items)
    {
        lock (Gate)
        {
            foreach (KeyValuePair<string, string> item in items)
            {
                string text = Scrub(item.Value);
                if (text.Length == 0)
                    continue;

                Chat.Add(new ChatLine
                {
                    User = Scrub(item.Key),
                    Text = text,
                    Ink = InkForUser(item.Key ?? ""),
                });
            }

            if (Chat.Count > 200)
                Chat.RemoveRange(0, Chat.Count - 200);
        }
    }

    /// <summary>The streamer's spoken line, shown as a subtitle.</summary>
    public static void Say(string speaker, string text)
    {
        string line = Scrub(text);
        if (line.Length == 0)
            return;

        lock (Gate)
        {
            _subtitleSpeaker = Scrub(speaker);
            _subtitle = line;
            _subtitleAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        }
    }

    /// <summary>The day/quest/money strip across the top.</summary>
    public static void SetProgress(string text)
    {
        lock (Gate)
            _progress = Scrub(text);
    }

    public static void ClearChat()
    {
        lock (Gate)
            Chat.Clear();
    }

    public static void Clear()
    {
        lock (Gate)
        {
            Latest.Clear();
            Chat.Clear();
            WidthCache.Clear();
            _progress = "";
            _subtitle = "";
            _subtitleSpeaker = "";
        }
    }

    private static string Scrub(string? value) =>
        (value ?? "").Replace("\r", " ").Replace("\n", " ").Trim();

    /// <summary>A stable colour per chat name, so a regular is recognisable at a glance.</summary>
    private static Color InkForUser(string user)
    {
        if (string.IsNullOrWhiteSpace(user))
            return new Color(210, 210, 210);

        int hash = 0;
        foreach (char c in user)
            hash = (hash * 31 + c) & 0x7fffffff;

        var palette = new[]
        {
            new Color(150, 220, 255), new Color(255, 200, 150), new Color(190, 255, 190),
            new Color(255, 180, 220), new Color(220, 200, 255), new Color(255, 240, 160),
        };

        return palette[hash % palette.Length];
    }

    // ------------------------------------------------------------------ draw

    public static void Draw(SpriteBatch b, ModConfig config)
    {
        if (!config.ShowOverlay && !config.ShowProgress && !config.ShowChat && !config.ShowSubtitle)
            return;

        try
        {
            float scale = Math.Clamp(config.PanelScale, 0.6f, 2.0f);
            var small = new Meter(Game1.smallFont, scale);

            if (config.ShowProgress)
                DrawProgress(b, config, small);

            if (config.ShowOverlay)
                DrawBoard(b, config, small);

            if (config.ShowChat)
                DrawChat(b, config, small);

            if (config.ShowSubtitle)
                DrawSubtitle(b, config, new Meter(Game1.dialogueFont, scale * 0.62f));
        }
        catch
        {
            // An overlay must never take the game - or the stream - down.
        }
    }

    private static void DrawProgress(SpriteBatch b, ModConfig config, Meter m)
    {
        string text;
        lock (Gate)
            text = _progress;

        if (text.Length == 0)
            return;

        int lineHeight = (int)m.LineHeight + 10;
        var strip = new Rectangle(0, 0, Game1.viewport.Width, lineHeight);

        b.Draw(Game1.staminaRect, strip, new Color(10, 12, 20) * 0.72f);

        float width = m.Width(text);
        float x = Math.Max(12f, (Game1.viewport.Width - width) / 2f);
        m.Draw(b, text, new Vector2(x, 5f), new Color(238, 238, 238) * 0.96f);
    }

    private static void DrawBoard(SpriteBatch b, ModConfig config, Meter m)
    {
        var rows = new List<(string Name, string Text, Color Ink, bool Fresh, long ShownAt)>();
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        lock (Gate)
        {
            foreach (string tag in Order)
            {
                string name = Names[tag];
                if (!Latest.TryGetValue(tag, out Entry? entry))
                {
                    rows.Add((name, "—", new Color(110, 110, 120), false, now));
                    continue;
                }

                Color ink = Inks.TryGetValue(tag, out Color colour) ? colour : new Color(210, 210, 210);
                rows.Add((name, entry.Text, ink, now - entry.At < FlashSeconds * 1000, entry.ShownAt));
            }
        }

        int lineHeight = (int)m.LineHeight + 2;
        int pad = (int)(10 * m.Scale);
        int header = (int)m.LineHeight + 8;
        int nameWidth = (int)m.Width("守门器") + 14;
        int width = Math.Clamp(config.OverlayWidth, 260, 900);

        int height = pad * 2 + header + rows.Count * lineHeight;
        var panel = new Rectangle(Math.Max(0, config.OverlayLeft), Math.Max(0, config.OverlayTop),
                                  width, height);

        b.Draw(Game1.staminaRect, panel, new Color(10, 12, 20) * 0.76f);
        Border(b, panel, new Color(120, 140, 180) * 0.9f);

        var cursor = new Vector2(panel.X + pad, panel.Y + pad);
        m.Draw(b, "智能体实况", cursor, new Color(255, 255, 255) * 0.95f);
        cursor.Y += header;

        int textStart = (int)(cursor.X - panel.X) + nameWidth;

        // The last glyph of a slice is drawn whole, so leave a little more room than the padding.
        int room = panel.Width - textStart - pad - (int)(12 * m.Scale);

        foreach ((string name, string text, Color ink, bool fresh, long shownAt) in rows)
        {
            if (fresh)
            {
                b.Draw(Game1.staminaRect,
                       new Rectangle(panel.X + 3, (int)cursor.Y - 1, panel.Width - 6, lineHeight),
                       new Color(90, 120, 190) * 0.35f);
            }

            m.Draw(b, name, cursor, fresh ? Color.White : ink * 0.92f);

            var textAt = new Vector2(panel.X + textStart, cursor.Y);
            float[] widths = CumulativeWidths(text, m.Scale);
            float total = widths[^1];

            if (total <= room)
                m.Draw(b, text, textAt, ink);
            else
                DrawMarquee(b, m, text, widths, textAt, room, ink, (now - shownAt) / 1000.0);

            cursor.Y += lineHeight;
        }
    }

    private static void DrawChat(SpriteBatch b, ModConfig config, Meter m)
    {
        List<ChatLine> lines;
        lock (Gate)
        {
            int want = Math.Clamp(config.ChatLines, 1, 30);
            lines = Chat.Skip(Math.Max(0, Chat.Count - want)).ToList();
        }

        if (lines.Count == 0)
            return;

        int width = (int)Math.Clamp(m.Width("这是一条大约用来量宽度") * 1.6f, 220, 460);
        int lineHeight = (int)m.LineHeight + 3;
        int pad = (int)(8 * m.Scale);
        int height = pad * 2 + lines.Count * lineHeight;
        int x = Game1.viewport.Width - width - Math.Max(0, config.OverlayLeft);
        int y = Math.Max(8, Game1.viewport.Height - height - (int)(140 * m.Scale));

        var panel = new Rectangle(x, y, width, height);
        b.Draw(Game1.staminaRect, panel, new Color(10, 12, 20) * 0.60f);

        var cursor = new Vector2(panel.X + pad, panel.Y + pad);

        foreach (ChatLine line in lines)
        {
            string name = line.User.Length > 0 ? $"{line.User}: " : "";
            float nameWidth = m.Width(name);

            if (name.Length > 0)
                m.Draw(b, name, cursor, line.Ink);

            string body = Fit(m, line.Text, panel.Width - pad * 2 - (int)nameWidth);
            m.Draw(b, body, new Vector2(cursor.X + nameWidth, cursor.Y), new Color(226, 226, 226));

            cursor.Y += lineHeight;
        }
    }

    private static void DrawSubtitle(SpriteBatch b, ModConfig config, Meter m)
    {
        string speaker, text;
        lock (Gate)
        {
            speaker = _subtitleSpeaker;
            text = _subtitle;

            if (config.SubtitleSeconds > 0 && _subtitleAt > 0)
            {
                double age = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - _subtitleAt) / 1000.0;
                if (age > config.SubtitleSeconds)
                    return;
            }
        }

        if (text.Length == 0)
            return;

        int maxWidth = (int)(Game1.viewport.Width * 0.72f);
        List<string> wrapped = Wrap(m, text, maxWidth, 3);

        int lineHeight = (int)m.LineHeight + 4;
        int pad = (int)(14 * m.Scale);

        // The speaker gets a line of its own. Sharing a line with the words means the first line
        // needs the speaker's width PLUS its own, and a box measured from either one alone clips
        // the sentence it was supposed to contain.
        string nameplate = speaker.Length > 0 ? speaker : "";
        int nameLines = nameplate.Length > 0 ? 1 : 0;

        float widest = 0f;
        foreach (string line in wrapped)
            widest = Math.Max(widest, m.Width(line));
        if (nameLines > 0)
            widest = Math.Max(widest, m.Width(nameplate));

        // A few pixels of slack: glyph measurement rounds, and a box that is one pixel too narrow
        // is the same bug as a box that is a hundred too narrow.
        int width = (int)widest + pad * 2 + (int)(6 * m.Scale);
        int height = (wrapped.Count + nameLines) * lineHeight + pad * 2;
        int x = (Game1.viewport.Width - width) / 2;
        int y = Game1.viewport.Height - height - (int)(26 * m.Scale);

        var panel = new Rectangle(x, y, width, height);
        b.Draw(Game1.staminaRect, panel, new Color(8, 10, 16) * 0.82f);
        Border(b, panel, new Color(150, 170, 210) * 0.85f);

        var cursor = new Vector2(panel.X + pad, panel.Y + pad);

        if (nameLines > 0)
        {
            m.Draw(b, nameplate, cursor, new Color(150, 220, 255));
            cursor.Y += lineHeight;
        }

        foreach (string line in wrapped)
        {
            m.Draw(b, line, cursor, Color.White);
            cursor.Y += lineHeight;
        }
    }

    // -------------------------------------------------------------- marquee

    /// <summary>
    /// Slides a too-long line sideways, looping forever, with a rest at each end.
    ///
    /// The trick is not to scroll to the end of the text and then jump back. One copy of the line
    /// slides left; a second copy sits exactly one *period* behind it, where a period is the text
    /// plus the gap. When the first copy has fully left, the second is in exactly the place the
    /// first started - so the picture at offset == period is identical to the picture at offset == 0,
    /// and the loop point is invisible. Scrolling to `total` and resetting instead gives precisely
    /// what it sounds like: a freeze, then a jump.
    ///
    /// Only the characters inside the window are drawn per copy, so nothing is clipped and nothing
    /// leaks past the panel edge.
    /// </summary>
    private static void DrawMarquee(SpriteBatch b, Meter m, string text, float[] widths,
                                    Vector2 at, int room, Color ink, double elapsed)
    {
        float total = widths[^1];
        if (total <= 1f)
            return;

        float speed = Math.Max(1f, ScrollSpeed * m.Scale);
        float period = total + LoopGap * m.Scale;              // one repetition, including the gap
        double cycle = HoldAtStart + period / speed + HoldAtEnd;
        if (cycle <= 0.01)
            return;

        double progress = elapsed % cycle;
        float offset = progress switch
        {
            _ when progress < HoldAtStart => 0f,
            _ when progress < HoldAtStart + period / speed =>
                (float)((progress - HoldAtStart) * speed),
            _ => period,                                        // clamped: renders as offset 0
        };

        float left = at.X;
        float right = at.X + room;

        // period > total > room, so two copies always cover the window.
        for (int i = 0; i < 2; i++)
        {
            float copyX = left - offset + i * period;
            if (copyX >= right || copyX + total <= left)
                continue;

            DrawCopy(b, m, text, widths, copyX, at.Y, ink, left, right);
        }
    }

    /// <summary>Draws the part of one copy that falls inside the window.</summary>
    private static void DrawCopy(SpriteBatch b, Meter m, string text, float[] widths,
                                 float copyX, float y, Color ink, float left, float right)
    {
        float total = widths[^1];
        float from = Math.Max(0f, left - copyX);          // window, in this copy's own coordinates
        float to = Math.Min(total, right - copyX);
        if (to <= from)
            return;

        int first = IndexAt(widths, from);
        int last = Math.Min(IndexAt(widths, to) + 1, text.Length);
        if (last <= first)
            return;

        m.Draw(b, text[first..last], new Vector2(copyX + widths[first], y), ink);
    }

    private static int IndexAt(float[] widths, float px)
    {
        for (int i = 1; i < widths.Length; i++)
        {
            if (widths[i] > px)
                return i - 1;
        }

        return widths.Length - 1;
    }

    /// <summary>
    /// Text positions of every character boundary, cached per string - this runs every frame.
    ///
    /// Measured as prefixes of the whole string, so a slice lines up with how the string would be
    /// drawn in one piece. Measuring characters one at a time drifts away from that.
    /// </summary>
    private static float[] CumulativeWidths(string text, float scale)
    {
        string key = $"{scale:0.##}|{text}";
        if (WidthCache.TryGetValue(key, out float[]? cached))
            return cached;

        var widths = new float[text.Length + 1];
        for (int i = 1; i <= text.Length; i++)
            widths[i] = Game1.smallFont.MeasureString(text[..i]).X * scale;

        if (WidthCache.Count > 300)
            WidthCache.Clear();

        WidthCache[key] = widths;
        return widths;
    }

    // --------------------------------------------------------------- pieces

    /// <summary>A font plus the scale everything is measured and drawn at.</summary>
    private sealed class Meter
    {
        private readonly SpriteFont _font;

        public Meter(SpriteFont font, float scale)
        {
            _font = font;
            Scale = Math.Clamp(scale, 0.5f, 2.5f);
        }

        public float Scale { get; }
        public float LineHeight => _font.MeasureString("Ag").Y * Scale;
        public float Width(string text) => _font.MeasureString(text ?? "").X * Scale;

        public void Draw(SpriteBatch b, string text, Vector2 at, Color color)
        {
            if (string.IsNullOrEmpty(text))
                return;

            b.DrawString(_font, text, at, color, 0f, Vector2.Zero, Scale, SpriteEffects.None, 0f);
        }
    }

    private static string Fit(Meter m, string text, int maxWidth)
    {
        if (maxWidth <= 8)
            return "";

        if (m.Width(text) <= maxWidth)
            return text;

        string cut = text;
        while (cut.Length > 4 && m.Width(cut + "…") > maxWidth)
            cut = cut[..^1];

        return cut + "…";
    }

    private static List<string> Wrap(Meter m, string text, int maxWidth, int maxLines)
    {
        var lines = new List<string>();
        string current = "";

        for (int i = 0; i < text.Length; i++)
        {
            string candidate = current + text[i];

            if (m.Width(candidate) <= maxWidth || current.Length == 0)
            {
                current = candidate;
                continue;
            }

            lines.Add(current);
            current = text[i].ToString();

            if (lines.Count == maxLines - 1)
            {
                // The last line: fill it, then say there was more.
                string body = "";
                foreach (char c in text[i..])
                {
                    if (body.Length > 0 && m.Width(body + c + "…") > maxWidth)
                        break;
                    body += c;
                }

                lines.Add(body + "…");
                return lines;
            }
        }

        if (current.Length > 0)
            lines.Add(current);

        return lines;
    }

    private static void Border(SpriteBatch b, Rectangle r, Color color)
    {
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Bottom - 1, r.Width, 1), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.X, r.Y, 1, r.Height), color);
        b.Draw(Game1.staminaRect, new Rectangle(r.Right - 1, r.Y, 1, r.Height), color);
    }
}
