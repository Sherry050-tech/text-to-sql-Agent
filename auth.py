import hashlib
import os
import re
import sqlite3
from datetime import datetime

import bcrypt

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")


def init_user_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_username(username: str) -> str:
    cleaned = (username or "").strip()
    if len(cleaned) < 3 or len(cleaned) > 32:
        raise ValueError("Username must be between 3 and 32 characters.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
        raise ValueError("Username may only contain letters, numbers, dots, underscores, or hyphens.")
    return cleaned


def _normalize_password(password: str) -> str:
    cleaned = (password or "").strip()
    if len(cleaned) < 4 or len(cleaned) > 128:
        raise ValueError("Password must be between 4 and 128 characters.")
    return cleaned


def create_user(username: str, password: str) -> bool:
    try:
        normalized_username = _normalize_username(username)
        normalized_password = _normalize_password(password)
    except ValueError:
        return False

    password_hash = bcrypt.hashpw(normalized_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    created_at = datetime.utcnow().isoformat()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (normalized_username, password_hash, created_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    try:
        normalized_username = _normalize_username(username)
        normalized_password = _normalize_password(password)
    except ValueError:
        return False

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            (normalized_username,),
        ).fetchone()
        if not row:
            return False
        stored_hash = row[0]
        return bcrypt.checkpw(normalized_password.encode("utf-8"), stored_hash.encode("utf-8"))
    finally:
        conn.close()
