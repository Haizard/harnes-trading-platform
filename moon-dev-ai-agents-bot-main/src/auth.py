"""
Moon Dev Authentication System
Signup, login, JWT tokens, password hashing.

DSH Pattern: EventBus → DB → Singleton
"""

import os
import time
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from termcolor import cprint

# Password hashing (using hashlib since bcrypt may not be installed)
def hash_password(password: str) -> str:
    """Hash password with salt using SHA-256."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, hashed = stored_hash.split(":", 1)
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == hashed
    except Exception:
        return False

# JWT token management (simple implementation)
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRY_HOURS = 24

def create_token(user_id: int, username: str) -> str:
    """Create a simple JWT-like token."""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + (JWT_EXPIRY_HOURS * 3600),
        "iat": int(time.time()),
    }
    # Simple base64 encoding (not real JWT, but functional)
    import base64
    import json
    data = json.dumps(payload, default=str)
    token = base64.urlsafe_b64encode(data.encode()).decode()
    # Add signature
    sig = hashlib.sha256(f"{JWT_SECRET}:{token}".encode()).hexdigest()[:16]
    return f"{token}.{sig}"

def verify_token(token: str) -> Optional[dict]:
    """Verify token and return payload."""
    try:
        import base64
        import json
        token_part, sig = token.rsplit(".", 1)
        # Verify signature
        expected_sig = hashlib.sha256(f"{JWT_SECRET}:{token_part}".encode()).hexdigest()[:16]
        if sig != expected_sig:
            return None
        # Decode payload
        data = json.loads(base64.urlsafe_b64decode(token_part))
        # Check expiry
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


class AuthManager:
    """Manages user authentication with DSH compliance."""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._db_available = False

        # Check DB
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                self._db_available = True
                self._ensure_tables()
                cprint("[AUTH] PostgreSQL connected — auth system active", "white", "on_green")
            else:
                cprint("[AUTH] No DB — using file-based auth", "yellow")
        except Exception:
            cprint("[AUTH] DB init error", "yellow")

    def _ensure_tables(self):
        """Create users table if it doesn't exist."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return

            with pool.connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        display_name TEXT DEFAULT '',
                        role TEXT DEFAULT 'user',
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        last_login TIMESTAMPTZ,
                        is_active BOOLEAN DEFAULT TRUE
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id),
                        token TEXT UNIQUE NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        expires_at TIMESTAMPTZ NOT NULL,
                        ip_address TEXT DEFAULT '',
                        user_agent TEXT DEFAULT ''
                    )
                """)

                cprint("[AUTH] Users and sessions tables ready", "cyan")

        except Exception as e:
            cprint(f"[AUTH] Table init error: {e}", "yellow")

    def signup(self, username: str, email: str, password: str, 
               display_name: str = "") -> dict:
        """Create a new user account."""
        if not self._db_available:
            return {"error": "Database not available"}

        # Validate
        if len(username) < 3:
            return {"error": "Username must be at least 3 characters"}
        if len(password) < 6:
            return {"error": "Password must be at least 6 characters"}
        if "@" not in email:
            return {"error": "Invalid email address"}

        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return {"error": "Database not available"}

            with pool.connection() as conn:
                # Check if username exists
                existing = conn.execute(
                    "SELECT id FROM users WHERE username = %s", (username,)
                ).fetchone()
                if existing:
                    return {"error": "Username already taken"}

                # Check if email exists
                existing = conn.execute(
                    "SELECT id FROM users WHERE email = %s", (email,)
                ).fetchone()
                if existing:
                    return {"error": "Email already registered"}

                # Create user
                password_hash = hash_password(password)
                display_name = display_name or username

                row = conn.execute(
                    """INSERT INTO users (username, email, password_hash, display_name) 
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (username, email, password_hash, display_name)
                ).fetchone()

                user_id = row["id"]

                # Create session token
                token = create_token(user_id, username)

                # Save session
                expires_at = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
                conn.execute(
                    """INSERT INTO sessions (user_id, token, expires_at) 
                       VALUES (%s, %s, %s)""",
                    (user_id, token, expires_at)
                )

                # Emit event
                self._emit_event("auth/signup", {"user_id": user_id, "username": username})

                cprint(f"[AUTH] New user: {username} ({email})", "cyan")

                return {
                    "success": True,
                    "user_id": user_id,
                    "username": username,
                    "token": token,
                }

        except Exception as e:
            return {"error": str(e)}

    def login(self, username: str, password: str) -> dict:
        """Authenticate user and return token."""
        if not self._db_available:
            return {"error": "Database not available"}

        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return {"error": "Database not available"}

            with pool.connection() as conn:
                # Find user
                row = conn.execute(
                    """SELECT id, username, email, password_hash, display_name, role 
                       FROM users WHERE username = %s AND is_active = TRUE""",
                    (username,)
                ).fetchone()

                if not row:
                    return {"error": "Invalid username or password"}

                # Verify password
                if not verify_password(password, row["password_hash"]):
                    return {"error": "Invalid username or password"}

                user_id = row["id"]

                # Create token
                token = create_token(user_id, username)

                # Save session
                expires_at = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
                conn.execute(
                    """INSERT INTO sessions (user_id, token, expires_at) 
                       VALUES (%s, %s, %s)""",
                    (user_id, token, expires_at)
                )

                # Update last login
                conn.execute(
                    "UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,)
                )

                # Emit event
                self._emit_event("auth/login", {"user_id": user_id, "username": username})

                cprint(f"[AUTH] Login: {username}", "cyan")

                return {
                    "success": True,
                    "user_id": user_id,
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "role": row["role"],
                    "token": token,
                }

        except Exception as e:
            return {"error": str(e)}

    def verify_session(self, token: str) -> Optional[dict]:
        """Verify a session token and return user info."""
        if not token:
            return None

        payload = verify_token(token)
        if not payload:
            return None

        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return payload  # Fallback to token data

            with pool.connection() as conn:
                # Check session exists and not expired
                row = conn.execute(
                    """SELECT s.user_id, u.username, u.display_name, u.role 
                       FROM sessions s 
                       JOIN users u ON s.user_id = u.id 
                       WHERE s.token = %s AND s.expires_at > NOW()""",
                    (token,)
                ).fetchone()

                if not row:
                    return None

                return {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "role": row["role"],
                }

        except Exception:
            return payload  # Fallback to token data

    def logout(self, token: str) -> bool:
        """Remove a session token."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return False

            with pool.connection() as conn:
                conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
                return True

        except Exception:
            return False

    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user info by ID."""
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if not pool:
                return None

            with pool.connection() as conn:
                row = conn.execute(
                    """SELECT id, username, email, display_name, role, 
                       created_at, last_login 
                       FROM users WHERE id = %s""",
                    (user_id,)
                ).fetchone()

                return dict(row) if row else None

        except Exception:
            return None

    def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus."""
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except Exception:
            pass

        if self.event_bus:
            try:
                import asyncio
                asyncio.ensure_future(self.event_bus.emit(event_name, payload))
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────
_auth_instance = None

def get_auth_manager(event_bus=None) -> AuthManager:
    """Get or create the singleton AuthManager instance."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = AuthManager(event_bus=event_bus)
        cprint("[AUTH] Auth Manager initialized", "white", "on_green")
    return _auth_instance
