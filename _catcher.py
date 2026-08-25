import http.server
import urllib.parse

DST = r"D:\CodeIDE\01-Code_item\01-Ai-item\ai-brower-tool\broadcast-live-tools\bili_cookies.txt"

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        c = urllib.parse.parse_qs(q).get("c", [""])[0]
        with open(DST, "w", encoding="utf-8") as f:
            f.write(c)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"OK saved {len(c)} chars".encode())

    def log_message(self, *a):
        pass

http.server.HTTPServer(("127.0.0.1", 18923), H).serve_forever()
