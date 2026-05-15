from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# TaskSpec
# ---------------------------------------------------------------------------

@dataclass
class TaskSpec:
    task_id: str
    title: str
    description: str
    status: str = "pending"
    pm_session: str = ""
    assigned_to: Optional[str] = None
    branch: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    owned_files: List[str] = field(default_factory=list)
    read_files: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    engine: str = "claude-agent"
    model: str = "sonnet"
    created_at: str = ""
    updated_at: str = ""
    result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TaskSpec:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, **kw)


def create_task(
    title: str,
    description: str,
    pm_session: str = "",
    depends_on: Optional[List[str]] = None,
    owned_files: Optional[List[str]] = None,
    read_files: Optional[List[str]] = None,
    acceptance_criteria: Optional[List[str]] = None,
    engine: str = "claude-agent",
    model: str = "sonnet",
) -> TaskSpec:
    tid = f"task-{_short_id()}"
    now = _now_iso()
    return TaskSpec(
        task_id=tid,
        title=title,
        description=description,
        pm_session=pm_session,
        branch=f"agent/{tid}",
        depends_on=depends_on or [],
        owned_files=owned_files or [],
        read_files=read_files or [],
        acceptance_criteria=acceptance_criteria or [],
        engine=engine,
        model=model,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass
class Message:
    id: str
    ts: str
    from_agent: str
    to_agent: str
    msg_type: str
    subject: str
    body: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Message:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, **kw)

    @property
    def filename(self) -> str:
        ts_safe = self.ts.replace(":", "").replace("-", "")[:15]
        return f"{ts_safe}-{self.from_agent}-{self.msg_type}.json"


def create_message(
    from_agent: str,
    to_agent: str,
    msg_type: str,
    subject: str,
    body: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> Message:
    return Message(
        id=f"msg-{_short_id()}",
        ts=_now_iso(),
        from_agent=from_agent,
        to_agent=to_agent,
        msg_type=msg_type,
        subject=subject,
        body=body,
        data=data or {},
    )


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

@dataclass
class Event:
    seq: int
    ts: str
    actor: str
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> Event:
        d = json.loads(line)
        return cls(**d)


# ---------------------------------------------------------------------------
# PM Session
# ---------------------------------------------------------------------------

@dataclass
class PMSession:
    id: str
    task: str
    status: str = "running"
    workers: List[str] = field(default_factory=list)
    started_at: str = ""
    total_cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PMSession:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


def create_pm_session(task: str) -> PMSession:
    return PMSession(
        id=f"pm-{_short_id()[:4]}",
        task=task,
        started_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# AyaState
# ---------------------------------------------------------------------------

@dataclass
class AyaState:
    project_name: str
    status: str = "running"
    pm_sessions: List[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    started_at: str = ""
    version: str = "0.1.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> AyaState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, **kw)
