# -*- coding: utf-8 -*-
"""
FREELANCE TEHRAN — Freelance Marketplace
Single-file Python + HTML + CSS + JavaScript
Run: python freelance_tehran.py
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

HTML = r"""
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<meta name="theme-color" content="#635bff">
<title>Freelance Tehran | فریلنس تهران</title>

<style>
:root {
    --primary:#635bff;
    --primary-dark:#5046e5;
    --primary-soft:#f0efff;
    --green:#16b89a;
    --green-soft:#e8faf5;
    --red:#e34d59;
    --red-soft:#fff0f1;
    --orange:#e99a35;
    --dark:#17172b;
    --text:#38384f;
    --muted:#85859b;
    --bg:#f7f7fc;
    --white:#fff;
    --border:#eaeaf2;
    --shadow:0 15px 45px rgba(31,31,78,.075);
    --radius:18px;
}

* {
    box-sizing:border-box;
    margin:0;
    padding:0;
    scrollbar-width:none;
}

*::-webkit-scrollbar {
    display:none;
    width:0;
    height:0;
}

html {
    scroll-behavior:smooth;
    scrollbar-width:none;
}

body {
    font-family:"Vazirmatn",Tahoma,Arial,sans-serif;
    background:var(--bg);
    color:var(--text);
    overflow-x:hidden;
    scrollbar-width:none;
}

button,input,textarea,select {
    font-family:inherit;
}

button {
    cursor:pointer;
}

a {
    text-decoration:none;
    color:inherit;
}

button:disabled {
    opacity:.55;
    cursor:not-allowed;
    transform:none!important;
}

.container {
    width:min(1200px,92%);
    margin:auto;
}

.hidden {
    display:none!important;
}

.muted {
    color:var(--muted);
}

.small {
    font-size:12px;
}

header {
    background:rgba(255,255,255,.95);
    border-bottom:1px solid var(--border);
    position:sticky;
    top:0;
    z-index:100;
    backdrop-filter:blur(16px);
}

.nav {
    min-height:78px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:20px;
}

.logo {
    display:flex;
    align-items:center;
    gap:10px;
    font-size:20px;
    font-weight:900;
    color:var(--dark);
    white-space:nowrap;
}

.logo-icon {
    width:43px;
    height:43px;
    border-radius:14px;
    display:grid;
    place-items:center;
    color:#fff;
    font-size:25px;
    background:linear-gradient(140deg,#847cff,#5045df);
    box-shadow:0 8px 20px #635bff45;
}

#categories, #projects, #how { scroll-margin-top: 95px; }

.nav-links {
    display:flex;
    align-items:center;
    gap:25px;
    font-size:13px;
    color:#6c6c83;
}

.nav-links a:hover {
    color:var(--primary);
}

.nav-actions,.user-actions {
    display:flex;
    align-items:center;
    gap:9px;
}

.btn {
    border:0;
    border-radius:11px;
    padding:12px 18px;
    font-size:13px;
    font-weight:800;
    transition:.2s;
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:7px;
}

.btn:hover {
    transform:translateY(-2px);
}

.btn-primary {
    color:white;
    background:var(--primary);
    box-shadow:0 7px 18px #635bff30;
}

.btn-primary:hover {
    background:var(--primary-dark);
}

.btn-light {
    color:var(--primary);
    background:var(--primary-soft);
}

.btn-outline {
    background:white;
    color:var(--text);
    border:1px solid var(--border);
}

.btn-green {
    color:white;
    background:var(--green);
}

.btn-red {
    color:var(--red);
    background:var(--red-soft);
}

.btn-dark {
    color:white;
    background:var(--dark);
}

.btn-sm {
    padding:9px 12px;
    font-size:11px;
    border-radius:9px;
}

.avatar {
    width:38px;
    height:38px;
    border-radius:50%;
    display:grid;
    place-items:center;
    background:var(--primary);
    color:white;
    font-weight:900;
    flex-shrink:0;
}

.hero {
    padding:85px 0 75px;
    background:
        radial-gradient(circle at 10% 20%,#dedcff 0,transparent 28%),
        radial-gradient(circle at 90% 80%,#d7fff4 0,transparent 25%),
        linear-gradient(135deg,#faf9ff,#f0f0ff);
}

.hero-grid {
    display:grid;
    grid-template-columns:1.15fr .85fr;
    gap:50px;
    align-items:center;
}

.hero-label {
    display:inline-flex;
    background:white;
    color:var(--primary);
    padding:9px 15px;
    border:1px solid #e8e5ff;
    border-radius:30px;
    font-size:12px;
    font-weight:800;
    margin-bottom:20px;
}

.hero h1 {
    font-size:clamp(35px,5vw,58px);
    line-height:1.55;
    color:var(--dark);
    font-weight:900;
}

.hero h1 span {
    color:var(--primary);
}

.hero p {
    font-size:15px;
    line-height:2.3;
    color:#77778c;
    margin-top:15px;
    max-width:600px;
}

.hero-buttons {
    display:flex;
    gap:12px;
    flex-wrap:wrap;
    margin-top:25px;
}

.hero-buttons .btn {
    padding:15px 22px;
}

.hero-art {
    min-height:340px;
    position:relative;
    display:grid;
    place-items:center;
}

.cube-art {
    width:190px;
    height:190px;
    display:grid;
    place-items:center;
    color:white;
    font-size:90px;
    border-radius:43px;
    transform:rotate(-12deg);
    background:linear-gradient(145deg,#827aff,#5147df);
    box-shadow:25px 30px 65px #635bff55;
}

.float-card {
    position:absolute;
    background:white;
    padding:15px 18px;
    border-radius:15px;
    box-shadow:var(--shadow);
    font-size:12px;
    font-weight:800;
}

.float-a {top:20px;right:0}
.float-b {bottom:20px;left:0}
.float-c {bottom:100px;right:0}

.section {
    padding:70px 0;
}

.section-heading {
    display:flex;
    align-items:end;
    justify-content:space-between;
    gap:20px;
    margin-bottom:28px;
}

.section-heading h2 {
    color:var(--dark);
    font-size:27px;
    font-weight:900;
}

.section-heading p {
    color:var(--muted);
    font-size:13px;
    margin-top:9px;
    line-height:1.9;
}

.categories {
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:17px;
}

.category-card {
    background:white;
    border:1px solid var(--border);
    padding:23px;
    border-radius:var(--radius);
    cursor:pointer;
    transition:.2s;
}

.category-card:hover {
    transform:translateY(-5px);
    box-shadow:var(--shadow);
    border-color:#c9c5ff;
}

.category-icon {
    width:50px;
    height:50px;
    border-radius:15px;
    display:grid;
    place-items:center;
    background:var(--primary-soft);
    font-size:24px;
    margin-bottom:15px;
}

.category-card h3 {
    font-size:15px;
    color:var(--dark);
}

.category-card p {
    font-size:11px;
    color:var(--muted);
    margin-top:8px;
    line-height:1.8;
}

.stats {
    background:var(--dark);
    color:white;
    border-radius:24px;
    padding:35px;
    display:grid;
    grid-template-columns:repeat(3,1fr);
    text-align:center;
    gap:20px;
}

.stat strong {
    display:block;
    color:#aaa5ff;
    font-size:32px;
    margin-bottom:7px;
}

.stat span {
    color:#c1c1d1;
    font-size:12px;
}

.toolbar {
    display:flex;
    gap:12px;
    margin-bottom:20px;
    flex-wrap:wrap;
}

.field {
    width:100%;
    border:1px solid var(--border);
    background:#fbfbfe;
    padding:13px 14px;
    border-radius:11px;
    outline:none;
    font-size:13px;
    color:var(--text);
    transition:.2s;
}

.field:focus {
    border-color:var(--primary);
    box-shadow:0 0 0 3px #635bff12;
}

.search-field {
    flex:1;
    min-width:220px;
    background:white;
}

.project-grid {
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:17px;
}

.project-card {
    background:white;
    border:1px solid var(--border);
    padding:23px;
    border-radius:18px;
    transition:.2s;
    min-width:0;
}

.project-card:hover {
    transform:translateY(-3px);
    box-shadow:var(--shadow);
}

.project-card h3 {
    color:var(--dark);
    font-size:17px;
    line-height:1.9;
    margin:12px 0 8px;
}

.project-card p {
    color:#77778d;
    font-size:12px;
    line-height:2.2;
    margin:12px 0 17px;
    overflow-wrap:anywhere;
}

.project-meta {
    display:flex;
    justify-content:space-between;
    gap:10px;
    flex-wrap:wrap;
    color:var(--muted);
    font-size:11px;
    margin:12px 0;
}

.project-price {
    color:var(--green);
    font-weight:900;
    font-size:14px;
}

.tag {
    display:inline-flex;
    background:var(--primary-soft);
    color:var(--primary);
    padding:6px 10px;
    border-radius:7px;
    font-size:11px;
    font-weight:800;
}

.status {
    display:inline-flex;
    align-items:center;
    gap:5px;
    border-radius:30px;
    padding:6px 10px;
    font-size:10px;
    font-weight:800;
}

.status-open {
    background:var(--green-soft);
    color:#07866e;
}

.status-pending {
    background:#fff5e7;
    color:#b8751a;
}

.status-accepted {
    background:#e7f8ef;
    color:#168451;
}

.status-rejected {
    background:var(--red-soft);
    color:#c33c4b;
}

.status-in_progress {
    background:#eeeaff;
    color:#6250c7;
}

.status-closed {
    background:#f0f0f4;
    color:#77778a;
}

.empty {
    background:white;
    padding:35px;
    border:1px dashed #d8d8e5;
    text-align:center;
    border-radius:16px;
    color:var(--muted);
    grid-column:1/-1;
    line-height:2;
}

.how-grid {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:18px;
}

.how-card {
    background:white;
    border:1px solid var(--border);
    padding:26px;
    border-radius:18px;
}

.how-number {
    width:43px;
    height:43px;
    display:grid;
    place-items:center;
    background:var(--primary-soft);
    color:var(--primary);
    border-radius:13px;
    font-weight:900;
    margin-bottom:16px;
}

.how-card h3 {
    font-size:15px;
    margin-bottom:10px;
}

.how-card p {
    font-size:12px;
    line-height:2.2;
    color:var(--muted);
}

footer {
    background:var(--dark);
    color:white;
    padding:50px 0 25px;
    margin-top:30px;
}

.footer-grid {
    display:grid;
    grid-template-columns:2fr 1fr 1fr;
    gap:35px;
}

footer p,footer a {
    color:#b5b5c7;
    font-size:12px;
    line-height:2.5;
}

footer h3 {
    margin-bottom:13px;
}

.footer-bottom {
    border-top:1px solid #343449;
    margin-top:30px;
    padding-top:20px;
    color:#9292a7;
    text-align:center;
    font-size:11px;
}

/* APP DASHBOARD */

.dashboard {
    display:none;
    min-height:80vh;
    padding:35px 0 70px;
}

.dashboard-layout {
    display:grid;
    grid-template-columns:245px 1fr;
    gap:22px;
    align-items:start;
}

.sidebar {
    background:white;
    border:1px solid var(--border);
    border-radius:18px;
    padding:15px;
    position:sticky;
    top:95px;
}

.sidebar-user {
    padding:15px 10px 20px;
    border-bottom:1px solid var(--border);
    margin-bottom:12px;
    display:flex;
    align-items:center;
    gap:10px;
}

.sidebar-user strong {
    display:block;
    font-size:13px;
}

.sidebar-user span {
    display:block;
    font-size:10px;
    color:var(--muted);
    margin-top:5px;
}

.side-link {
    width:100%;
    border:0;
    background:transparent;
    padding:13px 12px;
    border-radius:10px;
    text-align:right;
    color:#77778c;
    font-size:12px;
    font-weight:700;
    margin:3px 0;
}

.side-link:hover,.side-link.active {
    color:var(--primary);
    background:var(--primary-soft);
}

.dashboard-content {
    min-width:0;
}

.dash-title {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
    margin-bottom:22px;
}

.dash-title h2 {
    font-size:23px;
    color:var(--dark);
}

.dash-title p {
    font-size:12px;
    color:var(--muted);
    margin-top:7px;
}

.panel {
    background:white;
    border:1px solid var(--border);
    padding:22px;
    border-radius:17px;
    margin-bottom:17px;
    min-width:0;
}

.panel h3 {
    font-size:15px;
    color:var(--dark);
    margin-bottom:15px;
}

.panel-subtitle {
    color:var(--muted);
    font-size:11px;
    line-height:2;
    margin-top:-7px;
    margin-bottom:16px;
}

.dash-grid {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:14px;
    margin-bottom:18px;
}

.dash-stat {
    background:white;
    border:1px solid var(--border);
    padding:19px;
    border-radius:15px;
}

.dash-stat strong {
    display:block;
    font-size:25px;
    color:var(--primary);
    margin-bottom:7px;
}

.dash-stat span {
    font-size:11px;
    color:var(--muted);
}

.list-item {
    padding:16px 0;
    border-bottom:1px solid var(--border);
}

.list-item:last-child {
    border-bottom:0;
}

.list-top {
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:15px;
}

.list-item h4 {
    font-size:13px;
    line-height:1.9;
    color:var(--dark);
}

.list-item p {
    font-size:11px;
    line-height:2.1;
    color:var(--muted);
    margin-top:7px;
    overflow-wrap:anywhere;
}

.list-actions {
    display:flex;
    flex-wrap:wrap;
    gap:7px;
    margin-top:12px;
}

.proposal-card {
    border:1px solid var(--border);
    padding:17px;
    border-radius:14px;
    margin:12px 0;
}

.proposal-card h4 {
    font-size:13px;
    margin-bottom:8px;
}

.proposal-card p {
    font-size:11px;
    color:var(--muted);
    line-height:2;
}

.proposal-actions {
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin-top:13px;
}

.profile-form {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:14px;
}

.profile-form .full {
    grid-column:1/-1;
}

/* CHAT */

.chat-shell {
    display:grid;
    grid-template-columns:270px 1fr;
    min-height:520px;
    border:1px solid var(--border);
    border-radius:17px;
    overflow:hidden;
    background:white;
}

.chat-list {
    border-left:1px solid var(--border);
    padding:12px;
    overflow-y:auto;
    max-height:620px;
}

.chat-list h3 {
    font-size:14px;
    padding:10px 8px 15px;
}

.chat-contact {
    width:100%;
    display:block;
    text-align:right;
    padding:13px 10px;
    border:0;
    background:white;
    border-radius:12px;
    margin-bottom:5px;
}

.chat-contact:hover,.chat-contact.active {
    background:var(--primary-soft);
}

.chat-contact strong {
    display:block;
    font-size:12px;
}

.chat-contact span {
    display:block;
    font-size:10px;
    color:var(--muted);
    margin-top:6px;
}

.chat-main {
    display:flex;
    flex-direction:column;
    min-width:0;
    min-height:520px;
}

.chat-head {
    border-bottom:1px solid var(--border);
    padding:17px;
    font-size:13px;
    font-weight:800;
}

.chat-messages {
    flex:1;
    padding:18px;
    display:flex;
    flex-direction:column;
    gap:12px;
    overflow-y:auto;
    max-height:440px;
    background:#fafaff;
}

.message {
    max-width:78%;
    padding:12px 14px;
    border-radius:14px;
    font-size:12px;
    line-height:2;
    overflow-wrap:anywhere;
}

.message.mine {
    align-self:flex-start;
    background:var(--primary);
    color:white;
    border-bottom-left-radius:4px;
}

.message.theirs {
    align-self:flex-end;
    background:white;
    border:1px solid var(--border);
    border-bottom-right-radius:4px;
}

.message time {
    display:block;
    font-size:9px;
    opacity:.7;
    margin-top:5px;
}

.chat-compose {
    display:flex;
    gap:9px;
    padding:13px;
    border-top:1px solid var(--border);
}

.chat-compose input {
    flex:1;
}

.chat-placeholder {
    flex:1;
    display:grid;
    place-items:center;
    text-align:center;
    padding:25px;
    color:var(--muted);
    font-size:12px;
    line-height:2.2;
}

/* MODALS */

.modal-overlay {
    display:none;
    position:fixed;
    inset:0;
    z-index:500;
    background:#101024a8;
    backdrop-filter:blur(5px);
    align-items:center;
    justify-content:center;
    padding:15px;
}

.modal-overlay.active {
    display:flex;
}

.modal {
    width:min(540px,100%);
    max-height:90vh;
    overflow-y:auto;
    background:white;
    border-radius:21px;
    padding:26px;
    box-shadow:0 25px 80px #0003;
    animation:modalIn .2s ease;
}

@keyframes modalIn {
    from {opacity:0;transform:translateY(12px) scale(.98)}
    to {opacity:1;transform:translateY(0) scale(1)}
}

.modal-header {
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
    margin-bottom:20px;
}

.modal-header h2 {
    font-size:19px;
    color:var(--dark);
}

.close-modal {
    border:0;
    width:36px;
    height:36px;
    border-radius:10px;
    background:#f0f0f6;
    font-size:21px;
    color:#555;
}

.form-group {
    margin-bottom:14px;
}

.form-group label {
    display:block;
    font-size:12px;
    font-weight:800;
    margin-bottom:8px;
}

.form-help {
    font-size:10px;
    color:var(--muted);
    margin-top:6px;
    line-height:1.8;
}

.form-submit {
    width:100%;
    margin-top:8px;
}

.notice {
    display:none;
    padding:12px 14px;
    border-radius:10px;
    font-size:12px;
    line-height:1.9;
    margin-bottom:14px;
}

.notice.error {
    display:block;
    color:#b42318;
    background:#fff0ee;
}

.notice.success {
    display:block;
    color:#087a61;
    background:#e9fff8;
}

.toast-container {
    position:fixed;
    z-index:1000;
    left:20px;
    bottom:20px;
    display:flex;
    flex-direction:column;
    gap:9px;
    width:min(360px,calc(100% - 40px));
}

.toast {
    background:#202039;
    color:white;
    border-radius:13px;
    padding:14px 17px;
    font-size:12px;
    line-height:1.9;
    box-shadow:var(--shadow);
    animation:toastIn .2s ease;
}

.toast.error {
    background:#a72d3d;
}

.toast.success {
    background:#087d68;
}

@keyframes toastIn {
    from {opacity:0;transform:translateY(10px)}
    to {opacity:1;transform:translateY(0)}
}

.confirm-text {
    font-size:13px;
    color:var(--muted);
    line-height:2.2;
    margin-bottom:20px;
}

.confirm-actions {
    display:flex;
    gap:10px;
}

.confirm-actions button {
    flex:1;
}


/* JALALI THREE-COLUMN WHEEL PICKER */
.jalali-picker{border:1px solid var(--border);border-radius:16px;background:linear-gradient(145deg,#fbfbff,#f4f3ff);padding:12px;overflow:hidden}
.jalali-picker-head{display:grid;grid-template-columns:repeat(3,1fr);direction:ltr;text-align:center;color:var(--muted);font-size:11px;font-weight:800;padding:0 5px 9px}
.jalali-wheel-wrap{height:210px;position:relative;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;direction:ltr;overflow:hidden}
.jalali-selection-band{position:absolute;z-index:0;top:84px;right:0;left:0;height:42px;border-top:1px solid #c9c5ff;border-bottom:1px solid #c9c5ff;background:#635bff0c;pointer-events:none;border-radius:9px}
.jalali-wheel{height:210px;overflow-y:auto;overscroll-behavior:contain;scroll-snap-type:y mandatory;scrollbar-width:none;position:relative;z-index:1;text-align:center;direction:ltr;padding:84px 0}
.jalali-wheel::-webkit-scrollbar{display:none}
.jalali-wheel-item{height:42px;min-height:42px;display:flex;align-items:center;justify-content:center;scroll-snap-align:center;font-size:14px;color:#9a9aaf;cursor:pointer;user-select:none;transition:.12s;direction:rtl}
.jalali-wheel-item.selected{color:var(--primary);font-size:17px;font-weight:900}
.jalali-wheel-item.disabled{opacity:.25;pointer-events:none}
.jalali-picker-foot{text-align:center;margin-top:9px;font-size:12px;color:var(--primary);font-weight:900;min-height:20px}
.completion-box{margin-top:13px;padding:13px;border:1px solid #d9d5ff;background:#f8f7ff;border-radius:12px;font-size:11px;line-height:2.1;color:#5f5a91}
.completion-box.pending{border-color:#f2d8a9;background:#fffaf0;color:#93621d}
.completion-box.done{border-color:#bce8d8;background:#effcf6;color:#087a61}
.completion-box.rejected{border-color:#ffd0d5;background:#fff5f6;color:#a43d4c}
.completion-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.chat-closed-note{padding:12px 15px;background:#effcf6;color:#087a61;font-size:11px;line-height:2;border-top:1px solid #ccefe1}

/* RESPONSIVE */

@media(max-width:900px) {
    .hero-grid {grid-template-columns:1fr}
    .hero-art {min-height:260px}
    .nav-links {display:none}
    .categories {grid-template-columns:repeat(2,1fr)}
    .dashboard-layout {grid-template-columns:1fr}
    .sidebar {position:static}
    .sidebar-menu {display:grid;grid-template-columns:repeat(3,1fr)}
    .chat-shell {grid-template-columns:220px 1fr}
}

@media(max-width:650px) {
    .project-grid,.how-grid,.stats,.footer-grid {grid-template-columns:1fr}
    .hero {padding:55px 0}
    .section {padding:45px 0}
    .section-heading {align-items:flex-start;flex-direction:column}
    .nav {min-height:68px}
    .logo {font-size:14px}
    .logo-icon {width:37px;height:37px}
    .nav-actions .btn,.user-actions .btn {font-size:10px;padding:9px}
    .categories {gap:10px}
    .category-card {padding:16px}
    .dash-grid {grid-template-columns:1fr 1fr}
    .profile-form {grid-template-columns:1fr}
    .profile-form .full {grid-column:auto}
    .chat-shell {grid-template-columns:1fr}
    .chat-list {
        border-left:0;
        border-bottom:1px solid var(--border);
        max-height:180px;
    }
    .chat-main {min-height:440px}
    .list-top {flex-direction:column}
    .dashboard {padding-top:22px}
    .sidebar-menu {grid-template-columns:repeat(2,1fr)}
}


/* FREELANCE TEHRAN REPUTATION SYSTEM */
.profile-link {
    display:inline-flex;
    align-items:center;
    gap:6px;
    color:var(--primary);
    font-weight:900;
    cursor:pointer;
    border:0;
    background:transparent;
    font:inherit;
}
.profile-link:hover {text-decoration:underline}
.owner-line {
    display:flex;
    align-items:center;
    flex-wrap:wrap;
    gap:7px;
    margin:12px 0 5px;
    font-size:12px;
    color:var(--muted);
}
.rating-summary {
    display:inline-flex;
    align-items:center;
    gap:5px;
    color:#a87312;
    font-size:11px;
    font-weight:800;
}
.gold-verified {
    display:inline-flex;
    align-items:center;
    justify-content:center;
    width:19px;
    height:19px;
    border-radius:50%;
    color:#fff;
    background:linear-gradient(145deg,#ffe18a,#d89a19 65%,#a96b08);
    box-shadow:0 2px 7px #d69b2445;
    font-size:12px;
    vertical-align:middle;
    flex:0 0 auto;
}
.profile-hero {
    display:flex;
    align-items:center;
    gap:15px;
    padding:17px;
    border:1px solid var(--border);
    border-radius:16px;
    background:linear-gradient(135deg,#f7f6ff,#fffaf0);
    margin-bottom:17px;
}
.profile-avatar {
    width:58px;
    height:58px;
    border-radius:18px;
    display:grid;
    place-items:center;
    flex:0 0 auto;
    background:var(--primary-soft);
    color:var(--primary);
    font-size:24px;
    font-weight:900;
}
.profile-hero h3 {font-size:18px;color:var(--dark);margin-bottom:5px}
.profile-rating-number {font-size:26px;font-weight:900;color:#c18a1c}
.profile-section-title {font-size:14px;font-weight:900;color:var(--dark);margin:18px 0 10px}
.rating-stars {
    display:flex;
    flex-direction:row;
    direction:ltr;
    align-items:center;
    gap:2px;
}
.rating-star {
    border:0;
    padding:0;
    background:transparent;
    color:#d8d8e1;
    font-size:22px;
    line-height:1.3;
    cursor:pointer;
    transition:transform .12s;
}
.rating-star:hover {transform:scale(1.16)}
.rating-star.full {color:#e5a526}
.rating-star.half {
    background:linear-gradient(90deg,#e5a526 50%,#d8d8e1 50%);
    -webkit-background-clip:text;
    background-clip:text;
    color:transparent;
}
.rating-stars.interactive .rating-star {font-size:27px}
.rating-help {font-size:11px;color:var(--muted);line-height:1.9;margin-top:7px}
.rating-review {
    border:1px solid var(--border);
    border-radius:12px;
    padding:12px;
    margin-top:9px;
    background:#fff;
}
.rating-review-head {display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.rating-review p {font-size:12px;line-height:2;color:var(--text);margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere}
.rating-form-box {background:#fafaff;border:1px solid var(--border);border-radius:14px;padding:15px;margin-top:15px}
.rating-empty {padding:17px;border:1px dashed var(--border);border-radius:12px;text-align:center;color:var(--muted);font-size:12px;line-height:2}
.modal.modal-wide {width:min(680px,100%)}
@media(max-width:650px) {
    .profile-hero {align-items:flex-start}
    .rating-star {font-size:19px}
    .rating-stars.interactive .rating-star {font-size:24px}
}

/* RESPONSIVE REFINEMENT */
html { -webkit-text-size-adjust:100%; }
body { min-width:320px; }
img, video, iframe { max-width:100%; }
button, input, textarea, select { max-width:100%; }
.container { width:min(1200px, calc(100% - 40px)); }

@media (max-width:1100px) {
    .container { width:min(100% - 36px, 1100px); }
    .hero-grid { gap:28px; }
    .dashboard-layout { grid-template-columns:220px minmax(0,1fr); gap:16px; }
    .sidebar { padding:12px; }
    .chat-shell { grid-template-columns:230px minmax(0,1fr); }
}

@media (max-width:900px) {
    .container { width:calc(100% - 32px); }
    .nav { gap:12px; }
    .nav-links { display:none; }
    .hero { padding:62px 0 55px; }
    .hero-grid { grid-template-columns:minmax(0,1fr); }
    .hero-grid > div:first-child { text-align:center; }
    .hero p { margin-left:auto; margin-right:auto; }
    .hero-buttons { justify-content:center; }
    .hero-art { min-height:250px; }
    .dashboard-layout { grid-template-columns:minmax(0,1fr); }
    .sidebar { position:static; }
    .sidebar-menu { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:5px; }
    .side-link { text-align:center; padding:11px 7px; }
    .chat-shell { grid-template-columns:200px minmax(0,1fr); }
    .footer-grid { grid-template-columns:1.5fr 1fr 1fr; gap:20px; }
}

@media (max-width:650px) {
    .container { width:calc(100% - 28px); }
    .nav { min-height:64px; flex-wrap:wrap; padding:9px 0; }
    .logo { font-size:12px; gap:7px; }
    .logo-icon { width:34px; height:34px; font-size:20px; border-radius:11px; }
    .nav-actions, .user-actions { gap:6px; }
    .nav-actions .btn, .user-actions .btn { font-size:10px; padding:9px 10px; }
    .user-actions .avatar { width:32px; height:32px; }
    .hero { padding:44px 0 35px; }
    .hero h1 { font-size:clamp(29px,8vw,42px); line-height:1.65; }
    .hero p { font-size:13px; line-height:2.1; }
    .hero-buttons { display:grid; grid-template-columns:1fr; }
    .hero-buttons .btn { width:100%; }
    .hero-art { min-height:215px; }
    .cube-art { width:135px; height:135px; border-radius:30px; font-size:65px; }
    .float-card { padding:10px 12px; font-size:10px; }
    .float-a { top:10px; right:0; }
    .float-b { bottom:8px; left:0; }
    .float-c { bottom:75px; right:0; }
    .section { padding:38px 0; }
    .section-heading { align-items:stretch; flex-direction:column; gap:13px; }
    .section-heading h2 { font-size:22px; }
    .section-heading .btn { width:100%; }
    .categories { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .category-card { padding:14px; border-radius:14px; }
    .category-icon { width:42px; height:42px; font-size:20px; margin-bottom:10px; }
    .category-card h3 { font-size:13px; }
    .category-card p { font-size:10px; }
    .stats { grid-template-columns:1fr; padding:24px 15px; gap:18px; }
    .stat strong { font-size:27px; }
    .toolbar { flex-direction:column; }
    .search-field { min-width:0; width:100%; }
    .project-grid, .how-grid, .footer-grid { grid-template-columns:minmax(0,1fr); }
    .project-card { padding:17px; }
    .project-card h3 { font-size:15px; }
    .dash-title { align-items:flex-start; flex-direction:column; }
    .dash-title .btn { width:100%; }
    .dash-title h2 { font-size:20px; }
    .dash-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }
    .dash-stat { padding:14px; }
    .dash-stat strong { font-size:21px; }
    .panel { padding:16px; }
    .sidebar-menu { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .side-link { font-size:11px; }
    .profile-form { grid-template-columns:minmax(0,1fr); }
    .profile-form .full { grid-column:auto; }
    .chat-shell { grid-template-columns:minmax(0,1fr); min-height:0; }
    .chat-list { border-left:0; border-bottom:1px solid var(--border); max-height:170px; }
    .chat-main { min-height:400px; }
    .chat-messages { max-height:340px; padding:13px; }
    .message { max-width:90%; }
    .chat-compose { padding:10px; }
    .chat-compose .btn { padding:10px 12px; }
    .modal-overlay { padding:8px; align-items:flex-end; }
    .modal { width:100%; max-height:92dvh; padding:19px 15px; border-radius:19px 19px 12px 12px; }
    .modal-header h2 { font-size:17px; }
    .confirm-actions { flex-direction:column; }
    .profile-hero { padding:13px; gap:11px; }
    .profile-avatar { width:48px; height:48px; border-radius:14px; font-size:20px; }
    .profile-hero h3 { font-size:15px; }
    .modal.modal-wide { width:100%; }
    .jalali-picker { padding:8px; }
    .toast-container { left:12px; bottom:12px; width:calc(100% - 24px); }
}

@media (max-width:380px) {
    .container { width:calc(100% - 22px); }
    .logo { font-size:10px; }
    .nav-actions .btn, .user-actions .btn { padding:8px; font-size:9px; }
    .categories { grid-template-columns:1fr; }
    .dash-grid { grid-template-columns:1fr 1fr; }
    .sidebar-menu { grid-template-columns:1fr 1fr; }
    .project-meta { flex-direction:column; align-items:flex-start; }
    .list-actions .btn, .proposal-actions .btn { flex:1 1 100%; }
}

@media (prefers-reduced-motion:reduce) {
    *, *::before, *::after { scroll-behavior:auto !important; animation-duration:.01ms !important; transition-duration:.01ms !important; }
}

</style>
</head>

<body>

<header>
    <div class="container nav">
        <a class="logo" href="#" onclick="showHome();return false">
            <span class="logo-icon">⬡</span>
            <span>FREELANCE TEHRAN</span>
        </a>

        <nav class="nav-links">
            <a href="#home" onclick="navigateToSection('home');return false">خانه</a>
            <a href="#categories" onclick="navigateToSection('categories');return false">دسته‌بندی‌ها</a>
            <a href="#projects" onclick="navigateToSection('projects');return false">پروژه‌ها</a>
            <a href="#how" onclick="navigateToSection('how');return false">راهنمای همکاری</a>
        </nav>

        <div class="nav-actions" id="guestActions">
            <button class="btn btn-light" onclick="openModal('loginModal')">ورود</button>
            <button class="btn btn-primary" onclick="openModal('registerModal')">ثبت‌نام</button>
        </div>

        <div class="user-actions hidden" id="userActions">
            <div class="avatar" id="navAvatar">ف</div>
            <button class="btn btn-light" onclick="showDashboard()">پنل من</button>
            <button class="btn btn-outline" onclick="logout()">خروج</button>
        </div>
    </div>
</header>

<main id="home">

<section class="hero">
    <div class="container hero-grid">
        <div>
            <div class="hero-label">✦ دنیای تازه‌ای از همکاری حرفه‌ای</div>
            <h1>
                مهارتت را به<br>
                <span>فرصت‌های واقعی</span><br>
                تبدیل کن
            </h1>
            <p>
                در Freelance Tehran پروژه پیدا کن، پیشنهاد همکاری بفرست،
                با کارفرما ارتباط بگیر و مسیر حرفه‌ای خودت را بساز.
            </p>
            <div class="hero-buttons">
                <button class="btn btn-primary" onclick="openProjectModal()">ثبت پروژه جدید</button>
                <button class="btn btn-outline" onclick="scrollToProjects()">مشاهده پروژه‌ها</button>
            </div>
        </div>

        <div class="hero-art">
            <div class="cube-art">⬡</div>
            <div class="float-card float-a">✦ پروژه‌های متنوع</div>
            <div class="float-card float-b">✓ همکاری حرفه‌ای</div>
            <div class="float-card float-c">↗ رشد مهارت‌ها</div>
        </div>
    </div>
</section>

<section class="section" id="categories">
    <div class="container">
        <div class="section-heading">
            <div>
                <h2>دسته‌بندی خدمات</h2>
                <p>حوزه تخصصی خودت را انتخاب کن و فرصت‌های مرتبط را پیدا کن.</p>
            </div>
        </div>

        <div class="categories">
            <div class="category-card" onclick="filterCategory('طراحی گرافیک')">
                <div class="category-icon">🎨</div>
                <h3>طراحی گرافیک</h3>
                <p>لوگو، برندینگ، پوستر و رابط کاربری</p>
            </div>
            <div class="category-card" onclick="filterCategory('برنامه‌نویسی')">
                <div class="category-icon">💻</div>
                <h3>برنامه‌نویسی</h3>
                <p>وب‌سایت، اپلیکیشن و نرم‌افزار</p>
            </div>
            <div class="category-card" onclick="filterCategory('تولید محتوا')">
                <div class="category-icon">✍️</div>
                <h3>تولید محتوا</h3>
                <p>مقاله، ترجمه و کپی‌رایتینگ</p>
            </div>
            <div class="category-card" onclick="filterCategory('بازاریابی')">
                <div class="category-icon">📈</div>
                <h3>بازاریابی</h3>
                <p>سئو، شبکه‌های اجتماعی و تبلیغات</p>
            </div>
            <div class="category-card" onclick="filterCategory('ویدئو')">
                <div class="category-icon">🎬</div>
                <h3>تدوین و ویدئو</h3>
                <p>تدوین، موشن‌گرافیک و انیمیشن</p>
            </div>
            <div class="category-card" onclick="filterCategory('صدا')">
                <div class="category-icon">🎧</div>
                <h3>صدا و موسیقی</h3>
                <p>گویندگی، پادکست و آهنگ‌سازی</p>
            </div>
            <div class="category-card" onclick="filterCategory('مهندسی')">
                <div class="category-icon">📐</div>
                <h3>مهندسی</h3>
                <p>نقشه‌کشی، معماری و مدل‌سازی</p>
            </div>
            <div class="category-card" onclick="filterCategory('سایر')">
                <div class="category-icon">✨</div>
                <h3>سایر خدمات</h3>
                <p>تخصص‌های متنوع و خدمات دیگر</p>
            </div>
        </div>
    </div>
</section>

<section class="section">
    <div class="container">
        <div class="stats">
            <div class="stat">
                <strong id="statProjects">۰</strong>
                <span>پروژه‌های ثبت‌شده</span>
            </div>
            <div class="stat">
                <strong id="statUsers">۰</strong>
                <span>کاربران عضو</span>
            </div>
            <div class="stat">
                <strong id="statProposals">۰</strong>
                <span>پیشنهادهای ارسال‌شده</span>
            </div>
        </div>
    </div>
</section>

<section class="section" id="projects">
    <div class="container">
        <div class="section-heading">
            <div>
                <h2>پروژه‌های فعال</h2>
                <p>پروژه‌های ثبت‌شده توسط کاربران را بررسی کن.</p>
            </div>
            <button class="btn btn-primary" onclick="openProjectModal()">+ ثبت پروژه</button>
        </div>

        <div class="toolbar">
            <input id="projectSearch" class="field search-field"
                placeholder="جستجو در پروژه‌ها..." oninput="renderProjects()">
            <select id="categoryFilter" class="field search-field" onchange="renderProjects()">
                <option value="">همه دسته‌بندی‌ها</option>
                <option>طراحی گرافیک</option>
                <option>برنامه‌نویسی</option>
                <option>تولید محتوا</option>
                <option>بازاریابی</option>
                <option>ویدئو</option>
                <option>صدا</option>
                <option>مهندسی</option>
                <option>سایر</option>
            </select>
        </div>

        <div class="project-grid" id="projectList">
            <div class="empty">در حال دریافت پروژه‌ها...</div>
        </div>
    </div>
</section>

<section class="section" id="how">
    <div class="container">
        <div class="section-heading">
            <div>
                <h2>شروع همکاری در سه مرحله</h2>
                <p>مسیر ساده‌ای برای شروع یک همکاری حرفه‌ای.</p>
            </div>
        </div>

        <div class="how-grid">
            <div class="how-card">
                <div class="how-number">۰۱</div>
                <h3>حساب کاربری بساز</h3>
                <p>ثبت‌نام کن و اطلاعات حرفه‌ای و مهارت‌های خودت را تکمیل کن.</p>
            </div>
            <div class="how-card">
                <div class="how-number">۰۲</div>
                <h3>پروژه پیدا کن</h3>
                <p>پروژه‌های موجود را بررسی کن یا پروژه موردنیازت را ثبت کن.</p>
            </div>
            <div class="how-card">
                <div class="how-number">۰۳</div>
                <h3>پیشنهاد ارسال کن</h3>
                <p>پیشنهادت را بفرست و پس از تأیید کارفرما وارد گفت‌وگو شو.</p>
            </div>
        </div>
    </div>
</section>

</main>

<!-- DASHBOARD -->

<section class="dashboard" id="dashboard">
    <div class="container dashboard-layout">

        <aside class="sidebar">
            <div class="sidebar-user">
                <div class="avatar" id="sideAvatar">ف</div>
                <div>
                    <strong id="sideName">کاربر</strong>
                    <span id="sideEmail">ایمیل</span>
                </div>
            </div>

            <div class="sidebar-menu">
                <button class="side-link active" data-tab="overview" onclick="switchTab('overview',this)">▦ نمای کلی</button>
                <button class="side-link" data-tab="myprojects" onclick="switchTab('myprojects',this)">▤ پروژه‌های من</button>
                <button class="side-link" data-tab="incoming" onclick="switchTab('incoming',this)">↙ پیشنهادهای دریافتی</button>
                <button class="side-link" data-tab="outgoing" onclick="switchTab('outgoing',this)">↗ پیشنهادهای ارسالی</button>
                <button class="side-link" data-tab="chats" onclick="switchTab('chats',this)">✉ پیام‌ها و گفتگوها</button>
                <button class="side-link" data-tab="profile" onclick="switchTab('profile',this)">◉ پروفایل من</button>
            </div>

            <button class="btn btn-outline" style="width:100%;margin-top:15px" onclick="showHome()">
                بازگشت به سایت
            </button>
        </aside>

        <div class="dashboard-content">

            <div class="dash-title">
                <div>
                    <h2 id="dashHeading">نمای کلی</h2>
                    <p id="dashDescription">مدیریت حساب و همکاری‌های شما</p>
                </div>
            </div>

            <div id="tab-overview" class="tab-content">
                <div class="dash-grid">
                    <div class="dash-stat">
                        <strong id="myProjectCount">۰</strong>
                        <span>پروژه‌های من</span>
                    </div>
                    <div class="dash-stat">
                        <strong id="myIncomingCount">۰</strong>
                        <span>پیشنهادهای دریافتی</span>
                    </div>
                    <div class="dash-stat">
                        <strong id="myChatCount">۰</strong>
                        <span>گفتگوهای فعال</span>
                    </div>
                </div>

                <div class="panel">
                    <h3>آخرین فعالیت‌ها</h3>
                    <div id="recentActivity"></div>
                </div>
            </div>

            <div id="tab-myprojects" class="tab-content hidden">
                <div class="panel">
                    <h3>پروژه‌هایی که ثبت کرده‌ام</h3>
                    <p class="panel-subtitle">
                        می‌توانی پروژه خودت را حذف کنی. با حذف پروژه، پیشنهادهای مرتبط نیز حذف می‌شوند.
                    </p>
                    <div id="myProjectsList"></div>
                </div>
            </div>

            <div id="tab-incoming" class="tab-content hidden">
                <div class="panel">
                    <h3>پیشنهادهای دریافتی</h3>
                    <p class="panel-subtitle">
                        پیشنهادهای ارسال‌شده برای پروژه‌هایت را بررسی کن.
                        می‌توانی هر پیشنهاد را تأیید یا رد کنی.
                    </p>
                    <div id="incomingList"></div>
                </div>
            </div>

            <div id="tab-outgoing" class="tab-content hidden">
                <div class="panel">
                    <h3>پیشنهادهای ارسالی من</h3>
                    <p class="panel-subtitle">
                        وضعیت پیشنهادهایی که برای پروژه‌های دیگران ارسال کرده‌ای را ببین.
                    </p>
                    <div id="outgoingList"></div>
                </div>
            </div>

            <div id="tab-chats" class="tab-content hidden">
                <div class="panel">
                    <h3>پیام‌ها و گفتگوها</h3>
                    <p class="panel-subtitle">
                        گفتگو فقط برای پیشنهادهایی فعال می‌شود که کارفرما تأیید کرده باشد.
                    </p>

                    <div class="chat-shell">
                        <div class="chat-list">
                            <h3>گفتگوهای من</h3>
                            <div id="chatContacts">
                                <div class="empty">در حال بارگذاری...</div>
                            </div>
                        </div>

                        <div class="chat-main">
                            <div class="chat-placeholder" id="chatPlaceholder">
                                <div>
                                    <div style="font-size:35px;margin-bottom:12px">✉</div>
                                    <strong>یک گفتگو را انتخاب کن</strong>
                                    <p>بعد از تأیید پیشنهاد توسط کارفرما، امکان چت فعال می‌شود.</p>
                                </div>
                            </div>

                            <div id="chatActive" class="hidden" style="display:flex;flex-direction:column;flex:1;min-height:0">
                                <div class="chat-head" id="chatTitle">گفتگو</div>
                                <div class="chat-messages" id="chatMessages"></div>
                                <div id="chatClosedNote" class="chat-closed-note hidden">این پروژه با تأیید کارفرما و فریلنسر تکمیل شده است. تاریخچه گفتگو برای مشاهده باقی مانده، اما ارسال پیام جدید غیرفعال است.</div>
                                <form class="chat-compose" id="chatCompose" onsubmit="sendChatMessage(event)">
                                    <input id="chatInput" class="field" maxlength="3000"
                                        placeholder="پیام خود را بنویس..." autocomplete="off">
                                    <button class="btn btn-primary">ارسال</button>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="tab-profile" class="tab-content hidden">
                <div class="panel">
                    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px">
                        <h3>ویرایش پروفایل</h3>
                        <button type="button" class="btn btn-outline btn-sm" onclick="currentUser && openUserProfile(currentUser.id)">مشاهده پروفایل عمومی</button>
                    </div>
                    <form onsubmit="saveProfile(event)">
                        <div class="profile-form">
                            <div class="form-group">
                                <label>نام نمایشی</label>
                                <input class="field" id="profileName" required maxlength="100">
                            </div>
                            <div class="form-group">
                                <label>نوع حساب</label>
                                <select class="field" id="profileRole">
                                    <option value="freelancer">فریلنسر</option>
                                    <option value="employer">کارفرما</option>
                                    <option value="both">هر دو</option>
                                </select>
                            </div>
                            <div class="form-group full">
                                <label>درباره من</label>
                                <textarea class="field" id="profileBio" rows="4" maxlength="2000"></textarea>
                            </div>
                            <div class="form-group full">
                                <label>مهارت‌ها</label>
                                <input class="field" id="profileSkills" maxlength="500"
                                    placeholder="مثلاً Python، طراحی سایت، فتوشاپ">
                            </div>
                            <div class="full">
                                <button class="btn btn-primary">ذخیره تغییرات</button>
                            </div>
                        </div>
                    </form>
                </div>
            </div>

        </div>
    </div>
</section>

<footer>
    <div class="container">
        <div class="footer-grid">
            <div>
                <div class="logo" style="color:white;margin-bottom:17px">
                    <span class="logo-icon">⬡</span>
                    FREELANCE TEHRAN
                </div>
                <p>بستری برای ارتباط کارفرمایان و متخصصان و ساخت فرصت‌های تازه.</p>
            </div>
            <div>
                <h3>دسترسی سریع</h3>
                <p><a href="#home">صفحه اصلی</a></p>
                <p><a href="#projects">پروژه‌ها</a></p>
                <p><a href="#categories">دسته‌بندی‌ها</a></p>
            </div>
            <div>
                <h3>حساب کاربری</h3>
                <p><a href="#" onclick="openModal('loginModal');return false">ورود</a></p>
                <p><a href="#" onclick="openModal('registerModal');return false">ثبت‌نام</a></p>
                <p><a href="#" onclick="openProjectModal();return false">ثبت پروژه</a></p>
            </div>
        </div>
        <div class="footer-bottom">© 2026 Freelance Tehran — تمامی حقوق محفوظ است.</div>
    </div>
</footer>

<!-- LOGIN MODAL -->

<div class="modal-overlay" id="loginModal">
    <div class="modal">
        <div class="modal-header">
            <h2>ورود به حساب</h2>
            <button class="close-modal" onclick="closeModals()">×</button>
        </div>
        <div id="loginNotice" class="notice"></div>
        <form onsubmit="login(event)">
            <div class="form-group">
                <label>ایمیل</label>
                <input class="field" type="email" id="loginEmail" required>
            </div>
            <div class="form-group">
                <label>رمز عبور</label>
                <input class="field" type="password" id="loginPassword" required>
            </div>
            <button class="btn btn-primary form-submit">ورود به حساب</button>
        </form>
    </div>
</div>

<!-- REGISTER MODAL -->

<div class="modal-overlay" id="registerModal">
    <div class="modal">
        <div class="modal-header">
            <h2>ساخت حساب کاربری</h2>
            <button class="close-modal" onclick="closeModals()">×</button>
        </div>
        <div id="registerNotice" class="notice"></div>
        <form onsubmit="register(event)">
            <div class="form-group">
                <label>نام و نام خانوادگی</label>
                <input class="field" id="registerName" required minlength="2" maxlength="100">
            </div>
            <div class="form-group">
                <label>ایمیل</label>
                <input class="field" type="email" id="registerEmail" required>
            </div>
            <div class="form-group">
                <label>رمز عبور</label>
                <input class="field" type="password" id="registerPassword" required minlength="8">
            </div>
            <div class="form-group">
                <label>نوع حساب</label>
                <select class="field" id="registerRole">
                    <option value="freelancer">فریلنسر</option>
                    <option value="employer">کارفرما</option>
                    <option value="both">هر دو</option>
                </select>
            </div>
            <button class="btn btn-primary form-submit">ساخت حساب</button>
        </form>
    </div>
</div>

<!-- PROJECT MODAL -->

<div class="modal-overlay" id="projectModal">
    <div class="modal">
        <div class="modal-header">
            <h2>ثبت پروژه جدید</h2>
            <button class="close-modal" onclick="closeModals()">×</button>
        </div>
        <div id="projectNotice" class="notice"></div>
        <form onsubmit="createProject(event)">
            <div class="form-group">
                <label>عنوان پروژه</label>
                <input class="field" id="projectTitle" required maxlength="150">
            </div>
            <div class="form-group">
                <label>دسته‌بندی</label>
                <select class="field" id="projectCategory" required>
                    <option value="">انتخاب دسته‌بندی</option>
                    <option>طراحی گرافیک</option>
                    <option>برنامه‌نویسی</option>
                    <option>تولید محتوا</option>
                    <option>بازاریابی</option>
                    <option>ویدئو</option>
                    <option>صدا</option>
                    <option>مهندسی</option>
                    <option>سایر</option>
                </select>
            </div>
            <div class="form-group">
                <label>توضیحات کامل پروژه</label>
                <textarea class="field" id="projectDescription" required minlength="20"></textarea>
            </div>
            <div class="form-group">
                <label>بودجه (تومان)</label>
                <input class="field" type="number" id="projectBudget" min="1000" required>
            </div>
            <div class="form-group">
                <label>مهلت انجام پروژه (تقویم شمسی)</label>
                <input type="hidden" id="projectDeadline" value="">
                <div class="jalali-picker" id="jalaliPicker" dir="rtl">
                    <div class="jalali-picker-head"><span>ماه</span><span>روز</span><span>سال</span></div>
                    <div class="jalali-wheel-wrap">
                        <div class="jalali-selection-band"></div>
                        <div class="jalali-wheel" id="jalaliMonthWheel" aria-label="انتخاب ماه"></div>
                        <div class="jalali-wheel" id="jalaliDayWheel" aria-label="انتخاب روز"></div>
                        <div class="jalali-wheel" id="jalaliYearWheel" aria-label="انتخاب سال"></div>
                    </div>
                    <div class="jalali-picker-foot" id="jalaliDatePreview">انتخاب مهلت پروژه</div>
                </div>
                <div class="form-help">ماه‌ها به نام فارسی نمایش داده می‌شوند. سال‌های قبل از سال جاری قابل انتخاب نیستند و تعداد روزها با ماه و سال هماهنگ می‌شود.</div>
            </div>
            <button class="btn btn-primary form-submit">ثبت پروژه</button>
        </form>
    </div>
</div>

<!-- PROPOSAL MODAL -->

<div class="modal-overlay" id="proposalModal">
    <div class="modal">
        <div class="modal-header">
            <h2>ارسال پیشنهاد همکاری</h2>
            <button class="close-modal" onclick="closeModals()">×</button>
        </div>
        <div id="proposalNotice" class="notice"></div>
        <form onsubmit="sendProposal(event)">
            <input type="hidden" id="proposalProjectId">
            <div class="form-group">
                <label>مبلغ پیشنهادی (تومان)</label>
                <input class="field" type="number" id="proposalAmount" min="1000" required>
            </div>
            <div class="form-group">
                <label>مدت انجام (روز)</label>
                <input class="field" type="number" id="proposalDays" min="1" max="3650" required>
            </div>
            <div class="form-group">
                <label>پیام به کارفرما</label>
                <textarea class="field" id="proposalMessage" required minlength="10" maxlength="3000"></textarea>
            </div>
            <button class="btn btn-primary form-submit">ارسال پیشنهاد</button>
        </form>
    </div>
</div>

<!-- PUBLIC USER PROFILE / RATINGS MODAL -->
<div class="modal-overlay" id="userProfileModal">
    <div class="modal modal-wide">
        <div class="modal-header">
            <h2>پروفایل کاربر</h2>
            <button class="close-modal" onclick="closeModals()">×</button>
        </div>
        <div id="userProfileContent">
            <div class="empty">در حال بارگذاری پروفایل...</div>
        </div>
    </div>
</div>

<!-- CUSTOM CONFIRM MODAL -->

<div class="modal-overlay" id="confirmModal">
    <div class="modal" style="max-width:420px">
        <div class="modal-header">
            <h2 id="confirmTitle">تأیید عملیات</h2>
            <button class="close-modal" onclick="resolveConfirm(false)">×</button>
        </div>
        <p class="confirm-text" id="confirmText"></p>
        <div class="confirm-actions">
            <button class="btn btn-outline" onclick="resolveConfirm(false)">انصراف</button>
            <button class="btn btn-red" id="confirmYes" onclick="resolveConfirm(true)">بله، انجام بده</button>
        </div>
    </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
"use strict";

/* ==========================================================
   GLOBAL STATE
========================================================== */

let currentUser = null;
let projects = [];
let dashboardData = null;
let activeProposalId = null;
let activeChatTimer = null;
let currentTab = "overview";
let confirmResolver = null;
let chatLastMessageId = 0;
let activeRatingProfileId = null;
let activeRatingEligibleProjects = [];
let ratingDraftScore = 0;

const $ = id => document.getElementById(id);


/* ==========================================================
   API
========================================================== */

async function api(url, method="GET", data=null) {
    const options = {
        method,
        credentials:"same-origin",
        headers:{}
    };

    if (data !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);
    let result;

    try {
        result = await response.json();
    } catch {
        throw new Error("پاسخ سرور قابل خواندن نیست.");
    }

    if (!response.ok) {
        throw new Error(result.error || "خطایی رخ داده است.");
    }

    return result;
}


/* ==========================================================
   UI HELPERS
========================================================== */

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&":"&amp;",
        "<":"&lt;",
        ">":"&gt;",
        '"':"&quot;",
        "'":"&#039;"
    })[char]);
}

function numberFa(value) {
    return Number(value || 0).toLocaleString("fa-IR");
}

function toast(message, type="success") {
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = message;
    $("toastContainer").appendChild(el);

    setTimeout(() => {
        el.style.opacity = "0";
        el.style.transform = "translateY(10px)";
        setTimeout(() => el.remove(), 250);
    }, 3500);
}

function showNotice(id, message, type="error") {
    const el = $(id);
    el.textContent = message;
    el.className = "notice " + type;
}

function openModal(id) {
    closeModals();
    $(id).classList.add("active");
}

function closeModals() {
    document.querySelectorAll(".modal-overlay").forEach(el => {
        if (el.id !== "confirmModal") {
            el.classList.remove("active");
        }
    });
}

function customConfirm(title, message, yesText="بله، انجام بده") {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = message;
    $("confirmYes").textContent = yesText;
    $("confirmModal").classList.add("active");

    return new Promise(resolve => {
        confirmResolver = resolve;
    });
}

function resolveConfirm(result) {
    $("confirmModal").classList.remove("active");

    if (confirmResolver) {
        confirmResolver(result);
        confirmResolver = null;
    }
}

function statusLabel(status) {
    const labels = {
        open:"باز",
        pending:"در انتظار بررسی",
        accepted:"تأیید شده",
        rejected:"رد شده",
        cancelled:"لغو شده",
        in_progress:"در حال انجام",
        closed:"تکمیل‌شده و بسته",
        none:"اعلام نشده",
        completion_pending:"در انتظار تأیید فریلنسر",
        completion_rejected:"درخواست پایان رد شده"
    };

    return labels[status] || status;
}

function statusBadge(status) {
    return `<span class="status status-${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`;
}

function formatDate(value) {
    if (!value) return "ثبت نشده";
    return escapeHtml(value);
}

function currentJalaliParts() {
    try {
        const parts = new Intl.DateTimeFormat("en-US-u-ca-persian", {
            year:"numeric",month:"numeric",day:"numeric"
        }).formatToParts(new Date());
        const get = type => Number(parts.find(part => part.type === type)?.value);
        const result = {year:get("year"),month:get("month"),day:get("day")};
        if (result.year > 1200 && result.month >= 1 && result.month <= 12 && result.day >= 1) return result;
    } catch {}
    return {year:1405,month:1,day:1};
}
function currentJalaliYear() { return currentJalaliParts().year; }

const jalaliMonthNames = ["فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور","مهر","آبان","آذر","دی","بهمن","اسفند"];
let jalaliSelection = {day:1, month:1, year:currentJalaliYear()};
let wheelScrollTimers = {};

function jalaliLeapYear(year) {
    const breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
    let jp = breaks[0], jump = 0, jm = 0, leap = 0;
    if (year < jp || year >= breaks[breaks.length - 1]) return false;
    for (let i=1;i<breaks.length;i++) {
        jm = breaks[i]; jump = jm - jp;
        if (year < jm) break;
        jp = jm;
    }
    let n = year - jp;
    if (jump - n < 6) n = n - jump + Math.floor((jump + 4) / 33) * 33;
    leap = (((n + 1) % 33) - 1) % 4;
    if (leap === -1) leap = 4;
    return leap === 0;
}

function jalaliMonthDays(month, year) {
    if (month <= 6) return 31;
    if (month <= 11) return 30;
    return jalaliLeapYear(year) ? 30 : 29;
}

function faDigits(value) { return String(value).replace(/\d/g, d => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]); }

function wheelItems(type) {
    if (type === "month") return jalaliMonthNames.map((name,index)=>({value:index+1,label:name}));
    if (type === "year") {
        const min = currentJalaliYear();
        return Array.from({length:31},(_,i)=>({value:min+i,label:faDigits(min+i)}));
    }
    return Array.from({length:jalaliMonthDays(jalaliSelection.month,jalaliSelection.year)},(_,i)=>({value:i+1,label:faDigits(i+1)}));
}

function renderWheel(type, preserve=true) {
    const wheel = $("jalali" + type[0].toUpperCase() + type.slice(1) + "Wheel");
    if (!wheel) return;
    const previous = jalaliSelection[type];
    const items = wheelItems(type);
    wheel.innerHTML = items.map(item => `
        <div class="jalali-wheel-item ${item.value === previous ? "selected" : ""}"
             data-value="${item.value}" onclick="chooseWheelValue('${type}',${item.value})">${item.label}</div>
    `).join("");
    if (!items.some(item=>item.value===previous)) jalaliSelection[type]=items[items.length-1].value;
    const selected = wheel.querySelector(`[data-value="${jalaliSelection[type]}"]`);
    if (selected && preserve) wheel.scrollTop = selected.offsetTop - 84;
    else if (selected) wheel.scrollTop = selected.offsetTop - 84;
}

function chooseWheelValue(type, value) {
    if (type === "year" && value < currentJalaliYear()) return;
    jalaliSelection[type] = value;
    if (type === "month" || type === "year") {
        const maxDay = jalaliMonthDays(jalaliSelection.month,jalaliSelection.year);
        if (jalaliSelection.day > maxDay) jalaliSelection.day = maxDay;
        renderWheel("day", true);
    }
    renderWheel(type, true);
    updateJalaliPreview();
}

function updateJalaliPreview() {
    const {day,month,year}=jalaliSelection;
    const value = `${day} / ${month} / ${year}`;
    $("projectDeadline").value = value;
    $("jalaliDatePreview").textContent = `${faDigits(day)} ${jalaliMonthNames[month-1]} ${faDigits(year)}`;
    ["month","day","year"].forEach(type=>{
        const wheel=$("jalali"+type[0].toUpperCase()+type.slice(1)+"Wheel");
        if (!wheel) return;
        wheel.querySelectorAll(".jalali-wheel-item").forEach(item=>item.classList.toggle("selected",Number(item.dataset.value)===jalaliSelection[type]));
    });
}

function initJalaliPicker() {
    const nowYear=currentJalaliYear();
    const today=currentJalaliParts();
    jalaliSelection={day:today.day,month:today.month,year:Math.max(today.year,nowYear)};
    renderWheel("month",false); renderWheel("day",false); renderWheel("year",false);
    ["month","day","year"].forEach(type=>{
        const wheel=$("jalali"+type[0].toUpperCase()+type.slice(1)+"Wheel");
        wheel.addEventListener("scroll",()=>{
            clearTimeout(wheelScrollTimers[type]);
            wheelScrollTimers[type]=setTimeout(()=>{
                const index=Math.max(0,Math.min(wheelItems(type).length-1,Math.round(wheel.scrollTop/42)));
                const item=wheelItems(type)[index];
                if (item) chooseWheelValue(type,item.value);
            },90);
        },{passive:true});
    });
    updateJalaliPreview();
}


/* ==========================================================
   NAVIGATION
========================================================== */

function updateNavigation() {
    if (currentUser) {
        $("guestActions").classList.add("hidden");
        $("userActions").classList.remove("hidden");
        $("navAvatar").textContent = currentUser.name.charAt(0);
        $("sideAvatar").textContent = currentUser.name.charAt(0);
        $("sideName").textContent = currentUser.name;
        $("sideEmail").textContent = currentUser.email;
    } else {
        $("guestActions").classList.remove("hidden");
        $("userActions").classList.add("hidden");
    }
}

function navigateToSection(sectionId) {
    // اگر کاربر داخل پنل کاربری باشد، ابتدا صفحه اصلی را دوباره نمایش بده.
    $("dashboard").style.display = "none";
    $("home").style.display = "block";
    stopChatPolling();

    const target = $(sectionId);
    if (!target) return;

    // پس از قابل‌مشاهده شدن صفحه اصلی، به بخش انتخاب‌شده برو.
    requestAnimationFrame(() => {
        if (sectionId === "home") {
            window.scrollTo({top: 0, behavior: "smooth"});
        } else {
            target.scrollIntoView({behavior: "smooth", block: "start"});
        }
    });
}

function showHome() {
    $("dashboard").style.display = "none";
    $("home").style.display = "block";
    stopChatPolling();
    window.scrollTo({top:0,behavior:"smooth"});
}

function scrollToProjects() {
    $("projects").scrollIntoView({behavior:"smooth"});
}

function filterCategory(category) {
    $("categoryFilter").value = category;
    scrollToProjects();
    renderProjects();
}

function openProjectModal() {
    if (!currentUser) {
        toast("برای ثبت پروژه ابتدا وارد حساب خودت شو.", "error");
        openModal("loginModal");
        return;
    }
    initJalaliPicker();
    openModal("projectModal");
}


/* ==========================================================
   AUTHENTICATION
========================================================== */

async function register(event) {
    event.preventDefault();

    try {
        const result = await api("/api/register", "POST", {
            name:$("registerName").value,
            email:$("registerEmail").value,
            password:$("registerPassword").value,
            role:$("registerRole").value
        });

        currentUser = result.user;
        updateNavigation();
        closeModals();
        event.target.reset();
        await loadAll();
        toast("حساب کاربری با موفقیت ساخته شد.");
    } catch (error) {
        showNotice("registerNotice", error.message);
    }
}

async function login(event) {
    event.preventDefault();

    try {
        const result = await api("/api/login", "POST", {
            email:$("loginEmail").value,
            password:$("loginPassword").value
        });

        currentUser = result.user;
        updateNavigation();
        closeModals();
        event.target.reset();
        await loadAll();
        toast("خوش آمدی!");
    } catch (error) {
        showNotice("loginNotice", error.message);
    }
}

async function logout() {
    try {
        await api("/api/logout", "POST", {});
    } catch {}

    currentUser = null;
    dashboardData = null;
    activeProposalId = null;
    stopChatPolling();
    updateNavigation();
    showHome();
    await loadAll();
    toast("از حساب کاربری خارج شدی.");
}


/* ==========================================================
   PROJECTS
========================================================== */

function renderProjects() {
    const search = $("projectSearch").value.trim().toLowerCase();
    const category = $("categoryFilter").value;

    const filtered = projects.filter(project => {
        const text = (
            project.title + " " +
            project.description + " " +
            project.owner_name
        ).toLowerCase();

        return text.includes(search) &&
            (!category || project.category === category);
    });

    if (!filtered.length) {
        $("projectList").innerHTML =
            '<div class="empty">پروژه‌ای مطابق جستجوی شما پیدا نشد.</div>';
        return;
    }

    $("projectList").innerHTML = filtered.map(project => {
        const own = currentUser && currentUser.id === project.owner_id;

        return `
        <article class="project-card">
            <span class="tag">${escapeHtml(project.category)}</span>
            ${statusBadge(project.status)}
            <div class="owner-line">
                <span>کارفرما:</span>
                <button class="profile-link" onclick="openUserProfile(${project.owner_id})">${escapeHtml(project.owner_name)}</button>
                ${project.owner_verified ? '<span class="gold-verified" title="نشان طلایی">✓</span>' : ''}
                ${project.owner_rating_count ? `<span class="rating-summary">★ ${Number(project.owner_rating).toFixed(1)}</span>` : ''}
                <span style="margin-right:auto">${escapeHtml(project.created_at)}</span>
            </div>

            <h3>${escapeHtml(project.title)}</h3>
            <p>${escapeHtml(project.description)}</p>

            <div class="project-meta">
                <span class="project-price">${numberFa(project.budget)} تومان</span>
                <span>مهلت: ${formatDate(project.deadline || "توافقی")}</span>
            </div>

            ${
                own
                ? `<button class="btn btn-light" onclick="showDashboardAndTab('myprojects')">مدیریت پروژه</button>`
                : project.status === "open"
                    ? `<button class="btn btn-primary" onclick="openProposal(${project.id})">ارسال پیشنهاد</button>`
                    : `<button class="btn btn-outline" disabled>پروژه در حال انجام</button>`
            }
        </article>
        `;
    }).join("");
}

async function createProject(event) {
    event.preventDefault();

    try {
        const result = await api("/api/projects", "POST", {
            title:$("projectTitle").value,
            category:$("projectCategory").value,
            description:$("projectDescription").value,
            budget:$("projectBudget").value,
            deadline:$("projectDeadline").value
        });

        closeModals();
        event.target.reset();
        await loadAll();
        toast(result.message || "پروژه با موفقیت ثبت شد.");
    } catch (error) {
        showNotice("projectNotice", error.message);
    }
}

async function deleteProject(projectId) {
    const confirmed = await customConfirm(
        "حذف پروژه",
        "آیا مطمئنی می‌خواهی این پروژه را حذف کنی؟ تمام پیشنهادهای مرتبط با این پروژه نیز حذف می‌شوند.",
        "حذف پروژه"
    );

    if (!confirmed) return;

    try {
        await api(`/api/projects/${projectId}/delete`, "POST", {});
        await loadAll();
        await loadDashboard();
        toast("پروژه و پیشنهادهای مرتبط حذف شدند.");
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   PROPOSALS
========================================================== */

function openProposal(projectId) {
    if (!currentUser) {
        toast("برای ارسال پیشنهاد ابتدا وارد حساب کاربری شو.", "error");
        openModal("loginModal");
        return;
    }

    $("proposalProjectId").value = projectId;
    openModal("proposalModal");
}

async function sendProposal(event) {
    event.preventDefault();

    try {
        await api("/api/proposals", "POST", {
            project_id:$("proposalProjectId").value,
            amount:$("proposalAmount").value,
            days:$("proposalDays").value,
            message:$("proposalMessage").value
        });

        closeModals();
        event.target.reset();
        await loadAll();
        await loadDashboard();
        toast("پیشنهادت با موفقیت برای کارفرما ارسال شد.");
    } catch (error) {
        showNotice("proposalNotice", error.message);
    }
}

async function decideProposal(proposalId, decision) {
    const isAccept = decision === "accept";

    const confirmed = await customConfirm(
        isAccept ? "تأیید پیشنهاد" : "رد پیشنهاد",
        isAccept
            ? "با تأیید این پیشنهاد، فریلنسر انتخاب می‌شود و گفتگوی خصوصی بین شما فعال خواهد شد. پیشنهادهای دیگر این پروژه رد خواهند شد."
            : "آیا از رد کردن این پیشنهاد مطمئنی؟ بعد از رد شدن، این پیشنهاد دیگر قابل تأیید نیست.",
        isAccept ? "تأیید و شروع گفتگو" : "رد پیشنهاد"
    );

    if (!confirmed) return;

    try {
        const result = await api(
            `/api/proposals/${proposalId}/decision`,
            "POST",
            {decision}
        );

        await loadAll();
        await loadDashboard();

        if (isAccept) {
            toast("پیشنهاد تأیید شد. گفتگوی خصوصی فعال شد.");
            switchTab("chats");
            await loadChats();
        } else {
            toast(result.message || "پیشنهاد رد شد.");
        }
    } catch (error) {
        toast(error.message, "error");
    }
}

async function cancelProposal(proposalId) {
    const confirmed = await customConfirm(
        "لغو پیشنهاد",
        "آیا می‌خواهی پیشنهاد ارسال‌شده خودت را لغو کنی؟",
        "لغو پیشنهاد"
    );

    if (!confirmed) return;

    try {
        await api(`/api/proposals/${proposalId}/cancel`, "POST", {});
        await loadAll();
        await loadDashboard();
        toast("پیشنهاد لغو شد.");
    } catch (error) {
        toast(error.message, "error");
    }
}



async function requestProjectCompletion(projectId) {
    const confirmed = await customConfirm(
        "اعلام پایان پروژه",
        "با این کار درخواست تأیید پایان برای فریلنسر ارسال می‌شود. پروژه فقط پس از تأیید فریلنسر بسته خواهد شد.",
        "ارسال درخواست پایان"
    );
    if (!confirmed) return;
    try {
        const result=await api(`/api/projects/${projectId}/completion/request`,"POST",{});
        await loadAll(); await loadDashboard();
        toast(result.message || "درخواست پایان برای فریلنسر ارسال شد.");
    } catch(error) { toast(error.message,"error"); }
}

async function decideProjectCompletion(proposalId, decision) {
    const accept=decision==="confirm";
    const confirmed=await customConfirm(
        accept ? "تأیید پایان پروژه" : "رد درخواست پایان",
        accept
            ? "آیا تأیید می‌کنی که پروژه کامل تحویل شده است؟ با تأیید تو، پروژه به‌صورت نهایی بسته می‌شود."
            : "با رد درخواست، پروژه باز و در وضعیت انجام باقی می‌ماند و کارفرما می‌تواند بعداً دوباره درخواست بدهد.",
        accept ? "بله، پروژه تمام شده" : "رد درخواست"
    );
    if (!confirmed) return;
    try {
        const result=await api(`/api/proposals/${proposalId}/completion/decision`,"POST",{decision});
        await loadAll(); await loadDashboard();
        toast(result.message || "وضعیت پایان پروژه ثبت شد.");
    } catch(error) { toast(error.message,"error"); }
}

function completionPanel(project, side, proposalId=null) {
    const state=project.completion_status || "none";
    if (state==="completed" || project.status==="closed") {
        return `<div class="completion-box done">✓ پروژه با تأیید هر دو طرف تکمیل و بسته شده است.</div>`;
    }
    if (state==="pending") {
        if (side==="freelancer") {
            return `<div class="completion-box pending">کارفرما پایان پروژه را اعلام کرده است. لطفاً تحویل کامل پروژه را بررسی و نتیجه را ثبت کن.
                <div class="completion-actions">
                    <button class="btn btn-green btn-sm" onclick="decideProjectCompletion(${proposalId},'confirm')">✓ تأیید پایان پروژه</button>
                    <button class="btn btn-red btn-sm" onclick="decideProjectCompletion(${proposalId},'reject')">✕ پروژه هنوز تمام نشده</button>
                </div></div>`;
        }
        return `<div class="completion-box pending">درخواست پایان پروژه ارسال شده و منتظر تأیید فریلنسر است.</div>`;
    }
    if (state==="rejected") {
        if (side==="employer") return `<div class="completion-box rejected">فریلنسر درخواست پایان قبلی را تأیید نکرده است. پس از تکمیل موارد باقی‌مانده می‌توانی دوباره درخواست بدهی.
            <div class="completion-actions"><button class="btn btn-primary btn-sm" onclick="requestProjectCompletion(${project.id})">ارسال مجدد درخواست پایان</button></div></div>`;
        return `<div class="completion-box rejected">درخواست قبلی پایان پروژه رد شده است؛ پروژه همچنان در حال انجام است.</div>`;
    }
    if (side==="employer" && project.status==="in_progress") {
        return `<div class="completion-box">اگر کار تحویل شده و از اتمام آن مطمئنی، درخواست رسمی پایان را برای فریلنسر ارسال کن.
            <div class="completion-actions"><button class="btn btn-green btn-sm" onclick="requestProjectCompletion(${project.id})">✓ اعلام پایان پروژه</button></div></div>`;
    }
    return "";
}

/* ==========================================================
   DASHBOARD
========================================================== */

function showDashboardAndTab(tab) {
    showDashboard();
    switchTab(tab);
}

async function showDashboard() {
    if (!currentUser) {
        openModal("loginModal");
        return;
    }

    $("home").style.display = "none";
    $("dashboard").style.display = "block";
    window.scrollTo({top:0,behavior:"smooth"});

    await loadDashboard();
    switchTab(currentTab || "overview");
}

function switchTab(tab, button=null) {
    currentTab = tab;

    document.querySelectorAll(".tab-content").forEach(el => {
        el.classList.add("hidden");
    });

    const target = $("tab-" + tab);
    if (target) target.classList.remove("hidden");

    document.querySelectorAll(".side-link").forEach(el => {
        el.classList.toggle("active", el.dataset.tab === tab);
    });

    const titles = {
        overview:["نمای کلی","خلاصه فعالیت‌های حساب کاربری شما"],
        myprojects:["پروژه‌های من","مدیریت، بررسی و حذف پروژه‌های ثبت‌شده"],
        incoming:["پیشنهادهای دریافتی","بررسی پیشنهادهای فریلنسرها"],
        outgoing:["پیشنهادهای ارسالی","پیگیری وضعیت پیشنهادهای ارسال‌شده"],
        chats:["پیام‌ها و گفتگوها","ارتباط مستقیم با طرف قرارداد"],
        profile:["پروفایل من","اطلاعات و مشخصات حساب کاربری"]
    };

    const info = titles[tab] || titles.overview;
    $("dashHeading").textContent = info[0];
    $("dashDescription").textContent = info[1];

    if (tab === "chats") {
        loadChats();
    } else {
        stopChatPolling();
    }

    if (tab === "profile") {
        fillProfile();
    }
}

async function loadDashboard() {
    if (!currentUser) return;

    try {
        dashboardData = await api("/api/dashboard");
        renderDashboard();
    } catch (error) {
        toast(error.message, "error");
    }
}

function renderDashboard() {
    if (!dashboardData) return;

    const data = dashboardData;

    $("myProjectCount").textContent = numberFa(data.projects.length);
    $("myIncomingCount").textContent = numberFa(data.incoming.length);
    $("myChatCount").textContent = numberFa(data.chats.length);

    renderMyProjects(data.projects);
    renderIncoming(data.incoming);
    renderOutgoing(data.outgoing);
    renderRecent(data);
}

function renderRecent(data) {
    const items = [];

    data.projects.slice(0,3).forEach(project => {
        items.push(`
            <div class="list-item">
                <h4>پروژه ثبت کردی: ${escapeHtml(project.title)}</h4>
                <p>وضعیت: ${escapeHtml(statusLabel(project.status))}</p>
            </div>
        `);
    });

    data.incoming.slice(0,3).forEach(proposal => {
        items.push(`
            <div class="list-item">
                <h4>پیشنهاد جدید از ${escapeHtml(proposal.freelancer_name)}</h4>
                <p>برای پروژه ${escapeHtml(proposal.project_title)}</p>
            </div>
        `);
    });

    $("recentActivity").innerHTML = items.length
        ? items.join("")
        : '<div class="empty">هنوز فعالیتی در حساب شما ثبت نشده است.</div>';
}

function renderMyProjects(items) {
    if (!items.length) {
        $("myProjectsList").innerHTML =
            '<div class="empty">هنوز پروژه‌ای ثبت نکرده‌ای.</div>';
        return;
    }

    $("myProjectsList").innerHTML = items.map(project => `
        <div class="list-item">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(project.title)}</h4>
                    <p>
                        ${escapeHtml(project.category)}
                        · بودجه ${numberFa(project.budget)} تومان
                        · مهلت ${formatDate(project.deadline || "توافقی")}
                    </p>
                </div>
                <div>${statusBadge(project.status)}</div>
            </div>

            ${completionPanel(project,"employer")}
            <div class="list-actions">
                <button class="btn btn-light btn-sm"
                    onclick="showDashboardAndTab('incoming')">
                    مشاهده پیشنهادها
                </button>
                ${
                    project.status !== "in_progress" && project.status !== "closed"
                    ? `<button class="btn btn-red btn-sm" onclick="deleteProject(${project.id})">حذف پروژه</button>`
                    : `<span class="small muted">${project.status === "closed" ? "پروژه نهایی بسته شده است." : "پروژه در حال انجام است؛ حذف آن غیرفعال است."}</span>`
                }
            </div>
        </div>
    `).join("");
}

function renderIncoming(items) {
    if (!items.length) {
        $("incomingList").innerHTML =
            '<div class="empty">هنوز پیشنهادی برای پروژه‌هایت دریافت نکرده‌ای.</div>';
        return;
    }

    $("incomingList").innerHTML = items.map(proposal => `
        <div class="proposal-card">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(proposal.project_title)}</h4>
                    <p>
                        فریلنسر: <strong>${escapeHtml(proposal.freelancer_name)}</strong>
                        · ${escapeHtml(proposal.freelancer_email)}
                    </p>
                </div>
                ${statusBadge(proposal.status)}
            </div>

            <p style="margin-top:12px">
                مبلغ پیشنهادی:
                <strong style="color:var(--green)">
                    ${numberFa(proposal.amount)} تومان
                </strong>
                · زمان انجام: ${numberFa(proposal.days)} روز
            </p>

            <p style="margin-top:8px">${escapeHtml(proposal.message)}</p>
            ${proposal.status === "accepted" ? completionPanel(proposal,"employer") : ""}

            <div class="proposal-actions">
                ${
                    proposal.status === "pending"
                    ? `
                        <button class="btn btn-green btn-sm"
                            onclick="decideProposal(${proposal.id},'accept')">
                            ✓ تأیید و شروع گفتگو
                        </button>
                        <button class="btn btn-red btn-sm"
                            onclick="decideProposal(${proposal.id},'reject')">
                            ✕ رد پیشنهاد
                        </button>
                    `
                    : proposal.status === "accepted"
                        ? `
                            <button class="btn btn-primary btn-sm"
                                onclick="openChatFromProposal(${proposal.id})">
                                ✉ ورود به گفتگو
                            </button>
                        `
                        : `<span class="small muted">این پیشنهاد ${escapeHtml(statusLabel(proposal.status))} است.</span>`
                }
            </div>
        </div>
    `).join("");
}

function renderOutgoing(items) {
    if (!items.length) {
        $("outgoingList").innerHTML =
            '<div class="empty">هنوز برای هیچ پروژه‌ای پیشنهاد ارسال نکرده‌ای.</div>';
        return;
    }

    $("outgoingList").innerHTML = items.map(proposal => `
        <div class="proposal-card">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(proposal.project_title)}</h4>
                    <p>کارفرما: ${escapeHtml(proposal.employer_name)}</p>
                </div>
                ${statusBadge(proposal.status)}
            </div>

            <p style="margin-top:10px">
                مبلغ: <strong>${numberFa(proposal.amount)} تومان</strong>
                · زمان: ${numberFa(proposal.days)} روز
            </p>

            <p>${escapeHtml(proposal.message)}</p>
            ${proposal.status === "accepted" ? completionPanel(proposal,"freelancer",proposal.id) : ""}

            <div class="proposal-actions">
                ${
                    proposal.status === "pending"
                    ? `<button class="btn btn-red btn-sm" onclick="cancelProposal(${proposal.id})">لغو پیشنهاد</button>`
                    : proposal.status === "accepted"
                        ? `<button class="btn btn-primary btn-sm" onclick="openChatFromProposal(${proposal.id})">ورود به گفتگو</button>`
                        : `<span class="small muted">وضعیت: ${escapeHtml(statusLabel(proposal.status))}</span>`
                }
            </div>
        </div>
    `).join("");
}


/* ==========================================================
   PROFILE
========================================================== */

function fillProfile() {
    if (!currentUser) return;

    $("profileName").value = currentUser.name || "";
    $("profileRole").value = currentUser.role || "both";
    $("profileBio").value = currentUser.bio || "";
    $("profileSkills").value = currentUser.skills || "";
}

async function saveProfile(event) {
    event.preventDefault();

    try {
        const result = await api("/api/profile", "POST", {
            name:$("profileName").value,
            role:$("profileRole").value,
            bio:$("profileBio").value,
            skills:$("profileSkills").value
        });

        currentUser = result.user;
        updateNavigation();
        await loadDashboard();
        toast("پروفایل با موفقیت به‌روزرسانی شد.");
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   PUBLIC PROFILES & 10-STAR RATINGS
========================================================== */

function verifiedBadge(isVerified) {
    return isVerified ? '<span class="gold-verified" title="نشان طلایی Freelance Tehran">✓</span>' : '';
}

function ratingText(score, count) {
    if (!count) return '';
    return `<span class="rating-summary">★ ${Number(score).toFixed(1)}</span>`;
}

function renderStars(score, interactive=false) {
    const value = Math.max(0, Math.min(10, Number(score || 0)));
    let html = '<div class="rating-stars' + (interactive ? ' interactive' : '') + '">';
    for (let i=1;i<=10;i++) {
        const state = value >= i ? 'full' : (value >= i-0.5 ? 'half' : '');
        html += interactive
            ? `<button type="button" class="rating-star ${state}" aria-label="${i} ستاره" onclick="changeRatingStar(${i})">★</button>`
            : `<span class="rating-star ${state}" aria-hidden="true">★</span>`;
    }
    return html + '</div>';
}

function changeRatingStar(index) {
    const halfValue = index - 0.5;
    if (ratingDraftScore === halfValue) {
        ratingDraftScore = index;
    } else if (ratingDraftScore === index) {
        ratingDraftScore = index - 1;
    } else {
        ratingDraftScore = halfValue;
    }
    const stars = $('ratingInputStars');
    if (stars) stars.innerHTML = renderStars(ratingDraftScore, true);
    const value = $('ratingDraftValue');
    if (value) value.textContent = ratingDraftScore.toFixed(1) + ' از ۱۰';
}

async function openUserProfile(userId) {
    $('userProfileContent').innerHTML = '<div class="empty">در حال بارگذاری پروفایل...</div>';
    openModal('userProfileModal');
    activeRatingProfileId = Number(userId);
    ratingDraftScore = 0;

    try {
        const data = await api(`/api/users/${Number(userId)}`);
        activeRatingEligibleProjects = data.eligible_projects || [];
        const user = data.user;
        const isOwn = currentUser && Number(currentUser.id) === Number(user.id);
        const average = Number(data.average_rating || 0);
        const count = Number(data.rating_count || 0);
        const roleLabels = {freelancer:'فریلنسر',employer:'کارفرما',both:'فریلنسر و کارفرما'};
        const reviews = (data.ratings || []).map(item => `
            <div class="rating-review">
                <div class="rating-review-head">
                    <button class="profile-link" onclick="openUserProfile(${item.reviewer_id})">${escapeHtml(item.reviewer_name)}</button>
                    <span class="rating-summary">★ ${Number(item.score).toFixed(1)}</span>
                </div>
                <div class="small muted">${escapeHtml(item.created_at || '')} · ${escapeHtml(item.project_title || 'همکاری تکمیل‌شده')}</div>
                ${item.comment ? `<p>${escapeHtml(item.comment)}</p>` : ''}
            </div>
        `).join('');

        let rateBox = '';
        if (!isOwn && currentUser && activeRatingEligibleProjects.length) {
            const options = activeRatingEligibleProjects.map(project =>
                `<option value="${project.proposal_id}">${escapeHtml(project.project_title)}</option>`
            ).join('');
            rateBox = `
                <div class="rating-form-box">
                    <h3 class="profile-section-title" style="margin-top:0">ثبت امتیاز برای این کاربر</h3>
                    <label class="small">پروژه‌ای که با هم انجام داده‌اید</label>
                    <select class="field" id="ratingProjectId">${options}</select>
                    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:13px">
                        <div id="ratingInputStars">${renderStars(0,true)}</div>
                        <strong id="ratingDraftValue" style="color:#b77c10">0.0 از ۱۰</strong>
                    </div>
                    <div class="rating-help">روی هر ستاره بار اول نیم‌ستاره، بار دوم ستاره کامل و بار سوم خالی می‌شود. امتیاز از ۰٫۵ تا ۱۰ قابل انتخاب است.</div>
                    <textarea class="field" id="ratingComment" rows="3" maxlength="500" style="margin-top:12px" placeholder="نظر شما درباره تجربه همکاری (اختیاری)"></textarea>
                    <button class="btn btn-primary" style="margin-top:10px" onclick="submitUserRating()">ثبت امتیاز</button>
                </div>`;
        } else if (!isOwn && currentUser) {
            rateBox = '<div class="rating-help" style="margin-top:14px">برای امتیاز دادن باید یک پروژه مشترک با این کاربر به‌صورت کامل بسته شده باشد و هنوز برای آن همکاری امتیاز ثبت نکرده باشی.</div>';
        } else if (!isOwn && !currentUser) {
            rateBox = '<div class="rating-help" style="margin-top:14px">برای امتیاز دادن ابتدا وارد حساب کاربری شو.</div>';
        }

        $('userProfileContent').innerHTML = `
            <div class="profile-hero">
                <div class="profile-avatar">${escapeHtml((user.name || '?').charAt(0))}</div>
                <div style="min-width:0;flex:1">
                    <h3>${escapeHtml(user.name)} ${verifiedBadge(data.is_verified)}</h3>
                    <div class="small muted">${escapeHtml(roleLabels[user.role] || 'کاربر')} · عضویت از ${escapeHtml((user.created_at || '').slice(0,10))}</div>
                    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:7px">
                        <span class="profile-rating-number">${count ? average.toFixed(1) : '—'}</span>
                        ${ratingText(average,count)}
                    </div>
                </div>
            </div>
            <h3 class="profile-section-title">درباره من</h3>
            <p style="font-size:13px;line-height:2.1;white-space:pre-wrap">${escapeHtml(user.bio || 'هنوز توضیحی ثبت نشده است.')}</p>
            <h3 class="profile-section-title">مهارت‌ها</h3>
            <p style="font-size:13px;line-height:2">${escapeHtml(user.skills || 'هنوز مهارتی ثبت نشده است.')}</p>
            ${rateBox}
            <h3 class="profile-section-title">نظرات و امتیازها (${numberFa(count)})</h3>
            ${reviews || '<div class="rating-empty">هنوز نظری برای این کاربر ثبت نشده است.</div>'}
        `;
    } catch (error) {
        $('userProfileContent').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
}

async function submitUserRating() {
    if (!currentUser) {
        toast('برای ثبت امتیاز ابتدا وارد حساب کاربری شو.','error');
        return;
    }
    if (!activeRatingProfileId || !ratingDraftScore || ratingDraftScore < 0.5) {
        toast('ابتدا امتیاز خودت را با ستاره‌ها انتخاب کن.','error');
        return;
    }
    const proposalId = Number($('ratingProjectId')?.value || 0);
    try {
        await api('/api/ratings','POST',{
            reviewee_id:activeRatingProfileId,
            proposal_id:proposalId,
            score:ratingDraftScore,
            comment:$('ratingComment')?.value || ''
        });
        toast('امتیاز شما با موفقیت ثبت شد.');
        await loadAll();
        await openUserProfile(activeRatingProfileId);
    } catch (error) {
        toast(error.message,'error');
    }
}


/* ==========================================================
   CHAT SYSTEM
========================================================== */

function stopChatPolling() {
    if (activeChatTimer) {
        clearInterval(activeChatTimer);
        activeChatTimer = null;
    }
}

async function loadChats() {
    if (!currentUser) return;

    try {
        const result = await api("/api/chats");

        if (!result.chats.length) {
            $("chatContacts").innerHTML =
                '<div class="empty">هنوز گفتگوی فعالی نداری. گفتگو پس از تأیید پیشنهاد ایجاد می‌شود.</div>';
            return;
        }

        $("chatContacts").innerHTML = result.chats.map(chat => `
            <button class="chat-contact ${activeProposalId === chat.proposal_id ? "active" : ""}"
                onclick="selectChat(${chat.proposal_id})">
                <strong>${escapeHtml(chat.other_name)}</strong>
                <span>${escapeHtml(chat.project_title)}</span>
                <span>${numberFa(chat.amount)} تومان</span>
            </button>
        `).join("");

        if (activeProposalId) {
            const stillExists = result.chats.some(
                chat => chat.proposal_id === activeProposalId
            );

            if (!stillExists) {
                activeProposalId = null;
                $("chatActive").classList.add("hidden");
                $("chatPlaceholder").classList.remove("hidden");
            }
        }
    } catch (error) {
        toast(error.message, "error");
    }
}

async function openChatFromProposal(proposalId) {
    showDashboard();
    switchTab("chats");
    await loadChats();
    await selectChat(proposalId);
}

async function selectChat(proposalId) {
    activeProposalId = proposalId;
    chatLastMessageId = 0;

    $("chatPlaceholder").classList.add("hidden");
    $("chatActive").classList.remove("hidden");
    $("chatMessages").innerHTML = '<div class="chat-placeholder">در حال بارگذاری گفتگو...</div>';

    await loadChatMessages(true);
    await loadChats();

    stopChatPolling();
    activeChatTimer = setInterval(() => {
        loadChatMessages(false);
    }, 2200);
}

async function loadChatMessages(reset=false) {
    if (!activeProposalId) return;

    try {
        const result = await api(
            `/api/chats/${activeProposalId}?after=${chatLastMessageId}`
        );

        $("chatTitle").textContent =
            result.project_title + " — " + result.other_name;
        $("chatCompose").classList.toggle("hidden", !!result.project_closed);
        $("chatClosedNote").classList.toggle("hidden", !result.project_closed);

        if (reset) {
            $("chatMessages").innerHTML = "";
            chatLastMessageId = 0;
        }

        if (result.messages.length) {
            const box = $("chatMessages");

            result.messages.forEach(message => {
                if (message.id <= chatLastMessageId) return;

                const item = document.createElement("div");
                item.className = "message " +
                    (message.sender_id === currentUser.id ? "mine" : "theirs");

                const body = document.createElement("div");
                body.textContent = message.body;

                const time = document.createElement("time");
                time.textContent = message.created_at;

                item.appendChild(body);
                item.appendChild(time);
                box.appendChild(item);

                chatLastMessageId = Math.max(chatLastMessageId, message.id);
            });

            box.scrollTop = box.scrollHeight;
        }
    } catch (error) {
        stopChatPolling();
        toast(error.message, "error");
    }
}

async function sendChatMessage(event) {
    event.preventDefault();

    const body = $("chatInput").value.trim();

    if (!body || !activeProposalId) return;

    try {
        await api(`/api/chats/${activeProposalId}/send`, "POST", {body});
        $("chatInput").value = "";
        await loadChatMessages(false);
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   INITIAL LOAD
========================================================== */

async function loadAll() {
    try {
        const data = await api("/api/bootstrap");

        currentUser = data.user;
        projects = data.projects;

        updateNavigation();
        renderProjects();

        $("statProjects").textContent = numberFa(data.stats.projects);
        $("statUsers").textContent = numberFa(data.stats.users);
        $("statProposals").textContent = numberFa(data.stats.proposals);

        if (currentUser && $("dashboard").style.display === "block") {
            await loadDashboard();
        }
    } catch (error) {
        console.error(error);
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    initJalaliPicker();
    await loadAll();

    document.querySelectorAll(".modal-overlay").forEach(modal => {
        modal.addEventListener("click", event => {
            if (event.target === modal && modal.id !== "confirmModal") {
                closeModals();
            }
        });
    });

    $("confirmModal").addEventListener("click", event => {
        if (event.target === $("confirmModal")) {
            resolveConfirm(false);
        }
    });
});
</script>

</body>
</html>
"""


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
        body = HTML.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
