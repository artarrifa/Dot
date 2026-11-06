"""Offline, fail-closed Instagram Business action proposal queue.

This module does not import Dot's LLM tools and must not receive access tokens.
It never calls Meta. Production transport is deliberately out of scope until
the OAuth, sandbox, human-approval and integration tests are independently verified.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionType = Literal["publish_image", "publish_reel", "reply_comment", "reply_dm"]
UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


class InstagramAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ActionType
    account_id: str = Field(min_length=1, max_length=40, pattern=r"^[0-9]+$")
    target_id: str | None = Field(default=None, max_length=64, pattern=r"^[0-9]+$")
    media_url: str | None = Field(default=None, max_length=2048)
    text: str = Field(min_length=1, max_length=2200)
    due_at: datetime

    @model_validator(mode="after")
    def valid_target(self) -> InstagramAction:
        if self.due_at.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        if self.kind.startswith("publish_"):
            if self.target_id is not None or not self.media_url:
                raise ValueError("publishing requires media_url and no target_id")
            from urllib.parse import urlsplit
            url = urlsplit(self.media_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError("media_url must be a clean HTTPS URL")
            import ipaddress
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                pass
            else:
                raise ValueError("media_url may not point to an IP address")
            if url.hostname.lower() in {"localhost", "localhost.localdomain"} or url.hostname.lower().endswith(".local"):
                raise ValueError("private/local media destinations are not allowed")
        elif not self.target_id or self.media_url is not None:
            raise ValueError("replies require target_id and no media_url")
        return self


def canonical(action: InstagramAction) -> str:
    return action.model_dump_json(exclude_none=True)


def fingerprint(action: InstagramAction) -> str:
    return hashlib.sha256(canonical(action).encode("utf-8")).hexdigest()


class SafeQueue:
    """SQLite proposals + audit. No post/reply network operation exists here.

    The audit table intentionally keeps no captions, addresses or tokens.
    Its integrity hash is an identifier, not a cryptographic audit chain.
    """

    def __init__(self, database: Path, *, allowed_account_id: str):
        if not allowed_account_id.isdecimal():
            raise ValueError("allowed_account_id must be numeric")
        self.allowed_account_id = allowed_account_id
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS proposals "
            "(id TEXT PRIMARY KEY, body TEXT NOT NULL, fingerprint TEXT NOT NULL, "
            "state TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS audit "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, event_at TEXT NOT NULL, "
            "proposal_id TEXT NOT NULL, action_kind TEXT NOT NULL, event TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL)"
        )
        self.db.commit()
        try:
            database.chmod(0o600)
        except OSError:
            pass

    def _audit(self, proposal_id: str, action: InstagramAction, event: str) -> None:
        self.db.execute(
            "INSERT INTO audit (event_at,proposal_id,action_kind,event,fingerprint) VALUES (?,?,?,?,?)",
            (now_utc().isoformat(), proposal_id, action.kind, event, fingerprint(action)),
        )

    def propose(self, action: InstagramAction) -> str:
        if action.account_id != self.allowed_account_id:
            raise PermissionError("account is not allowlisted")
        if action.due_at < now_utc() - timedelta(minutes=1):
            raise ValueError("due_at is in the past")
        proposal_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO proposals VALUES (?,?,?,?,?,?)",
                (proposal_id, canonical(action), fingerprint(action),
                 "pending", now_utc().isoformat(), (now_utc() + timedelta(days=7)).isoformat()),
            )
            self._audit(proposal_id, action, "proposed")
        return proposal_id

    def inspect(self, proposal_id: str) -> dict[str, str]:
        row = self.db.execute(
            "SELECT body, fingerprint, state, expires_at FROM proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise KeyError("proposal not found")
        body, digest, state, expires = row
        action = InstagramAction.model_validate_json(body)
        if fingerprint(action) != digest:
            raise ValueError("proposal payload changed")
        return {"id": proposal_id, "state": state, "expires_at": expires,
                "fingerprint": digest, "action": json.loads(canonical(action))}

    def approve_by_human(self, proposal_id: str, *, reviewed_fingerprint: str, human_confirmed: bool) -> None:
        """Call ONLY from authenticated, human-operated UI; never expose as an LLM tool."""
        if not human_confirmed:
            raise PermissionError("human approval required")
        record = self.inspect(proposal_id)
        if record["state"] != "pending" or now_utc() >= datetime.fromisoformat(record["expires_at"]):
            raise PermissionError("proposal is no longer pending")
        if reviewed_fingerprint != record["fingerprint"]:
            raise PermissionError("the displayed action differs from stored content")
        action = InstagramAction.model_validate(record["action"])
        with self.db:
            changed = self.db.execute(
                "UPDATE proposals SET state='approved' WHERE id=? AND state='pending'",
                (proposal_id,),
            ).rowcount
            if changed != 1:
                raise PermissionError("concurrent approval change")
            self._audit(proposal_id, action, "approved")

    def list_ready_for_manual_review(self) -> list[dict[str, str]]:
        """No automatic execution in V0.1, even for approved proposals."""
        rows = self.db.execute(
            "SELECT id FROM proposals WHERE state='approved' ORDER BY created_at"
        ).fetchall()
        result = []
        for (proposal_id,) in rows:
            record = self.inspect(proposal_id)
            action = InstagramAction.model_validate(record["action"])
            if action.due_at <= now_utc() and now_utc() < datetime.fromisoformat(record["expires_at"]):
                result.append(record)
        return result

    def close(self) -> None:
        self.db.close()
