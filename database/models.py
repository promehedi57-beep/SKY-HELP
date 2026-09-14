"""
Typed row wrappers for the tables used most often.
Raw aiosqlite.Row is fine too; these classes give IDE-friendly access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Admin:
    id: int
    user_id: int
    role: str
    enabled: bool
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Admin":
        return cls(row["id"], row["user_id"], row["role"], bool(row["enabled"]), row["created_at"])


@dataclass(slots=True)
class Trigger:
    id: int
    trigger: str
    category: str
    description: str
    priority: int
    enabled: bool

    @classmethod
    def from_row(cls, row) -> "Trigger":
        return cls(row["id"], row["trigger"], row["category"], row["description"],
                   row["priority"], bool(row["enabled"]))


@dataclass(slots=True)
class Method:
    id: int
    method_key: str
    title: str
    content: str
    template: str
    category: str
    enabled: bool
    version: int

    @classmethod
    def from_row(cls, row) -> "Method":
        return cls(row["id"], row["method_key"], row["title"], row["content"],
                   row["template"], row["category"], bool(row["enabled"]), row["version"])


@dataclass(slots=True)
class PendingMethod:
    id: int
    source_name: str
    source_username: str
    raw_text: str
    detected_trigger: str
    status: str

    @classmethod
    def from_row(cls, row) -> "PendingMethod":
        return cls(row["id"], row["source_name"], row["source_username"],
                   row["raw_text"], row["detected_trigger"], row["status"])


@dataclass(slots=True)
class ApiKeyStatus:
    id: int
    key_index: int
    key_hash: str
    key_prefix: str
    status: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    rate_limit_errors: int
    timeout_errors: int
    cooldown_until: str | None

    @classmethod
    def from_row(cls, row) -> "ApiKeyStatus":
        return cls(row["id"], row["key_index"], row["key_hash"], row["key_prefix"],
                   row["status"], row["total_requests"], row["successful_requests"],
                   row["failed_requests"], row["rate_limit_errors"], row["timeout_errors"],
                   row["cooldown_until"])
