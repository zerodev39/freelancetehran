# -*- coding: utf-8 -*-
"""
FREELANCE TEHRAN — Freelance Marketplace
Modular Python + HTML + CSS + JavaScript
Run: python server.py
Open: http://127.0.0.1:8000

Database: freelance_cube.db
Python standard library only.
"""

import os
import re
import json
import hmac
import time
import sqlite3
import secrets
import hashlib
import threading

from pathlib import Path
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


# ============================================================
# CONFIG
# ============================================================

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
DATABASE = Path(__file__).with_name("freelance_cube.db")

SESSIONS = {}
SESSION_LOCK = threading.RLock()

CATEGORIES = [
    "طراحی گرافیک",
    "برنامه‌نویسی",
    "تولید محتوا",
    "بازاریابی",
    "ویدئو",
    "صدا",
    "مهندسی",
    "سایر"
]


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    con = sqlite3.connect(DATABASE, timeout=25)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def initialize_database():
    with db_connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'both',
            bio TEXT NOT NULL DEFAULT '',
            skills TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            budget INTEGER NOT NULL,
            deadline TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            completion_status TEXT NOT NULL DEFAULT 'none',
            completion_requested_at TEXT DEFAULT '',
            completion_confirmed_at TEXT DEFAULT '',
            completion_rejected_at TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            freelancer_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            days INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, freelancer_id),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(freelancer_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(proposal_id) REFERENCES proposals(id) ON DELETE CASCADE,
            FOREIGN KEY(sender_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            reviewee_id INTEGER NOT NULL,
            score REAL NOT NULL CHECK(score >= 0.5 AND score <= 10),
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(proposal_id, reviewer_id),
            FOREIGN KEY(proposal_id) REFERENCES proposals(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewee_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_projects_owner
        ON projects(owner_id);

        CREATE INDEX IF NOT EXISTS idx_projects_status
        ON projects(status);

        CREATE INDEX IF NOT EXISTS idx_proposals_project
        ON proposals(project_id);

        CREATE INDEX IF NOT EXISTS idx_proposals_freelancer
        ON proposals(freelancer_id);

        CREATE INDEX IF NOT EXISTS idx_ratings_reviewee
        ON ratings(reviewee_id);

        """)

        # Migration support for databases created by earlier versions.
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(projects)").fetchall()
        }

        if "updated_at" not in columns:
            con.execute(
                "ALTER TABLE projects ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            )

        # Safe, additive migrations for existing installations.
        project_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(projects)").fetchall()
        }
        project_migrations = {
            "completion_status": "TEXT NOT NULL DEFAULT 'none'",
            "completion_requested_at": "TEXT DEFAULT ''",
            "completion_confirmed_at": "TEXT DEFAULT ''",
            "completion_rejected_at": "TEXT DEFAULT ''",
        }
        for column_name, column_type in project_migrations.items():
            if column_name not in project_columns:
                con.execute(
                    f"ALTER TABLE projects ADD COLUMN {column_name} {column_type}"
                )

        proposal_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(proposals)").fetchall()
        }

        if "updated_at" not in proposal_columns:
            con.execute(
                "ALTER TABLE proposals ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            )

        # Migrate older message tables before adding an index that depends on
        # proposal_id. This is intentionally additive and preserves old rows.
        message_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "proposal_id" not in message_columns:
            con.execute("ALTER TABLE messages ADD COLUMN proposal_id INTEGER")
        con.execute("CREATE INDEX IF NOT EXISTS idx_messages_proposal ON messages(proposal_id)")


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean(value, limit=5000):
    return str(value or "").strip()[:limit]


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        260000
    ).hex()
    return salt + "$" + result


def verify_password(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        actual = hash_password(password, salt).split("$", 1)[1]
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def public_user(row):
    if not row:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "bio": row["bio"],
        "skills": row["skills"]
    }


def parse_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def valid_email(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


def current_jalali_year():
    today = datetime.now()
    return today.year - (621 if (today.month > 3 or (today.month == 3 and today.day >= 20)) else 622)


def jalali_is_leap_year(year):
    breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
              1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178]
    if year < breaks[0] or year >= breaks[-1]:
        return False
    jp = breaks[0]
    jump = 0
    for jm in breaks[1:]:
        jump = jm - jp
        if year < jm:
            break
        jp = jm
    n = year - jp
    if jump - n < 6:
        n = n - jump + ((jump + 4) // 33) * 33
    value = ((n + 1) % 33) - 1
    leap = value - int(value / 4) * 4
    if leap == -1:
        leap = 4
    return leap == 0


def valid_jalali_date(value):
    """
    Accepts Persian calendar date as entered by the user:
    3/4/1405
    03/04/1405
    ۳/۴/۱۴۰۵
    """
    value = clean(value, 30)
    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    ))
    value = value.replace("-", "/").replace(".", "/")

    if not value:
        return True

    parts = value.split("/")
    if len(parts) != 3:
        return False

    try:
        day, month, year = map(int, parts)
    except ValueError:
        return False

    if year < current_jalali_year() or year > 1600:
        return False

    if month < 1 or month > 12:
        return False

    if month <= 6 and day > 31:
        return False

    if month >= 7 and day > 30:
        return False
    if month == 12 and day == 30 and not jalali_is_leap_year(year):
        return False

    return 1 <= day <= 31


def normalize_jalali_date(value):
    value = clean(value, 30)
    value = value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    ))
    value = value.replace("-", "/").replace(".", "/")

    if not value:
        return ""

    parts = value.split("/")
    if len(parts) != 3:
        return value

    try:
        day, month, year = map(int, parts)
        return f"{day:02d}/{month:02d}/{year:04d}"
    except ValueError:
        return value


def get_session_token(handler):
    raw = handler.headers.get("Cookie", "")
    for item in raw.split(";"):
        item = item.strip()
        if item.startswith("fc_session="):
            return item.split("=", 1)[1]
    return None


def get_current_user(handler):
    token = get_session_token(handler)
    if not token:
        return None

    with SESSION_LOCK:
        user_id = SESSIONS.get(token)

    if not user_id:
        return None

    with db_connect() as con:
        row = con.execute(
            "SELECT * FROM users WHERE id=?",
            (user_id,)
        ).fetchone()

    return row


def create_session(user_id):
    token = secrets.token_urlsafe(40)
    with SESSION_LOCK:
        SESSIONS[token] = user_id
    return token


def destroy_session(token):
    if token:
        with SESSION_LOCK:
            SESSIONS.pop(token, None)


def row_list(rows):
    return [dict(row) for row in rows]


# ============================================================
# HTML DOCUMENT
# ============================================================




# ============================================================
# HTTP HANDLER
# ============================================================

class FreelanceTehranHandler(BaseHTTPRequestHandler):

    server_version = "FreelanceTehran/2.0"

    def log_message(self, fmt, *args):
        print("[Freelance Tehran]", fmt % args)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length > 1_000_000:
                return {}

            raw = self.rfile.read(length)
            if not raw:
                return {}

            result = json.loads(raw.decode("utf-8"))
            return result if isinstance(result, dict) else {}

        except Exception:
            return {}

    def send_json(self, data, status=200, token=None, clear_cookie=False):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

        if token:
            self.send_header(
                "Set-Cookie",
                f"fc_session={token}; HttpOnly; SameSite=Lax; Path=/"
            )

        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                "fc_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
            )

        self.end_headers()
        self.wfile.write(body)

    def send_html(self):
        template_dir = Path(__file__).with_name("templates")
        parts = {
            "HOME": "home.html",
            "PROJECTS": "projects.html",
            "HOME_END": "home_end.html",
            "DASHBOARD": "dashboard.html",
            "FOOTER": "footer.html",
            "MODALS": "modals.html",
        }
        page = (template_dir / "index.html").read_text(encoding="utf-8")
        for key, filename in parts.items():
            fragment = (template_dir / filename).read_text(encoding="utf-8")
            page = page.replace("{{" + key + "}}", fragment)
        body = page.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path):
        root = Path(__file__).with_name("static").resolve()
        relative = path.removeprefix("/static/")
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            self.send_json({"error": "فایل پیدا نشد."}, 404)
            return
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
        }
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def require_user(self):
        user = get_current_user(self)

        if not user:
            self.send_json({
                "error":"برای انجام این عملیات ابتدا وارد حساب کاربری شو."
            }, 401)
            return None

        return user

    def get_path_parts(self):
        path = urlparse(self.path).path
        return [part for part in path.split("/") if part]

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        parts = self.get_path_parts()

        if path == "/":
            self.send_html()
            return

        if path.startswith("/static/"):
            self.send_static(path)
            return

        if path in ("/projects", "/home", "/dashboard"):
            self.send_html()
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/api/bootstrap":
            self.api_bootstrap()
            return

        if len(parts) == 3 and parts[:2] == ["api", "users"]:
            self.api_user_profile(parse_int(parts[2]))
            return

        if path == "/api/dashboard":
            self.api_dashboard()
            return

        if path == "/api/chats":
            self.api_chats()
            return

        if len(parts) == 3 and parts[0:2] == ["api", "chats"]:
            proposal_id = parse_int(parts[2])
            query = parse_qs(parsed.query)
            after = parse_int(query.get("after", ["0"])[0])
            self.api_chat_messages(proposal_id, after)
            return

        self.send_json({"error":"مسیر موردنظر پیدا نشد."}, 404)

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path
        parts = self.get_path_parts()
        data = self.read_json()

        if path == "/api/register":
            self.api_register(data)
            return

        if path == "/api/login":
            self.api_login(data)
            return

        if path == "/api/logout":
            self.api_logout()
            return

        if path == "/api/projects":
            self.api_create_project(data)
            return

        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "delete":
            self.api_delete_project(parse_int(parts[2]))
            return

        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["completion", "request"]:
            self.api_request_project_completion(parse_int(parts[2]))
            return

        if path == "/api/proposals":
            self.api_create_proposal(data)
            return

        if len(parts) == 4 and parts[:2] == ["api", "proposals"] and parts[3] == "decision":
            self.api_decide_proposal(parse_int(parts[2]), data)
            return

        if len(parts) == 5 and parts[:2] == ["api", "proposals"] and parts[3:] == ["completion", "decision"]:
            self.api_decide_project_completion(parse_int(parts[2]), data)
            return

        if len(parts) == 4 and parts[:2] == ["api", "proposals"] and parts[3] == "cancel":
            self.api_cancel_proposal(parse_int(parts[2]))
            return

        if path == "/api/profile":
            self.api_update_profile(data)
            return

        if path == "/api/ratings":
            self.api_create_rating(data)
            return

        if len(parts) == 4 and parts[:2] == ["api", "chats"] and parts[3] == "send":
            self.api_send_message(parse_int(parts[2]), data)
            return

        self.send_json({"error":"مسیر API پیدا نشد."}, 404)

    # ========================================================
    # AUTH ENDPOINTS
    # ========================================================

    def api_register(self, data):
        name = clean(data.get("name"), 100)
        email = clean(data.get("email"), 200).lower()
        password = str(data.get("password") or "")
        role = clean(data.get("role"), 30)

        if len(name) < 2:
            self.send_json({"error":"نام واردشده معتبر نیست."}, 400)
            return

        if not valid_email(email):
            self.send_json({"error":"ایمیل معتبر وارد کن."}, 400)
            return

        if len(password) < 8:
            self.send_json({"error":"رمز عبور باید حداقل ۸ کاراکتر باشد."}, 400)
            return

        if role not in ("freelancer", "employer", "both"):
            role = "both"

        try:
            with db_connect() as con:
                cursor = con.execute("""
                    INSERT INTO users(name,email,password,role)
                    VALUES(?,?,?,?)
                """, (
                    name,
                    email,
                    hash_password(password),
                    role
                ))

                user_id = cursor.lastrowid

            token = create_session(user_id)

            self.send_json({
                "success":True,
                "user":{
                    "id":user_id,
                    "name":name,
                    "email":email,
                    "role":role,
                    "bio":"",
                    "skills":""
                }
            }, token=token)

        except sqlite3.IntegrityError:
            self.send_json({"error":"این ایمیل قبلاً ثبت شده است."}, 409)

    def api_login(self, data):
        email = clean(data.get("email"), 200).lower()
        password = str(data.get("password") or "")

        with db_connect() as con:
            user = con.execute(
                "SELECT * FROM users WHERE email=?",
                (email,)
            ).fetchone()

        if not user or not verify_password(password, user["password"]):
            self.send_json({"error":"ایمیل یا رمز عبور اشتباه است."}, 401)
            return

        token = create_session(user["id"])

        self.send_json({
            "success":True,
            "user":public_user(user)
        }, token=token)

    def api_logout(self):
        token = get_session_token(self)
        destroy_session(token)
        self.send_json({"success":True}, clear_cookie=True)

    # ========================================================
    # BOOTSTRAP / DASHBOARD
    # ========================================================

    def api_bootstrap(self):
        user = get_current_user(self)

        with db_connect() as con:
            projects = con.execute("""
                SELECT
                    p.*,
                    u.name AS owner_name,
                    COALESCE(r.avg_rating, 0) AS owner_rating,
                    COALESCE(r.rating_count, 0) AS owner_rating_count,
                    CASE WHEN COALESCE(r.rating_count, 0) >= 10
                              AND COALESCE(r.avg_rating, 0) >= 9
                         THEN 1 ELSE 0 END AS owner_verified
                FROM projects p
                JOIN users u ON u.id=p.owner_id
                LEFT JOIN (
                    SELECT reviewee_id, AVG(score) AS avg_rating, COUNT(*) AS rating_count
                    FROM ratings GROUP BY reviewee_id
                ) r ON r.reviewee_id=u.id
                WHERE p.status IN ('open','in_progress')
                ORDER BY p.id DESC
            """).fetchall()

            stats = {
                "projects":con.execute(
                    "SELECT COUNT(*) FROM projects"
                ).fetchone()[0],
                "users":con.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0],
                "proposals":con.execute(
                    "SELECT COUNT(*) FROM proposals"
                ).fetchone()[0]
            }

        self.send_json({
            "user":public_user(user),
            "projects":row_list(projects),
            "stats":stats
        })

    def api_dashboard(self):
        user = self.require_user()
        if not user:
            return

        with db_connect() as con:
            projects = con.execute("""
                SELECT *
                FROM projects
                WHERE owner_id=?
                ORDER BY id DESC
            """, (user["id"],)).fetchall()

            incoming = con.execute("""
                SELECT
                    pr.*,
                    p.title AS project_title,
                    p.owner_id,
                    p.status AS project_status,
                    p.completion_status,
                    p.completion_requested_at,
                    p.completion_confirmed_at,
                    freelancer.name AS freelancer_name,
                    freelancer.email AS freelancer_email
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                JOIN users freelancer ON freelancer.id=pr.freelancer_id
                WHERE p.owner_id=?
                ORDER BY pr.id DESC
            """, (user["id"],)).fetchall()

            outgoing = con.execute("""
                SELECT
                    pr.*,
                    p.title AS project_title,
                    p.owner_id,
                    p.status AS project_status,
                    p.completion_status,
                    p.completion_requested_at,
                    p.completion_confirmed_at,
                    employer.name AS employer_name
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                JOIN users employer ON employer.id=p.owner_id
                WHERE pr.freelancer_id=?
                ORDER BY pr.id DESC
            """, (user["id"],)).fetchall()

            chats = con.execute("""
                SELECT id
                FROM proposals
                WHERE status='accepted'
                AND (
                    freelancer_id=?
                    OR project_id IN (
                        SELECT id FROM projects WHERE owner_id=?
                    )
                )
            """, (user["id"], user["id"])).fetchall()

        self.send_json({
            "user":public_user(user),
            "projects":row_list(projects),
            "incoming":row_list(incoming),
            "outgoing":row_list(outgoing),
            "chats":row_list(chats)
        })

    # ========================================================
    # PROJECT ENDPOINTS
    # ========================================================

    def api_create_project(self, data):
        user = self.require_user()
        if not user:
            return

        title = clean(data.get("title"), 150)
        category = clean(data.get("category"), 80)
        description = clean(data.get("description"), 5000)
        deadline_raw = clean(data.get("deadline"), 30)

        budget = parse_int(data.get("budget"))

        if len(title) < 3:
            self.send_json({"error":"عنوان پروژه باید حداقل ۳ کاراکتر باشد."}, 400)
            return

        if len(description) < 20:
            self.send_json({"error":"توضیحات پروژه باید حداقل ۲۰ کاراکتر باشد."}, 400)
            return

        if category not in CATEGORIES:
            self.send_json({"error":"دسته‌بندی انتخاب‌شده معتبر نیست."}, 400)
            return

        if budget < 1000:
            self.send_json({"error":"بودجه پروژه معتبر نیست."}, 400)
            return

        if not valid_jalali_date(deadline_raw):
            self.send_json({
                "error":"تاریخ شمسی را به شکل روز / ماه / سال وارد کن؛ مثلاً ۳ / ۴ / ۱۴۰۵."
            }, 400)
            return

        deadline = normalize_jalali_date(deadline_raw)

        with db_connect() as con:
            cursor = con.execute("""
                INSERT INTO projects(
                    owner_id,title,category,description,budget,deadline,status
                )
                VALUES(?,?,?,?,?,?,'open')
            """, (
                user["id"],
                title,
                category,
                description,
                budget,
                deadline
            ))

            project_id = cursor.lastrowid

        self.send_json({
            "success":True,
            "project_id":project_id,
            "message":"پروژه با موفقیت ثبت شد."
        })

    def api_delete_project(self, project_id):
        user = self.require_user()
        if not user:
            return

        with db_connect() as con:
            project = con.execute(
                "SELECT * FROM projects WHERE id=?",
                (project_id,)
            ).fetchone()

            if not project:
                self.send_json({"error":"پروژه پیدا نشد."}, 404)
                return

            if project["owner_id"] != user["id"]:
                self.send_json({
                    "error":"فقط صاحب پروژه می‌تواند آن را حذف کند."
                }, 403)
                return

            if project["status"] in ("in_progress", "closed"):
                self.send_json({
                    "error":"پروژه در حال انجام یا تکمیل‌شده است و قابل حذف نیست."
                }, 409)
                return

            con.execute(
                "DELETE FROM projects WHERE id=?",
                (project_id,)
            )

        self.send_json({
            "success":True,
            "message":"پروژه حذف شد."
        })

    # ========================================================
    # PROPOSAL ENDPOINTS
    # ========================================================

    def api_create_proposal(self, data):
        user = self.require_user()
        if not user:
            return

        project_id = parse_int(data.get("project_id"))
        amount = parse_int(data.get("amount"))
        days = parse_int(data.get("days"))
        message = clean(data.get("message"), 3000)

        if project_id < 1 or amount < 1000 or days < 1:
            self.send_json({"error":"اطلاعات پیشنهاد معتبر نیست."}, 400)
            return

        if len(message) < 10:
            self.send_json({"error":"پیام پیشنهاد باید حداقل ۱۰ کاراکتر باشد."}, 400)
            return

        with db_connect() as con:
            project = con.execute(
                "SELECT * FROM projects WHERE id=?",
                (project_id,)
            ).fetchone()

            if not project:
                self.send_json({"error":"پروژه پیدا نشد."}, 404)
                return

            if project["status"] != "open":
                self.send_json({"error":"این پروژه دیگر پیشنهاد جدید نمی‌پذیرد."}, 409)
                return

            if project["owner_id"] == user["id"]:
                self.send_json({
                    "error":"نمی‌توانی برای پروژه خودت پیشنهاد بفرستی."
                }, 400)
                return

            try:
                con.execute("""
                    INSERT INTO proposals(
                        project_id,freelancer_id,amount,days,message,status
                    )
                    VALUES(?,?,?,?,?,'pending')
                """, (
                    project_id,
                    user["id"],
                    amount,
                    days,
                    message
                ))

            except sqlite3.IntegrityError:
                self.send_json({
                    "error":"قبلاً برای این پروژه پیشنهاد فرستاده‌ای."
                }, 409)
                return

        self.send_json({
            "success":True,
            "message":"پیشنهاد برای کارفرما ارسال شد."
        })

    def api_decide_proposal(self, proposal_id, data):
        user = self.require_user()
        if not user:
            return

        decision = clean(data.get("decision"), 20)

        if decision not in ("accept", "reject"):
            self.send_json({"error":"تصمیم انتخاب‌شده معتبر نیست."}, 400)
            return

        with db_connect() as con:
            proposal = con.execute("""
                SELECT
                    pr.*,
                    p.owner_id,
                    p.status AS project_status
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                WHERE pr.id=?
            """, (proposal_id,)).fetchone()

            if not proposal:
                self.send_json({"error":"پیشنهاد پیدا نشد."}, 404)
                return

            if proposal["owner_id"] != user["id"]:
                self.send_json({
                    "error":"فقط کارفرمای صاحب پروژه می‌تواند درباره پیشنهاد تصمیم بگیرد."
                }, 403)
                return

            if proposal["status"] != "pending":
                self.send_json({
                    "error":"این پیشنهاد قبلاً بررسی شده است."
                }, 409)
                return

            if proposal["project_status"] != "open":
                self.send_json({
                    "error":"این پروژه در وضعیت قابل بررسی پیشنهاد نیست."
                }, 409)
                return

            if decision == "reject":
                con.execute("""
                    UPDATE proposals
                    SET status='rejected',updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (proposal_id,))

                self.send_json({
                    "success":True,
                    "message":"پیشنهاد رد شد."
                })
                return

            # Accept exactly one proposal for this project.
            con.execute("""
                UPDATE proposals
                SET status='rejected',updated_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND status='pending' AND id<>?
            """, (proposal["project_id"], proposal_id))

            con.execute("""
                UPDATE proposals
                SET status='accepted',updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (proposal_id,))

            con.execute("""
                UPDATE projects
                SET status='in_progress',updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (proposal["project_id"],))

        self.send_json({
            "success":True,
            "message":"پیشنهاد تأیید شد و گفتگو فعال شد."
        })

    def api_cancel_proposal(self, proposal_id):
        user = self.require_user()
        if not user:
            return

        with db_connect() as con:
            proposal = con.execute(
                "SELECT * FROM proposals WHERE id=?",
                (proposal_id,)
            ).fetchone()

            if not proposal:
                self.send_json({"error":"پیشنهاد پیدا نشد."}, 404)
                return

            if proposal["freelancer_id"] != user["id"]:
                self.send_json({
                    "error":"فقط ارسال‌کننده پیشنهاد می‌تواند آن را لغو کند."
                }, 403)
                return

            if proposal["status"] != "pending":
                self.send_json({
                    "error":"فقط پیشنهادهای در انتظار بررسی قابل لغو هستند."
                }, 409)
                return

            con.execute("""
                UPDATE proposals
                SET status='cancelled',updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (proposal_id,))

        self.send_json({
            "success":True,
            "message":"پیشنهاد لغو شد."
        })

    # ========================================================
    # TWO-PARTY PROJECT COMPLETION
    # ========================================================

    def api_request_project_completion(self, project_id):
        user = self.require_user()
        if not user:
            return

        with db_connect() as con:
            project = con.execute("""
                SELECT p.*, pr.id AS accepted_proposal_id
                FROM projects p
                LEFT JOIN proposals pr ON pr.project_id=p.id AND pr.status='accepted'
                WHERE p.id=?
            """, (project_id,)).fetchone()

            if not project:
                self.send_json({"error":"پروژه پیدا نشد."}, 404)
                return
            if project["owner_id"] != user["id"]:
                self.send_json({"error":"فقط کارفرمای صاحب پروژه می‌تواند پایان آن را اعلام کند."}, 403)
                return
            if project["status"] != "in_progress" or not project["accepted_proposal_id"]:
                self.send_json({"error":"فقط پروژه‌ای که فریلنسر آن انتخاب شده و در حال انجام است قابل اعلام پایان است."}, 409)
                return
            if project["completion_status"] == "completed":
                self.send_json({"error":"این پروژه قبلاً تکمیل و بسته شده است."}, 409)
                return
            if project["completion_status"] == "pending":
                self.send_json({"error":"درخواست پایان قبلاً ارسال شده و منتظر پاسخ فریلنسر است."}, 409)
                return

            con.execute("""
                UPDATE projects
                SET completion_status='pending',
                    completion_requested_at=CURRENT_TIMESTAMP,
                    completion_confirmed_at='',
                    completion_rejected_at='',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (project_id,))

        self.send_json({"success":True,"message":"درخواست پایان پروژه برای فریلنسر ارسال شد و منتظر تأیید اوست."})

    def api_decide_project_completion(self, proposal_id, data):
        user = self.require_user()
        if not user:
            return

        decision = clean(data.get("decision"), 20)
        if decision not in ("confirm", "reject"):
            self.send_json({"error":"تصمیم انتخاب‌شده معتبر نیست."}, 400)
            return

        with db_connect() as con:
            row = con.execute("""
                SELECT pr.id AS proposal_id, pr.freelancer_id, pr.status AS proposal_status,
                       p.id AS project_id, p.owner_id, p.status AS project_status,
                       p.completion_status
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                WHERE pr.id=?
            """, (proposal_id,)).fetchone()

            if not row:
                self.send_json({"error":"پیشنهاد یا پروژه پیدا نشد."}, 404)
                return
            if row["freelancer_id"] != user["id"]:
                self.send_json({"error":"فقط فریلنسر انتخاب‌شده می‌تواند درخواست پایان را تأیید یا رد کند."}, 403)
                return
            if row["proposal_status"] != "accepted":
                self.send_json({"error":"این پیشنهاد فعال نیست."}, 409)
                return
            if row["project_status"] != "in_progress":
                self.send_json({"error":"وضعیت پروژه اجازه ثبت پاسخ را نمی‌دهد."}, 409)
                return
            if row["completion_status"] != "pending":
                self.send_json({"error":"درخواست فعال و در انتظار تأییدی برای این پروژه وجود ندارد."}, 409)
                return

            if decision == "reject":
                con.execute("""
                    UPDATE projects
                    SET completion_status='rejected',
                        completion_rejected_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (row["project_id"],))
                message = "درخواست پایان رد شد؛ پروژه همچنان در حال انجام است."
            else:
                con.execute("""
                    UPDATE projects
                    SET completion_status='completed',
                        status='closed',
                        completion_confirmed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND completion_status='pending' AND status='in_progress'
                """, (row["project_id"],))
                message = "پایان پروژه توسط هر دو طرف تأیید شد؛ پروژه به‌صورت نهایی بسته شد."

        self.send_json({"success":True,"message":message})

    # ========================================================
    # PROFILE
    # ========================================================

    def api_update_profile(self, data):
        user = self.require_user()
        if not user:
            return

        name = clean(data.get("name"), 100)
        role = clean(data.get("role"), 30)
        bio = clean(data.get("bio"), 2000)
        skills = clean(data.get("skills"), 500)

        if len(name) < 2:
            self.send_json({"error":"نام معتبر نیست."}, 400)
            return

        if role not in ("freelancer", "employer", "both"):
            self.send_json({"error":"نوع حساب معتبر نیست."}, 400)
            return

        with db_connect() as con:
            con.execute("""
                UPDATE users
                SET name=?,role=?,bio=?,skills=?
                WHERE id=?
            """, (
                name,
                role,
                bio,
                skills,
                user["id"]
            ))

            updated = con.execute(
                "SELECT * FROM users WHERE id=?",
                (user["id"],)
            ).fetchone()

        self.send_json({
            "success":True,
            "user":public_user(updated)
        })

    # ========================================================
    # PUBLIC USER PROFILES & RATINGS
    # ========================================================

    def api_user_profile(self, user_id):
        if user_id < 1:
            self.send_json({"error":"شناسه کاربر معتبر نیست."}, 400)
            return

        viewer = get_current_user(self)
        with db_connect() as con:
            user = con.execute(
                "SELECT id,name,role,bio,skills,created_at FROM users WHERE id=?",
                (user_id,)
            ).fetchone()
            if not user:
                self.send_json({"error":"کاربر موردنظر پیدا نشد."}, 404)
                return

            summary = con.execute("""
                SELECT AVG(score) AS average_rating, COUNT(*) AS rating_count
                FROM ratings WHERE reviewee_id=?
            """, (user_id,)).fetchone()
            average = float(summary["average_rating"] or 0)
            count = int(summary["rating_count"] or 0)

            ratings = con.execute("""
                SELECT r.score,r.comment,r.created_at,
                       reviewer.id AS reviewer_id,reviewer.name AS reviewer_name,
                       p.title AS project_title
                FROM ratings r
                JOIN users reviewer ON reviewer.id=r.reviewer_id
                JOIN proposals pr ON pr.id=r.proposal_id
                JOIN projects p ON p.id=pr.project_id
                WHERE r.reviewee_id=?
                ORDER BY r.id DESC
                LIMIT 30
            """, (user_id,)).fetchall()

            eligible = []
            if viewer and viewer["id"] != user_id:
                eligible = con.execute("""
                    SELECT pr.id AS proposal_id,p.title AS project_title
                    FROM proposals pr
                    JOIN projects p ON p.id=pr.project_id
                    WHERE pr.status='accepted' AND p.status='closed'
                      AND (
                        (p.owner_id=? AND pr.freelancer_id=?)
                        OR (p.owner_id=? AND pr.freelancer_id=?)
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM ratings r
                        WHERE r.proposal_id=pr.id AND r.reviewer_id=?
                      )
                    ORDER BY pr.id DESC
                """, (user_id,viewer["id"],viewer["id"],user_id,viewer["id"])).fetchall()

        self.send_json({
            "user":dict(user),
            "average_rating":round(average,2),
            "rating_count":count,
            "is_verified":bool(count >= 10 and average >= 9),
            "ratings":row_list(ratings),
            "eligible_projects":row_list(eligible)
        })

    def api_create_rating(self, data):
        reviewer = self.require_user()
        if not reviewer:
            return

        reviewee_id = parse_int(data.get("reviewee_id"))
        proposal_id = parse_int(data.get("proposal_id"))
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        comment = clean(data.get("comment"), 500)

        if reviewee_id < 1 or proposal_id < 1 or reviewee_id == reviewer["id"]:
            self.send_json({"error":"اطلاعات امتیازدهی معتبر نیست."}, 400)
            return
        if score < 0.5 or score > 10 or abs(score * 2 - round(score * 2)) > 0.000001:
            self.send_json({"error":"امتیاز باید بین ۰٫۵ تا ۱۰ و با گام نیم‌ستاره باشد."}, 400)
            return

        with db_connect() as con:
            collaboration = con.execute("""
                SELECT pr.id,pr.status AS proposal_status,p.owner_id,p.status AS project_status,
                       pr.freelancer_id
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                WHERE pr.id=?
            """, (proposal_id,)).fetchone()

            if not collaboration or collaboration["proposal_status"] != "accepted" or collaboration["project_status"] != "closed":
                self.send_json({"error":"امتیازدهی فقط پس از تکمیل و بسته‌شدن پروژه امکان‌پذیر است."}, 403)
                return

            owner_id = collaboration["owner_id"]
            freelancer_id = collaboration["freelancer_id"]
            if reviewer["id"] == owner_id and reviewee_id == freelancer_id:
                pass
            elif reviewer["id"] == freelancer_id and reviewee_id == owner_id:
                pass
            else:
                self.send_json({"error":"فقط طرفین همان پروژه می‌توانند به یکدیگر امتیاز بدهند."}, 403)
                return

            existing = con.execute(
                "SELECT id FROM ratings WHERE proposal_id=? AND reviewer_id=?",
                (proposal_id,reviewer["id"])
            ).fetchone()
            if existing:
                self.send_json({"error":"برای این همکاری قبلاً امتیاز ثبت کرده‌ای."}, 409)
                return

            con.execute("""
                INSERT INTO ratings(proposal_id,reviewer_id,reviewee_id,score,comment)
                VALUES(?,?,?,?,?)
            """, (proposal_id,reviewer["id"],reviewee_id,score,comment))

        self.send_json({"success":True,"message":"امتیاز با موفقیت ثبت شد."})

    # ========================================================
    # CHAT
    # ========================================================

    def get_authorized_chat(self, proposal_id, user_id):
        with db_connect() as con:
            row = con.execute("""
                SELECT
                    pr.*,
                    p.title AS project_title,
                    p.owner_id,
                    p.status AS project_status,
                    employer.name AS employer_name,
                    freelancer.name AS freelancer_name,
                    freelancer.id AS freelancer_id
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                JOIN users employer ON employer.id=p.owner_id
                JOIN users freelancer ON freelancer.id=pr.freelancer_id
                WHERE pr.id=?
                AND pr.status='accepted'
            """, (proposal_id,)).fetchone()

        if not row:
            return None

        if user_id not in (row["owner_id"], row["freelancer_id"]):
            return None

        return row

    def api_chats(self):
        user = self.require_user()
        if not user:
            return

        with db_connect() as con:
            chats = con.execute("""
                SELECT
                    pr.id AS proposal_id,
                    pr.amount,
                    pr.days,
                    p.title AS project_title,
                    p.owner_id,
                    CASE
                        WHEN p.owner_id=?
                        THEN freelancer.name
                        ELSE employer.name
                    END AS other_name,
                    CASE
                        WHEN p.owner_id=?
                        THEN freelancer.id
                        ELSE employer.id
                    END AS other_user_id
                FROM proposals pr
                JOIN projects p ON p.id=pr.project_id
                JOIN users employer ON employer.id=p.owner_id
                JOIN users freelancer ON freelancer.id=pr.freelancer_id
                WHERE pr.status='accepted'
                AND (p.owner_id=? OR pr.freelancer_id=?)
                ORDER BY pr.updated_at DESC
            """, (
                user["id"],
                user["id"],
                user["id"],
                user["id"]
            )).fetchall()

        self.send_json({
            "chats":row_list(chats)
        })

    def api_chat_messages(self, proposal_id, after):
        user = self.require_user()
        if not user:
            return

        chat = self.get_authorized_chat(proposal_id, user["id"])

        if not chat:
            self.send_json({
                "error":"این گفتگو وجود ندارد یا هنوز توسط کارفرما فعال نشده است."
            }, 403)
            return

        other_name = (
            chat["freelancer_name"]
            if user["id"] == chat["owner_id"]
            else chat["employer_name"]
        )

        with db_connect() as con:
            messages = con.execute("""
                SELECT
                    m.id,
                    m.proposal_id,
                    m.sender_id,
                    m.body,
                    m.created_at
                FROM messages m
                WHERE m.proposal_id=?
                AND m.id>?
                ORDER BY m.id ASC
                LIMIT 150
            """, (proposal_id, after)).fetchall()

        self.send_json({
            "proposal_id":proposal_id,
            "project_title":chat["project_title"],
            "other_name":other_name,
            "project_closed":chat["project_status"] == "closed",
            "messages":row_list(messages)
        })

    def api_send_message(self, proposal_id, data):
        user = self.require_user()
        if not user:
            return

        body = clean(data.get("body"), 3000)

        if len(body) < 1:
            self.send_json({"error":"پیام خالی قابل ارسال نیست."}, 400)
            return

        chat = self.get_authorized_chat(proposal_id, user["id"])

        if not chat:
            self.send_json({
                "error":"ارسال پیام فقط در گفتگوی تأییدشده امکان‌پذیر است."
            }, 403)
            return
        if chat["project_status"] == "closed":
            self.send_json({"error":"این پروژه با تأیید هر دو طرف بسته شده و گفت‌وگو فقط برای مشاهده سوابق در دسترس است."}, 409)
            return

        with db_connect() as con:
            con.execute("""
                INSERT INTO messages(proposal_id,sender_id,body)
                VALUES(?,?,?)
            """, (
                proposal_id,
                user["id"],
                body
            ))

            con.execute("""
                UPDATE proposals
                SET updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (proposal_id,))

        self.send_json({"success":True})


# ============================================================
# START
# ============================================================

def main():
    initialize_database()

    server = ThreadingHTTPServer(
        (HOST, PORT),
        FreelanceTehranHandler
    )

    print()
    print("=" * 62)
    print("                 FREELANCE TEHRAN")
    print("=" * 62)
    print()
    print("Website:")
    print(f"http://{HOST}:{PORT}")
    print()
    print("Database:")
    print(str(DATABASE))
    print()
    print("Press CTRL+C to stop.")
    print("=" * 62)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFreelance Tehran stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
