"""
Rotating Gemini API Key Engine.

Features:
- Multiple keys from env (GEMINI_API_KEYS) + dynamically added via /addkey.
- Round-robin rotation with health-aware failover (cooldown/error keys skipped).
- Error classification: RATE_LIMIT / QUOTA / TIMEOUT / AUTH_ERROR / SERVER_ERROR / UNKNOWN.
- Cooldowns: 429/quota -> long (config), timeout -> short (config), auth -> DISABLED forever.
- Automatic reactivation after cooldown expiry (lazy, checked on each rotation).
- Per-key stats persisted in api_key_status (hash + masked prefix only, never plaintext).
- Dynamic keys persisted ENCRYPTED (HMAC-SHA256 stream cipher keyed from bot token,
  HMAC-authenticated) so they survive restarts; decryptable only within this process/host.
- generate() never raises: returns (text, meta) or (None, meta) with a graceful reason.

Never logs plaintext keys. Masked form: AIzaSy...X9a
"""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone

from google import genai
from google.genai import types as gtypes

logger = logging.getLogger(__name__)

MASK_PREFIX, MASK_SUFFIX = 6, 3


class ErrorClass:
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA = "QUOTA"
    TIMEOUT = "TIMEOUT"
    AUTH_ERROR = "AUTH_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    UNKNOWN = "UNKNOWN"


RETRYABLE = {ErrorClass.RATE_LIMIT, ErrorClass.QUOTA, ErrorClass.TIMEOUT,
             ErrorClass.SERVER_ERROR, ErrorClass.UNKNOWN}


def mask_key(key: str) -> str:
    if not key:
        return "?"
    return f"{key[:MASK_PREFIX]}...{key[-MASK_SUFFIX:]}"


def key_fingerprint(key: str) -> str:
    """Stable identifier for DB rows: sha256 of the key (first 16 hex chars)."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class _Cipher:
    """HMAC-SHA256 keystream encryption (authenticated). Key derived from bot token."""

    def __init__(self, secret: str):
        self._master = hmac.new(secret.encode(), b"gemini-key-vault", hashlib.sha256).digest()

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(16)
        ks = self._keystream(nonce, len(plaintext))
        ct = bytes(a ^ b for a, b in zip(plaintext.encode(), ks))
        mac = hmac.new(self._master, nonce + ct, hashlib.sha256).digest()[:16]
        return (nonce + mac + ct).hex()

    def decrypt(self, token: str) -> str | None:
        try:
            raw = bytes.fromhex(token)
            nonce, mac, ct = raw[:16], raw[16:32], raw[32:]
            if not hmac.compare_digest(
                    mac, hmac.new(self._master, nonce + ct, hashlib.sha256).digest()[:16]):
                return None
            ks = self._keystream(nonce, len(ct))
            return bytes(a ^ b for a, b in zip(ct, ks)).decode()
        except Exception:
            return None

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        out, counter = b"", 0
        while len(out) < length:
            out += hmac.new(self._master, nonce + counter.to_bytes(4, "big"),
                            hashlib.sha256).digest()
            counter += 1
        return out[:length]


class _KeyEntry:
    __slots__ = ("index", "fingerprint", "masked", "key", "cipher",
                 "cooldown_until", "status")

    def __init__(self, index: int, key: str):
        self.index = index
        self.key = key
        self.fingerprint = key_fingerprint(key)
        self.masked = mask_key(key)
        self.cooldown_until = 0.0
        self.status = "ACTIVE"

    def healthy(self) -> bool:
        return self.status == "ACTIVE" and time.monotonic() >= self.cooldown_until


class GeminiKeyManager:
    """
    Usage:
        mgr = GeminiKeyManager(db, settings)
        await mgr.load()
        text, meta = await mgr.generate("Hello")
        if text is None:  # all keys failed -> graceful message
            ...
    """

    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.model = settings.gemini_model
        self.timeout = settings.gemini_timeout_seconds
        self.long_cooldown = settings.gemini_key_cooldown_seconds
        self.short_cooldown = settings.gemini_timeout_cooldown_seconds
        self._cipher = _Cipher(settings.bot_token)
        self._keys: list[_KeyEntry] = []
        self._rr = 0            # rotation position
        self._lock = asyncio.Lock()

    # ---------- lifecycle ----------

    async def load(self) -> None:
        """Load env keys + persisted dynamic keys; sync DB rows."""
        async with self._lock:
            self._keys.clear()
            rows = await self.db.fetchall(
                "SELECT * FROM api_key_status WHERE key_source='DYNAMIC' "
                "AND status != 'DISABLED' ORDER BY key_index")
            for r in rows:
                plain = self._cipher.decrypt(r["key_cipher"] or "")
                if plain:
                    self._keys.append(self._make_entry(plain))
                else:
                    logger.error("Dynamic key %s failed to decrypt — skipping "
                                 "(wrong bot token?)", r["key_hash"])
            for k in self.settings.gemini_api_keys:
                if not any(kk.key == k for kk in self._keys):
                    self._keys.append(self._make_entry(k))
            for i, entry in enumerate(self._keys):
                await self._sync_row(entry, create=True)
            logger.info("Key manager loaded: %d keys (rotation at position %d)",
                        len(self._keys), self._rr)

    def _make_entry(self, key: str) -> _KeyEntry:
        return _KeyEntry(len(self._keys), key)

    # ---------- DB sync ----------

    async def _sync_row(self, entry: _KeyEntry, create: bool = False) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        row = await self.db.fetchone(
            "SELECT id, status, cooldown_until FROM api_key_status WHERE key_hash = ?",
            (entry.fingerprint,))
        if row is None:
            if not create:
                return
            await self.db.execute(
                "INSERT INTO api_key_status (key_index, key_hash, key_prefix, key_source, "
                "status) VALUES (?, ?, ?, 'ENV', ?)",
                (entry.index, entry.fingerprint, entry.masked, entry.status))
            row = await self.db.fetchone(
                "SELECT id, status, cooldown_until FROM api_key_status WHERE key_hash = ?",
                (entry.fingerprint,))
        if row is None:
            return
        cd = None
        if entry.cooldown_until > time.monotonic():
            remain = entry.cooldown_until - time.monotonic()
            cd = (datetime.now(timezone.utc).timestamp() + remain)
            cd = datetime.fromtimestamp(cd, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            "UPDATE api_key_status SET status=?, cooldown_until=?, "
            "key_prefix=?, updated_at=? WHERE id=?",
            (entry.status, cd, entry.masked, now, row["id"]))

    async def _record(self, entry: _KeyEntry, error_class: str | None) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            """
            UPDATE api_key_status SET
                total_requests = total_requests + 1,
                successful_requests = successful_requests + ?,
                failed_requests = failed_requests + ?,
                rate_limit_errors = rate_limit_errors + ?,
                timeout_errors = timeout_errors + ?,
                last_used_at = ?, last_error_at = ?, updated_at = ?
            WHERE key_hash = ?
            """,
            (1 if error_class is None else 0,
             0 if error_class is None else 1,
             1 if error_class == ErrorClass.RATE_LIMIT else 0,
             1 if error_class == ErrorClass.TIMEOUT else 0,
             now, now if error_class else None, now, entry.fingerprint),
        )

    # ---------- rotation ----------

    def _next_healthy(self) -> _KeyEntry | None:
        if not self._keys:
            return None
        n = len(self._keys)
        for i in range(n):
            entry = self._keys[(self._rr + i) % n]
            if entry.healthy():
                self._rr = (self._rr + i + 1) % n
                return entry
        return None

    def _mark(self, entry: _KeyEntry, error_class: str) -> None:
        if error_class in (ErrorClass.RATE_LIMIT, ErrorClass.QUOTA):
            entry.cooldown_until = time.monotonic() + self.long_cooldown
            entry.status = "COOLDOWN"
            logger.warning("Key %s: %s — cooldown %ds", entry.masked, error_class,
                           self.long_cooldown)
        elif error_class == ErrorClass.TIMEOUT:
            entry.cooldown_until = time.monotonic() + self.short_cooldown
            entry.status = "COOLDOWN"
            logger.warning("Key %s: TIMEOUT — cooldown %ds", entry.masked, self.short_cooldown)
        elif error_class == ErrorClass.AUTH_ERROR:
            entry.status = "DISABLED"
            entry.cooldown_until = float("inf")
            logger.error("Key %s: AUTH_ERROR — permanently disabled", entry.masked)
        else:
            entry.cooldown_until = time.monotonic() + self.short_cooldown
            entry.status = "ERROR"
            logger.warning("Key %s: %s — short cooldown", entry.masked, error_class)

    def _reactivate(self) -> None:
        now = time.monotonic()
        for e in self._keys:
            if e.status in ("COOLDOWN", "ERROR") and now >= e.cooldown_until:
                e.status = "ACTIVE"
                e.cooldown_until = 0.0

    # ---------- error classification ----------

    @staticmethod
    def classify(exc: Exception) -> str:
        msg = str(exc).lower()
        code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None),
                                                     "status_code", None)
        if code == 429 or "429" in msg or "resource_exhausted" in msg or "quota" in msg:
            return ErrorClass.RATE_LIMIT
        if "permission" in msg and "denied" in msg:
            return ErrorClass.QUOTA
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in msg \
                or "timeout" in msg:
            return ErrorClass.TIMEOUT
        if code in (401, 403) or "api key" in msg and "invalid" in msg \
                or "api_key_invalid" in msg or "unauthenticated" in msg \
                or "permission denied" in msg:
            return ErrorClass.AUTH_ERROR
        if (code is not None and 500 <= int(code) < 600) or \
                "internal error" in msg or "unavailable" in msg or "503" in msg:
            return ErrorClass.SERVER_ERROR
        if "connection" in msg or "network" in msg or "unreachable" in msg:
            return ErrorClass.SERVER_ERROR
        return ErrorClass.UNKNOWN

    # ---------- main entry ----------

    async def generate(self, prompt: str, *, system: str | None = None,
                       history: list | None = None) -> tuple[str | None, dict]:
        """
        Try healthy keys in rotation until one succeeds.
        Returns (text, meta). text is None only if ALL keys failed.
        meta: {"attempts": [...], "success_key": masked|None, "error": reason|None}
        Attempt entries are masked — safe to log/display.
        """
        self._reactivate()
        attempts: list[dict] = []
        tried: set[str] = set()

        while True:
            entry = self._next_healthy()
            if entry is None or entry.fingerprint in tried:
                break
            tried.add(entry.fingerprint)
            t0 = time.monotonic()
            try:
                client = genai.Client(api_key=entry.key)
                cfg = gtypes.GenerateContentConfig(
                    system_instruction=system,
                ) if system else None
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=self.model,
                        contents=history + [prompt] if history else prompt,
                        config=cfg,
                    ),
                    timeout=self.timeout,
                )
                text = (response.text or "").strip()
                if not text:
                    raise ValueError("empty response")
                await self._record(entry, None)
                await self._sync_row(entry)
                logger.info("Gemini OK via key %s in %dms",
                            entry.masked, int((time.monotonic() - t0) * 1000))
                return text, {"attempts": attempts, "success_key": entry.masked,
                              "error": None}
            except Exception as exc:
                ec = self.classify(exc)
                self._mark(entry, ec)
                await self._record(entry, ec)
                await self._sync_row(entry)
                attempts.append({"key": entry.masked, "error_class": ec,
                                 "message": str(exc)[:120]})
                logger.warning("Key %s failed (%s): %s", entry.masked, ec, str(exc)[:120])
                if ec not in RETRYABLE:
                    # AUTH errors etc. — do not endlessly rotate; try next once, then stop
                    continue

        reason = "no_keys_configured" if not self._keys else "all_keys_unavailable"
        logger.error("Gemini generate failed: %s (attempts=%d)", reason, len(attempts))
        return None, {"attempts": attempts, "success_key": None, "error": reason}

    # ---------- dynamic key management (admin) ----------

    @staticmethod
    def validate_key_format(key: str) -> bool:
        key = key.strip()
        return key.startswith("AIza") and 30 <= len(key) <= 60 and key.isprintable()

    async def add_key(self, raw_key: str) -> tuple[bool, str]:
        key = raw_key.strip()
        if not self.validate_key_format(key):
            return False, "Invalid key format — Gemini keys start with 'AIza' and are ~39 chars."
        fp = key_fingerprint(key)
        async with self._lock:
            if any(k.key == key for k in self._keys):
                return False, "This key is already active in the rotation pool."
            existing = await self.db.fetchone(
                "SELECT id, status FROM api_key_status WHERE key_hash = ?", (fp,))
            if existing is not None and existing["status"] == "DISABLED":
                return False, "This key was previously disabled. Re-adding disabled keys is not allowed."
            entry = _KeyEntry(len(self._keys), key)
            self._keys.append(entry)
            self._rr = 0
            cipher_hex = self._cipher.encrypt(key)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            await self.db.execute(
                """
                INSERT INTO api_key_status
                    (key_index, key_hash, key_prefix, key_source, key_cipher, status)
                VALUES (?, ?, ?, 'DYNAMIC', ?, 'ACTIVE')
                ON CONFLICT(key_hash) DO UPDATE SET
                    status='ACTIVE', key_source='DYNAMIC', key_cipher=excluded.key_cipher,
                    cooldown_until=NULL, updated_at=excluded.updated_at
                """.replace("excluded.updated_at", f"'{now}'"),
                (entry.index, fp, entry.masked, cipher_hex),
            )
            logger.info("Dynamic key added: %s", entry.masked)
        return True, f"Key {entry.masked} added and active (position {entry.index + 1})."

    async def del_key(self, key_ref: str) -> tuple[bool, str]:
        """key_ref: masked prefix ('AIzaSy...X9a') or fingerprint hash."""
        async with self._lock:
            target = None
            for e in self._keys:
                if e.masked == key_ref.strip() or e.fingerprint == key_ref.strip():
                    target = e
                    break
            if target is None:
                return False, f"No active key matches '{key_ref}'. Use /apistats to list keys."
            self._keys.remove(target)
            self._rr = 0
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            await self.db.execute(
                "UPDATE api_key_status SET status='DISABLED', key_cipher='', "
                "cooldown_until=NULL, updated_at=? WHERE key_hash=?",
                (now, target.fingerprint))
            logger.info("Key removed from rotation: %s", target.masked)
            return True, f"Key {target.masked} removed from rotation and disabled."

    async def test_all_keys(self) -> list[dict]:
        """Lightweight health check per key. Updates statuses. Returns masked results."""
        self._reactivate()
        results: list[dict] = []
        for entry in list(self._keys):
            t0 = time.monotonic()
            try:
                client = genai.Client(api_key=entry.key)
                await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=self.model, contents="ping — reply with: pong"),
                    timeout=self.timeout)
                entry.status, entry.cooldown_until = "ACTIVE", 0.0
                await self._record(entry, None)
                results.append({"key": entry.masked, "ok": True, "ms":
                                int((time.monotonic() - t0) * 1000), "error": None})
            except Exception as exc:
                ec = self.classify(exc)
                self._mark(entry, ec)
                await self._record(entry, ec)
                results.append({"key": entry.masked, "ok": False, "ms":
                                int((time.monotonic() - t0) * 1000), "error": ec})
            await self._sync_row(entry)
        return results

    async def stats(self) -> dict:
        self._reactivate()
        rows = await self.db.fetchall(
            "SELECT * FROM api_key_status ORDER BY key_index")
        active = sum(1 for e in self._keys if e.healthy())
        return {
            "rows": rows,
            "total": len(rows),
            "active_in_pool": len(self._keys),
            "active": active,
            "cooldown": sum(1 for e in self._keys if e.status == "COOLDOWN"),
            "disabled": sum(1 for r in rows if r["status"] == "DISABLED"),
            "rotation_position": self._rr,
            "totals": {
                "requests": sum(r["total_requests"] for r in rows),
                "success": sum(r["successful_requests"] for r in rows),
                "rate_limited": sum(r["rate_limit_errors"] for r in rows),
                "timeouts": sum(r["timeout_errors"] for r in rows),
                "failures": sum(r["failed_requests"] for r in rows),
            },
        }
