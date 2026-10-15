using System.Net;
using System.Net.Sockets;
using System.Text;

namespace StateBridge.Utils;

/// <summary>Response returned by the request handler.</summary>
public class ApiResponse
{
    public int Status { get; set; } = 200;
    public string ContentType { get; set; } = "application/json; charset=utf-8";
    public string Body { get; set; } = "";

    public static ApiResponse Json(int status, string body) => new() { Status = status, Body = body };
    public static ApiResponse Text(int status, string body) => new()
    {
        Status = status,
        ContentType = "text/plain; charset=utf-8",
        Body = body,
    };
}


/// <summary>
/// Tiny blocking HTTP/1.1 server over raw TCP. Avoids HttpListener URL-ACL requirements
/// (no admin rights needed) and has zero external dependencies.
/// Runs entirely on a background thread; handlers decide how to hop to the game thread.
/// </summary>
public class HttpApiServer
{
    private readonly IPAddress _bind;
    private readonly int _port;
    private readonly Func<string, string, string?, ApiResponse> _handler;

    private TcpListener? _listener;
    private Thread? _thread;
    private volatile bool _running;

    public HttpApiServer(string bindAddress, int port, Func<string, string, string?, ApiResponse> handler)
    {
        _bind = IPAddress.Parse(bindAddress);
        _port = port;
        _handler = handler;
    }

    public void Start()
    {
        _listener = new TcpListener(_bind, _port);
        _listener.Start();
        _running = true;

        _thread = new Thread(AcceptLoop)
        {
            IsBackground = true,
            Name = "StateBridge-Http",
        };
        _thread.Start();
    }

    public void Stop()
    {
        _running = false;
        try { _listener?.Stop(); } catch { /* already stopped */ }
    }

    private void AcceptLoop()
    {
        while (_running)
        {
            TcpClient client;
            try
            {
                client = _listener!.AcceptTcpClient();
            }
            catch
            {
                break; // listener closed
            }

            ThreadPool.QueueUserWorkItem(_ => HandleClient(client));
        }
    }

    private void HandleClient(TcpClient client)
    {
        try
        {
            using (client)
            {
                client.ReceiveTimeout = 10_000;
                client.SendTimeout = 10_000;
                NetworkStream stream = client.GetStream();

                string? requestHead = ReadHead(stream);
                if (requestHead == null)
                    return;

                string[] lines = requestHead.Split("\r\n", StringSplitOptions.None);
                string[] requestLine = lines[0].Split(' ', 3);
                if (requestLine.Length < 2)
                    return;

                string method = requestLine[0].ToUpperInvariant();
                string target = requestLine[1];

                int contentLength = 0;
                for (int i = 1; i < lines.Length; i++)
                {
                    string line = lines[i];
                    if (line.Length == 0)
                        break;

                    int colon = line.IndexOf(':');
                    if (colon <= 0)
                        continue;

                    string headerName = line[..colon].Trim();
                    if (headerName.Equals("Content-Length", StringComparison.OrdinalIgnoreCase))
                        int.TryParse(line[(colon + 1)..].Trim(), out contentLength);
                }

                string? body = null;
                if (contentLength > 0)
                    body = ReadBody(stream, contentLength);

                ApiResponse response;
                try
                {
                    response = _handler(method, target, body);
                }
                catch (Exception ex)
                {
                    response = ApiResponse.Json(500, $"{{\"error\":\"{Escape(ex.Message)}\"}}");
                }

                WriteResponse(stream, response);
            }
        }
        catch
        {
            // client disconnected mid-request; nothing to do
        }
    }

    private static string? ReadHead(NetworkStream stream)
    {
        var buffer = new byte[1];
        var head = new StringBuilder();

        while (head.Length < 16_384)
        {
            int read = stream.Read(buffer, 0, 1);
            if (read <= 0)
                return null;

            head.Append((char)buffer[0]);
            if (head.Length >= 4 && head.ToString().EndsWith("\r\n\r\n", StringComparison.Ordinal))
                return head.ToString();
        }
        return null;
    }

    private static string? ReadBody(NetworkStream stream, int contentLength)
    {
        var bytes = new byte[contentLength];
        int offset = 0;

        while (offset < contentLength)
        {
            int read = stream.Read(bytes, offset, contentLength - offset);
            if (read <= 0)
                break;
            offset += read;
        }

        if (offset == 0)
            return null;

        return Encoding.UTF8.GetString(bytes, 0, offset);
    }

    private static void WriteResponse(NetworkStream stream, ApiResponse response)
    {
        byte[] bodyBytes = Encoding.UTF8.GetBytes(response.Body ?? "");
        string statusText = response.Status switch
        {
            200 => "OK",
            400 => "Bad Request",
            404 => "Not Found",
            405 => "Method Not Allowed",
            500 => "Internal Server Error",
            503 => "Service Unavailable",
            _ => "Unknown",
        };

        string head =
            $"HTTP/1.1 {response.Status} {statusText}\r\n" +
            $"Content-Type: {response.ContentType}\r\n" +
            $"Content-Length: {bodyBytes.Length}\r\n" +
            "Access-Control-Allow-Origin: *\r\n" +
            "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n" +
            "Access-Control-Allow-Headers: Content-Type\r\n" +
            "Connection: close\r\n" +
            "\r\n";

        byte[] headBytes = Encoding.ASCII.GetBytes(head);
        stream.Write(headBytes, 0, headBytes.Length);
        stream.Write(bodyBytes, 0, bodyBytes.Length);
        stream.Flush();
    }

    private static string Escape(string text) =>
        text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ");
}
