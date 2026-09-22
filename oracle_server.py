"""
Sychos Net — Oracle Server v2.0
Zentraler Server: Users, Credits, AI-Chat, Admin
"""
import os, sys, json, time, hmac, socket, platform
import sqlite3, hashlib, uuid, threading
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HOST = os.environ.get("ORACLE_HOST", "0.0.0.0")
PORT = int(os.environ.get("ORACLE_PORT", "7777"))
API_KEY = os.environ.get("ORACLE_API_KEY", "sychos-oracle-2024")
ADMIN_KEY = os.environ.get("ORACLE_ADMIN_KEY", "sychos-admin-2024")
DB_PATH = os.environ.get("ORACLE_DB", "sychos.db")

GEMINI_KEYS = {
    "gemini-2.0-flash": os.environ.get("GEMINI_KEY_FLASH", ""),
    "gemini-2.5-flash": os.environ.get("GEMINI_KEY_PRO", ""),
}

START_TIME = time.time()
db_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            credits REAL DEFAULT 100.0,
            is_banned INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            created_at REAL DEFAULT 0,
            last_login REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            uid TEXT NOT NULL,
            title TEXT DEFAULT 'Neuer Chat',
            model TEXT DEFAULT 'gemini-2.0-flash',
            created_at REAL DEFAULT 0,
            FOREIGN KEY (uid) REFERENCES users(uid)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            created_at REAL DEFAULT 0,
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        );
    """)
    # Ensure admin user exists
    row = db.execute("SELECT uid FROM users WHERE is_admin=1").fetchone()
    if not row:
        uid = str(uuid.uuid4())
        pw = hashlib.sha256("admin".encode()).hexdigest()
        db.execute("INSERT INTO users (uid,email,password_hash,display_name,credits,is_admin,created_at) VALUES (?,?,?,?,?,?,?)",
                   (uid, "admin@sychos.net", pw, "Admin", 99999, 1, time.time()))
        db.commit()
    db.close()

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ═══════════════════════════════════════════════════════════
#  GEMINI AI PROXY
# ═══════════════════════════════════════════════════════════
def call_gemini(model, messages, api_key):
    """Sendet Chat an Gemini API und gibt die Antwort zurück."""
    if not api_key:
        return {"ok": False, "error": f"Kein API Key für {model} konfiguriert"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = json.dumps({"contents": contents}).encode()
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            return {"ok": True, "text": text, "tokens": tokens}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ═══════════════════════════════════════════════════════════
#  HTTP HANDLER
# ═══════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        sys.stderr.write(f"  [{ts}] {fmt % args}\n")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        ln = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(ln)) if ln else {}

    def _get_user(self):
        """Extract user from Bearer token (uid)."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        uid = auth[7:]
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        db.close()
        return dict(row) if row else None

    def _check_admin(self):
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] == ADMIN_KEY

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ───────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/status":
            self._json(200, {"ok": True, "server": "Sychos Oracle", "version": "2.0.0",
                "uptime": int(time.time() - START_TIME), "hostname": socket.gethostname()})
            return

        if path == "/me":
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            if user["is_banned"]:
                self._json(403, {"ok": False, "error": "Account gesperrt"}); return
            self._json(200, {"ok": True, "uid": user["uid"], "email": user["email"],
                "display_name": user["display_name"], "credits": user["credits"],
                "is_admin": user["is_admin"]})
            return

        if path == "/chats":
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            db = get_db()
            rows = db.execute("SELECT * FROM chats WHERE uid=? ORDER BY created_at DESC", (user["uid"],)).fetchall()
            db.close()
            self._json(200, {"ok": True, "chats": [dict(r) for r in rows]})
            return

        if path.startswith("/messages/"):
            cid = path.split("/")[-1]
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            db = get_db()
            rows = db.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (cid,)).fetchall()
            db.close()
            self._json(200, {"ok": True, "messages": [dict(r) for r in rows]})
            return

        if path == "/admin/users":
            if not self._check_admin():
                self._json(403, {"ok": False, "error": "Kein Admin"}); return
            db = get_db()
            rows = db.execute("SELECT uid,email,display_name,credits,is_banned,is_admin FROM users").fetchall()
            db.close()
            self._json(200, {"ok": True, "users": [dict(r) for r in rows]})
            return

        self._json(404, {"ok": False, "error": "Nicht gefunden"})

    # ── POST ──────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        # Register
        if path == "/register":
            email = body.get("email", "").strip().lower()
            pw = body.get("password", "")
            name = body.get("display_name", email.split("@")[0])
            if not email or not pw:
                self._json(400, {"ok": False, "error": "Email und Passwort benötigt"}); return
            db = get_db()
            if db.execute("SELECT uid FROM users WHERE email=?", (email,)).fetchone():
                db.close()
                self._json(409, {"ok": False, "error": "Email bereits registriert"}); return
            uid = str(uuid.uuid4())
            db.execute("INSERT INTO users (uid,email,password_hash,display_name,credits,created_at) VALUES (?,?,?,?,?,?)",
                       (uid, email, hash_pw(pw), name, 100.0, time.time()))
            db.commit(); db.close()
            self._json(200, {"ok": True, "uid": uid, "display_name": name, "credits": 100.0})
            return

        # Login
        if path == "/login":
            email = body.get("email", "").strip().lower()
            pw = body.get("password", "")
            db = get_db()
            row = db.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                             (email, hash_pw(pw))).fetchone()
            if not row:
                db.close()
                self._json(401, {"ok": False, "error": "Falsche Anmeldedaten"}); return
            user = dict(row)
            if user["is_banned"]:
                db.close()
                self._json(403, {"ok": False, "error": "Account gesperrt"}); return
            db.execute("UPDATE users SET last_login=? WHERE uid=?", (time.time(), user["uid"]))
            db.commit(); db.close()
            self._json(200, {"ok": True, "uid": user["uid"], "email": user["email"],
                "display_name": user["display_name"], "credits": user["credits"],
                "is_admin": user["is_admin"]})
            return

        # Neuer Chat
        if path == "/chat/new":
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            cid = str(uuid.uuid4())
            title = body.get("title", "Neuer Chat")
            model = body.get("model", "gemini-2.0-flash")
            db = get_db()
            db.execute("INSERT INTO chats (id,uid,title,model,created_at) VALUES (?,?,?,?,?)",
                       (cid, user["uid"], title, model, time.time()))
            db.commit(); db.close()
            self._json(200, {"ok": True, "chat_id": cid, "title": title, "model": model})
            return

        # Chat löschen
        if path == "/chat/delete":
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            cid = body.get("chat_id", "")
            db = get_db()
            db.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
            db.execute("DELETE FROM chats WHERE id=? AND uid=?", (cid, user["uid"]))
            db.commit(); db.close()
            self._json(200, {"ok": True})
            return

        # AI Chat
        if path == "/chat/send":
            user = self._get_user()
            if not user:
                self._json(401, {"ok": False, "error": "Nicht angemeldet"}); return
            if user["is_banned"]:
                self._json(403, {"ok": False, "error": "Account gesperrt"}); return
            if user["credits"] < 1:
                self._json(402, {"ok": False, "error": "Keine Credits mehr"}); return
            cid = body.get("chat_id", "")
            msg = body.get("message", "").strip()
            model = body.get("model", "gemini-2.0-flash")
            if not msg:
                self._json(400, {"ok": False, "error": "Leere Nachricht"}); return
            api_key = GEMINI_KEYS.get(model, "")
            db = get_db()
            # Save user message
            db.execute("INSERT INTO messages (chat_id,role,content,model,created_at) VALUES (?,?,?,?,?)",
                       (cid, "user", msg, model, time.time()))
            # Build history
            rows = db.execute("SELECT role,content FROM messages WHERE chat_id=? ORDER BY created_at", (cid,)).fetchall()
            history = [{"role": r["role"], "content": r["content"]} for r in rows]
            db.commit(); db.close()
            # Call AI
            result = call_gemini(model, history, api_key)
            if not result["ok"]:
                self._json(500, {"ok": False, "error": result["error"]}); return
            # Save AI response & deduct credits
            cost = max(1, result.get("tokens", 100) / 100)
            db = get_db()
            db.execute("INSERT INTO messages (chat_id,role,content,model,tokens,cost,created_at) VALUES (?,?,?,?,?,?,?)",
                       (cid, "assistant", result["text"], model, result.get("tokens", 0), cost, time.time()))
            db.execute("UPDATE users SET credits = MAX(0, credits - ?) WHERE uid=?", (cost, user["uid"]))
            db.commit(); db.close()
            self._json(200, {"ok": True, "text": result["text"], "tokens": result.get("tokens", 0),
                "cost": cost, "remaining_credits": max(0, user["credits"] - cost)})
            return

        # ── Admin ─────────────────────────────────────────
        if path == "/admin/credits":
            if not self._check_admin():
                self._json(403, {"ok": False, "error": "Kein Admin"}); return
            uid = body.get("uid", "")
            amount = body.get("amount", 0)
            db = get_db()
            db.execute("UPDATE users SET credits = credits + ? WHERE uid=?", (amount, uid))
            db.commit(); db.close()
            self._json(200, {"ok": True})
            return

        if path == "/admin/ban":
            if not self._check_admin():
                self._json(403, {"ok": False, "error": "Kein Admin"}); return
            uid = body.get("uid", "")
            ban = 1 if body.get("ban", True) else 0
            db = get_db()
            db.execute("UPDATE users SET is_banned=? WHERE uid=?", (ban, uid))
            db.commit(); db.close()
            self._json(200, {"ok": True, "banned": bool(ban)})
            return

        self._json(404, {"ok": False, "error": "Nicht gefunden"})


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    init_db()
    print(f"""
  ╔══════════════════════════════════════════════╗
  ║       S Y C H O S   O R A C L E  v2.0       ║
  ╠══════════════════════════════════════════════╣
  ║  Host     : {HOST}:{PORT}
  ║  API Key  : {API_KEY}
  ║  Admin Key: {ADMIN_KEY}
  ║  Datenbank: {DB_PATH}
  ╠══════════════════════════════════════════════╣
  ║  Admin Login: admin@sychos.net / admin       ║
  ╚══════════════════════════════════════════════╝
    """)
    print(f"  → http://{HOST}:{PORT}")
    print(f"  → Ctrl+C zum Beenden\n")
    server = HTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server gestoppt.")
        server.server_close()

if __name__ == "__main__":
    main()
