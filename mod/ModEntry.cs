using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;
using StateBridge.Models;
using StateBridge.Utils;

namespace StateBridge;

/// <summary>
/// SMAPI entry point: hosts a local HTTP API so an external agent can
/// read structured game state (GET /state) and push actions (POST /action).
/// </summary>
public class ModEntry : Mod
{
    public const string ApiVersion = "1";

    private ModConfig _config = new();
    private HttpApiServer? _server;
    private readonly ConcurrentQueue<IGameThreadTask> _tasks = new();
    private bool _titleIntroSkipped;

    public override void Entry(IModHelper helper)
    {
        _config = helper.ReadConfig<ModConfig>();

        helper.Events.GameLoop.GameLaunched += OnGameLaunched;
        helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
        helper.Events.GameLoop.ReturnedToTitle += OnReturnedToTitle;
        helper.Events.GameLoop.SaveLoaded += OnSaveLoaded;
    }


    private void OnGameLaunched(object? sender, GameLaunchedEventArgs e)
    {
        try
        {
            _server = new HttpApiServer(_config.BindAddress, _config.Port, HandleRequest);
            _server.Start();

            string url = $"http://{(_config.BindAddress == "0.0.0.0" ? "127.0.0.1" : _config.BindAddress)}:{_config.Port}";
            Monitor.Log($"StateBridge API ready at {url} (actions: {_config.EnableActions})", LogLevel.Info);
            Monitor.Log($"Endpoints: GET {url}/state | POST {url}/action | GET {url}/health", LogLevel.Info);
        }
        catch (Exception ex)
        {
            Monitor.Log($"Failed to start StateBridge HTTP server: {ex}", LogLevel.Error);
        }
    }


    private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
    {
        MovementController.OnUpdateTicked();
        ActionProcessor.ProcessPendingNewGame(Monitor);
        ActionProcessor.ProcessPendingClicks(Monitor);
        ActionProcessor.ProcessPendingWork(Monitor);
        ActionProcessor.ProcessPendingSleep(Monitor);
        ChoreController.OnUpdateTicked();
        TrySkipTitleIntro();
        KeepGameRunning();

        while (_tasks.TryDequeue(out IGameThreadTask task))
            task.Run(Monitor);
    }


    /// <summary>
    /// Stardew pauses itself the moment its window loses focus. For an automated player that is
    /// fatal: every action still "succeeds" while the world is frozen (the clock stops and the
    /// farmer never moves). Turn the behaviour off and clear any pause that already happened.
    /// </summary>
    private void KeepGameRunning()
    {
        if (Game1.player == null || Game1.currentLocation == null)
            return; // title screen / loading: leave the game alone

        try
        {
            if (Game1.options != null && Game1.options.pauseWhenOutOfFocus)
                Game1.options.pauseWhenOutOfFocus = false;

            if (Game1.paused)
                Game1.paused = false;
        }
        catch (Exception ex)
        {
            Monitor.LogOnce($"could not clear the out-of-focus pause: {ex.Message}", LogLevel.Warn);
        }
    }


    /// <summary>
    /// The title screen animates its buttons in over a few seconds. Skip that so an agent
    /// can click "Load" immediately after launch, without waiting on the animation.
    /// </summary>
    private void TrySkipTitleIntro()
    {
        if (Game1.activeClickableMenu is not TitleMenu title)
        {
            _titleIntroSkipped = false;
            return;
        }

        // A submenu (LoadGameMenu, options, ...) is open; leave it alone.
        if (TitleMenu.subMenu != null)
            return;

        if (_titleIntroSkipped)
            return;

        try
        {
            title.skipToTitleButtons();
            _titleIntroSkipped = true;
            Monitor.Log("Title intro skipped; menu buttons are ready.", LogLevel.Trace);
        }
        catch (Exception ex)
        {
            Monitor.Log($"skipToTitleButtons failed: {ex.Message}", LogLevel.Warn);
        }
    }


    private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
    {
        Farmer? f = Game1.player;
        Monitor.Log(
            $"Save loaded: {f?.Name} | {Game1.dayOfMonth} {Game1.currentSeason} y{Game1.year} | " +
            $"@{(f != null ? $"{(int)f.Tile.X},{(int)f.Tile.Y}" : "?")} in {Game1.currentLocation?.Name}",
            LogLevel.Info);
    }


    private void OnReturnedToTitle(object? sender, ReturnedToTitleEventArgs e)
    {
        MovementController.Stop();
        _titleIntroSkipped = false;
    }


    /// <summary>Routes an HTTP request. Runs on the HTTP thread.</summary>
    private ApiResponse HandleRequest(string method, string target, string? body)
    {
        // Strip query string.
        int q = target.IndexOf('?');
        string path = q >= 0 ? target[..q] : target;
        path = path.TrimEnd('/');
        if (path.Length == 0)
            path = "/";

        if (method == "OPTIONS")
            return ApiResponse.Text(204, "");

        switch (path)
        {
            case "/health":
            {
                bool hasSave = Game1.player != null && Game1.currentLocation != null;
                bool free = hasSave && GameQuery.IsPlayerFree();
                string healthBody = $"{{\"ok\":true,\"api_version\":\"{ApiVersion}\"," +
                                    $"\"actions_enabled\":{_config.EnableActions.ToString().ToLowerInvariant()}," +
                                    $"\"has_save\":{hasSave.ToString().ToLowerInvariant()}," +
                                    $"\"player_free\":{free.ToString().ToLowerInvariant()}," +
                                    $"\"blocked_reason\":\"{(free ? "" : GameQuery.BlockedReason())}\"}}";
                return ApiResponse.Json(200, healthBody);
            }

            case "/state":
                // Always answer, even before a save is loaded: at the title screen the UI block
                // is the only thing the agent can act on.
                return RunOnGameThread(
                    () => ApiResponse.Json(200, StateSnapshotBuilder.BuildJson(_config, Monitor)),
                    timeoutMs: 3000);

            case "/action":
                if (method == "GET")
                    return ApiResponse.Text(405, "use POST /action with a JSON body");

                return RunOnGameThread(() =>
                {
                    (bool ok, string detail, Dictionary<string, object?>? data) =
                        ActionProcessor.Execute(body, _config.EnableActions, Monitor);

                    string json = JsonSerializer.Serialize(new ActionResult
                    {
                        Ok = ok,
                        Action = TryExtractActionName(body),
                        Detail = detail,
                        ReasonCode = ActionProcessor.ClassifyFailure(ok, detail),
                        Data = data,
                    });
                    return ApiResponse.Json(ok ? 200 : 400, json);
                }, timeoutMs: 5000);

            default:
                return ApiResponse.Json(404, $"{{\"error\":\"unknown path '{path}'\"}}");
        }
    }


    private ApiResponse RunOnGameThread(Func<ApiResponse> work, int timeoutMs)
    {
        var task = new GameThreadTask(work);
        _tasks.Enqueue(task);

        if (!task.Wait(timeoutMs))
            return ApiResponse.Json(503, "{\"error\":\"game thread busy (no tick processed)\"}");

        return task.Result ?? ApiResponse.Json(500, "{\"error\":\"no result produced\"}");
    }


    private static string TryExtractActionName(string? body)
    {
        if (string.IsNullOrWhiteSpace(body))
            return "";

        try
        {
            using JsonDocument doc = JsonDocument.Parse(body);
            return doc.RootElement.TryGetProperty("action", out JsonElement el)
                ? el.GetString() ?? ""
                : "";
        }
        catch
        {
            return "";
        }
    }


    private interface IGameThreadTask
    {
        void Run(IMonitor monitor);
    }


    /// <summary>Runs a delegate on the game thread and signals completion.</summary>
    private sealed class GameThreadTask : IGameThreadTask
    {
        private readonly Func<ApiResponse> _work;
        private readonly ManualResetEventSlim _done = new(false);

        public ApiResponse? Result { get; private set; }
        public Exception? Error { get; private set; }

        public GameThreadTask(Func<ApiResponse> work) => _work = work;

        public void Run(IMonitor monitor)
        {
            try
            {
                Result = _work();
            }
            catch (Exception ex)
            {
                Error = ex;
                monitor.Log($"Game-thread task failed: {ex}", LogLevel.Error);
            }
            finally
            {
                _done.Set();
            }
        }

        public bool Wait(int timeoutMs) => _done.Wait(timeoutMs);
    }
}
