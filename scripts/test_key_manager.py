#!/usr/bin/env python3
"""
Mocked tests for the rotating key engine — NO real API keys or network needed.

Simulates:
  1. Round-robin across 3 healthy keys
  2. 429 -> long cooldown -> skipped -> next key succeeds
  3. Timeout -> short cooldown -> skipped
  4. AUTH error -> DISABLED
  5. All keys on cooldown -> graceful failure
  6. Cooldown expiry -> automatic reactivation

Usage: python scripts/test_key_manager.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.key_manager import ErrorClass, GeminiKeyManager, _KeyEntry, mask_key
from config import load_settings
from database.db import Database


class FakeManager(GeminiKeyManager):
    """Overrides the network call with a scripted per-key behavior."""

    def __init__(self, db, settings, script):
        super().__init__(db, settings)
        self.script = script          # dict: key -> list of Exception|str outcomes

    async def _fake_call(self, entry):
        outcomes = self.script.get(entry.key, ["OK-DEFAULT"])
        outcome = outcomes.pop(0) if outcomes else "OK-DEFAULT"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def generate(self, prompt, **kw):
        self._reactivate()
        attempts = []
        tried = set()
        while True:
            entry = self._next_healthy()
            if entry is None or entry.fingerprint in tried:
                break
            tried.add(entry.fingerprint)
            try:
                text = await self._fake_call(entry)
                await self._record(entry, None)
                return text, {"success_key": entry.masked, "attempts": attempts}
            except Exception as exc:
                ec = self.classify(exc)
                self._mark(entry, ec)
                await self._record(entry, ec)
                attempts.append({"key": entry.masked, "error_class": ec})
        return None, {"success_key": None, "attempts": attempts, "error": "all_failed"}


class FakeSettings:
    gemini_model = "mock"
    gemini_timeout_seconds = 1
    gemini_key_cooldown_seconds = 0.3       # tiny for fast tests
    gemini_timeout_cooldown_seconds = 0.2
    gemini_api_keys = ()
    bot_token = "test-secret-token"


class _Timeout(Exception):
    pass


async def main() -> None:
    import os, tempfile
    tmp = tempfile.mkdtemp()
    os.environ["TELEGRAM_BOT_TOKEN"] = "test-secret-token"
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "t.db")

    settings = FakeSettings()
    db = Database(os.path.join(tmp, "t.db"))
    await db.connect()
    from database.migrations import run_migrations
    await run_migrations(db)

    K1, K2, K3 = "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1", \
                  "AIzaSyBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2", \
                  "AIzaSyCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC3"

    results = []

    def check(name, cond):
        results.append((name, cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # --- Test 1: round robin across healthy keys ---
    mgr = FakeManager(db, settings, {})
    mgr._keys = [_KeyEntry(i, k) for i, k in enumerate((K1, K2, K3))]
    r1 = await mgr.generate("q"); r2 = await mgr.generate("q"); r3 = await mgr.generate("q")
    keys_used = {r1[1]["success_key"], r2[1]["success_key"], r3[1]["success_key"]}
    check("round-robin cycles 3 healthy keys", len(keys_used) == 3)
    check("all responses succeed", all(x[0] is not None for x in (r1, r2, r3)))

    # --- Test 2: 429 -> cooldown -> failover to next key ---
    mgr = FakeManager(db, settings, {
        K1: [Exception("429 RESOURCE_EXHAUSTED quota exceeded"), "OK-2"],
        K2: ["OK-K2A", "OK-K2B"],
        K3: ["OK-K3"],
    })
    mgr._keys = [_KeyEntry(i, k) for i, k in enumerate((K1, K2, K3))]
    text, meta = await mgr.generate("q")
    check("429 on K1 -> failover to K2", text == "OK-K2A")
    k1 = next(e for e in mgr._keys if e.key == K1)
    check("K1 marked COOLDOWN", k1.status == "COOLDOWN")
    text2, _ = await mgr.generate("q")
    check("K1 skipped while cooling (K2 used)", text2 == "OK-K2B")

    # --- Test 3: timeout -> short cooldown -> skip ---
    mgr = FakeManager(db, settings, {
        K1: [_Timeout("request timed out"), "OK-AFTER"],
        K2: ["OK-T2"],
    })
    mgr._keys = [_KeyEntry(i, k) for i, k in enumerate((K1, K2, K3))]
    text, _ = await mgr.generate("q")
    check("timeout on K1 -> K2 answers", text == "OK-T2")

    # --- Test 4: auth error -> DISABLED ---
    mgr = FakeManager(db, settings, {K1: [Exception("API key not valid. Please pass a valid API key.")]})
    mgr._keys = [_KeyEntry(i, k) for i, k in enumerate((K1, K2, K3))]
    text, _ = await mgr.generate("q")
    k1 = next(e for e in mgr._keys if e.key == K1)
    check("AUTH_ERROR disables key", k1.status == "DISABLED")

    # --- Test 5: all keys cooling -> graceful None ---
    mgr = FakeManager(db, settings, {k: [Exception("429 quota")] for k in (K1, K2, K3)})
    mgr._keys = [_KeyEntry(i, k) for i, k in enumerate((K1, K2, K3))]
    text, meta = await mgr.generate("q")
    check("all keys fail -> graceful None", text is None)

    # --- Test 6: cooldown expiry -> reactivation ---
    await asyncio.sleep(0.35)   # cooldown was 0.3s
    mgr._reactivate()
    check("cooldown expired -> keys ACTIVE again",
          all(e.healthy() for e in mgr._keys))
    text, _ = await mgr.generate("q")
    check("post-reactivation request succeeds", text is not None)

    # --- Test 7: mask + fingerprint safety ---
    check("mask hides key body", mask_key(K1) == "AIzaSy...A1")
    check("fingerprint never equals key", GeminiKeyManager.key_fingerprint(K1) != K1)

    # --- Test 8: add_key validation (real manager methods, no network) ---
    real = GeminiKeyManager(db, settings)
    ok, msg = await real.add_key("not-a-key")
    check("add_key rejects bad format", not ok)
    ok, msg = await real.add_key(K1)
    check("add_key accepts valid format", ok)
    ok, msg = await real.add_key(K1)
    check("add_key rejects duplicate", not ok)
    row = await db.fetchone("SELECT key_cipher, key_source, status FROM api_key_status "
                            "WHERE key_source='DYNAMIC'")
    check("dynamic key stored encrypted (no plaintext in DB)",
          row is not None and K1 not in (row["key_cipher"] or ""))
    roundtrip = real._cipher.decrypt(row["key_cipher"])
    check("encrypted key decrypts correctly", roundtrip == K1)
    ok, msg = await real.del_key(mask_key(K1))
    check("del_key removes by masked ref", ok)
    row2 = await db.fetchone("SELECT status FROM api_key_status WHERE key_hash=?",
                             (GeminiKeyManager.key_fingerprint(K1),))
    check("del_key sets DISABLED in DB", row2 and row2["status"] == "DISABLED")

    # plaintext leak check across all DB text
    dump = await db.fetchall("SELECT * FROM api_key_status")
    leaked = any(K1 in str(tuple(r)) or K2 in str(tuple(r)) for r in dump)
    check("no plaintext key anywhere in api_key_status", not leaked)

    await db.close()
    failed = [n for n, ok in results if not ok]
    print(f"\nRESULT: {len(results) - len(failed)}/{len(results)} passed"
          + (f" — FAILED: {failed}" if failed else " — ALL TESTS PASSED"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
