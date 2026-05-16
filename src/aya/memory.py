"""
AYA Memory — three-layer persistent memory system.

Layer 1: Working Memory (per-session) — already in runtime/ (board, tasks, mailbox)
Layer 2: Project Memory (cross-session) — ~/.aya/memory/<project-hash>/
Layer 3: Global Memory (cross-project) — ~/.aya/memory/global/
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Regex for parsing fenced sections in patterns.md / profile.md
_SECTION_RE = re.compile(r'<!-- section: (.+?) -->\n(.*?)\n<!-- /section -->', re.DOTALL)


def _sanitize_key(key: str) -> str:
    """Sanitize a key for safe use in filenames and markdown headers."""
    safe = re.sub(r'[/\\.\n\r\x00]', '_', key)
    safe = safe.lstrip('._-')
    return safe[:64] or "unnamed"

AYA_HOME = Path.home() / ".aya"


def _project_hash(project_dir: Path) -> str:
    return hashlib.sha256(str(project_dir).encode()).hexdigest()[:12]


class AyaMemory:
    """Three-layer memory for AYA PM. File-based, append-only where possible."""

    def __init__(self, project_dir: str = "."):
        self.project_dir = Path(project_dir).resolve()
        self._proj_hash = _project_hash(self.project_dir)
        self.project_memory_dir = AYA_HOME / "memory" / self._proj_hash
        self.global_memory_dir = AYA_HOME / "memory" / "global"

    def ensure_dirs(self) -> None:
        self.project_memory_dir.mkdir(parents=True, exist_ok=True)
        self.global_memory_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Layer 2: Project Memory — Routing History
    # ------------------------------------------------------------------

    def log_routing(self, task_id: str, task_type: str, model: str,
                    engine: str, success: bool, cost_usd: float = 0.0,
                    turns: int = 0, duration_ms: int = 0) -> None:
        """Append a routing result to history (append-only JSONL)."""
        self.ensure_dirs()
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "task_type": task_type,
            "model": model,
            "engine": engine,
            "success": success,
            "cost_usd": cost_usd,
            "turns": turns,
            "duration_ms": duration_ms,
        }
        self._append_jsonl(self.project_memory_dir / "routing-history.jsonl", entry)

    def get_routing_history(self, task_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Read routing history, optionally filtered by task_type."""
        entries = self._read_jsonl(self.project_memory_dir / "routing-history.jsonl")
        if task_type:
            entries = [e for e in entries if e.get("task_type") == task_type]
        return entries[-limit:]

    def get_model_stats(self) -> Dict[str, Dict[str, Any]]:
        """Aggregate routing history into per-model stats.
        Returns: {model_name: {total, successes, failures, success_rate, avg_cost, avg_turns}}"""
        entries = self._read_jsonl(self.project_memory_dir / "routing-history.jsonl")
        stats: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            model = e.get("model", "unknown")
            if model not in stats:
                stats[model] = {"total": 0, "successes": 0, "failures": 0,
                                "total_cost": 0.0, "total_turns": 0}
            s = stats[model]
            s["total"] += 1
            if e.get("success"):
                s["successes"] += 1
            else:
                s["failures"] += 1
            s["total_cost"] += e.get("cost_usd", 0)
            s["total_turns"] += e.get("turns", 0)

        # Calculate rates and averages
        for model, s in stats.items():
            s["success_rate"] = s["successes"] / s["total"] if s["total"] > 0 else 0
            s["avg_cost"] = s["total_cost"] / s["total"] if s["total"] > 0 else 0
            s["avg_turns"] = s["total_turns"] / s["total"] if s["total"] > 0 else 0

        return stats

    # ------------------------------------------------------------------
    # Layer 2: Project Memory — Failures
    # ------------------------------------------------------------------

    def log_failure(self, task_id: str, model: str, task_type: str,
                    error_summary: str, resolution: str = "") -> None:
        """Log a worker failure for future reference."""
        self.ensure_dirs()
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "model": model,
            "task_type": task_type,
            "error_summary": error_summary,
            "resolution": resolution,
        }
        self._append_jsonl(self.project_memory_dir / "failures.jsonl", entry)

    def get_failures(self, task_type: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Read failure history."""
        entries = self._read_jsonl(self.project_memory_dir / "failures.jsonl")
        if task_type:
            entries = [e for e in entries if e.get("task_type") == task_type]
        return entries[-limit:]

    # ------------------------------------------------------------------
    # Layer 2: Project Memory — Patterns
    # ------------------------------------------------------------------

    def write_pattern(self, key: str, content: str) -> None:
        """Write or update a project pattern. Patterns are stored as fenced sections in patterns.md."""
        self.ensure_dirs()
        key = _sanitize_key(key)
        patterns_file = self.project_memory_dir / "patterns.md"
        existing = self.read_patterns()
        existing[key] = content
        # Write all patterns back using fenced format to avoid ## collision
        lines = []
        for k, v in sorted(existing.items()):
            lines.append(f"<!-- section: {k} -->\n{v}\n<!-- /section -->")
        self._write_file_atomic(patterns_file, "\n".join(lines))

    def read_patterns(self) -> Dict[str, str]:
        """Read all project patterns. Returns {key: content}."""
        patterns_file = self.project_memory_dir / "patterns.md"
        if not patterns_file.exists():
            return {}
        text = patterns_file.read_text()
        matches = _SECTION_RE.findall(text)
        return {k: v.strip() for k, v in matches}

    # ------------------------------------------------------------------
    # Layer 3: Global Memory — User Profile
    # ------------------------------------------------------------------

    PROFILE_FILE = "profile.md"

    def get_profile(self) -> Dict[str, str]:
        """Read the user profile. Returns {section_key: content}."""
        profile_path = self.global_memory_dir / "profile.md"
        if not profile_path.exists():
            return {}
        text = profile_path.read_text()
        matches = _SECTION_RE.findall(text)
        return {k: v.strip() for k, v in matches}

    def update_profile(self, key: str, content: str) -> None:
        """Update a section of the user profile."""
        self.ensure_dirs()
        key = _sanitize_key(key)
        profile = self.get_profile()
        profile[key] = content
        lines = []
        for k, v in sorted(profile.items()):
            lines.append(f"<!-- section: {k} -->\n{v}\n<!-- /section -->")
        self._write_file_atomic(self.global_memory_dir / "profile.md", "\n".join(lines))

    def observe_user_feedback(self, user_message: str, context: str = "") -> Optional[Dict[str, str]]:
        """Analyze a user message for profile-relevant signals.

        Detects patterns like:
        - "I prefer X" / "always do X" / "don't do X" → preferences
        - "I'm a backend developer" / "I work on..." → role
        - "Use pytest not unittest" / "We use TypeScript" → tech_stack

        Returns {key: content} if a profile update is warranted, None otherwise.
        This is a heuristic — PM should call this on user correction messages.
        """
        msg_lower = user_message.lower()

        # Detect preference signals
        pref_signals = ["i prefer", "always use", "don't use", "never use",
                        "i like", "i want", "please always", "from now on"]
        role_signals = ["i'm a", "i am a", "my role", "i work as", "i work on"]
        tech_signals = ["we use", "our stack", "this project uses", "tech stack"]

        result = None

        for signal in pref_signals:
            if signal in msg_lower:
                result = {"preferences": user_message}
                break

        if not result:
            for signal in role_signals:
                if signal in msg_lower:
                    result = {"role": user_message}
                    break

        if not result:
            for signal in tech_signals:
                if signal in msg_lower:
                    result = {"tech_stack": user_message}
                    break

        return result

    def get_worker_context(self) -> str:
        """Generate a context block from user profile to inject into worker prompts.
        Returns a formatted string suitable for inclusion in worker prompts."""
        profile = self.get_profile()
        if not profile:
            return ""

        lines = ["## User Profile (from PM memory)"]
        for key, content in sorted(profile.items()):
            lines.append(f"**{key}**: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Layer 3: Global Memory — Daily Activity Log
    # ------------------------------------------------------------------

    def log_activity(self, project_name: str, pm_session: str, summary: str,
                     tasks_completed: int = 0, models_used: Optional[List[str]] = None,
                     cost_usd: float = 0.0) -> None:
        """Append a daily activity entry. Called at PM session end."""
        self.ensure_dirs()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = {
            "date": today,
            "project": project_name,
            "pm_session": pm_session,
            "summary": summary,
            "tasks_completed": tasks_completed,
            "models_used": models_used or [],
            "cost_usd": cost_usd,
        }
        self._append_jsonl(self.global_memory_dir / "activity.jsonl", entry)

    def get_activity(self, days: int = 7) -> List[Dict[str, Any]]:
        """Read recent activity entries (last N days)."""
        entries = self._read_jsonl(self.global_memory_dir / "activity.jsonl")
        if not entries:
            return []
        # Filter to last N days
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        return [e for e in entries if e.get("date", "") >= cutoff]

    def get_activity_summary(self, days: int = 7) -> str:
        """Format recent activity as a human-readable summary."""
        entries = self.get_activity(days)
        if not entries:
            return "No activity in the last {} days.".format(days)
        lines = []
        current_date = ""
        for e in entries:
            date = e.get("date", "?")
            if date != current_date:
                current_date = date
                lines.append(f"\n### {date}")
            project = e.get("project", "?")
            summary = e.get("summary", "?")
            tasks = e.get("tasks_completed", 0)
            cost = e.get("cost_usd", 0)
            models = ", ".join(e.get("models_used", []))
            line = f"- **{project}**: {summary}"
            if tasks:
                line += f" ({tasks} tasks"
                if models:
                    line += f", {models}"
                if cost > 0:
                    line += f", ${cost:.2f}"
                line += ")"
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Layer 3: Global Memory — Preferences
    # ------------------------------------------------------------------

    def set_preference(self, key: str, value: Any) -> None:
        """Set a global user preference."""
        self.ensure_dirs()
        prefs = self.get_preferences()
        prefs[key] = value
        self._write_json(self.global_memory_dir / "preferences.json", prefs)


    def get_preferences(self) -> Dict[str, Any]:
        """Read all global user preferences."""
        pref_file = self.global_memory_dir / "preferences.json"
        if not pref_file.exists():
            return {}
        try:
            return json.loads(pref_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    # ------------------------------------------------------------------
    # Layer 3: Global Memory — Model Benchmarks
    # ------------------------------------------------------------------

    def update_model_benchmark(self, model: str, task_type: str,
                               success_rate: float, avg_cost: float) -> None:
        """Update global model benchmark data (aggregated across projects)."""
        self.ensure_dirs()
        benchmarks = self.get_model_benchmarks()
        key = f"{model}:{task_type}"
        benchmarks[key] = {
            "model": model,
            "task_type": task_type,
            "success_rate": success_rate,
            "avg_cost": avg_cost,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(self.global_memory_dir / "model-benchmarks.json", benchmarks)

    def get_model_benchmarks(self) -> Dict[str, Any]:
        """Read global model benchmarks."""
        bench_file = self.global_memory_dir / "model-benchmarks.json"
        if not bench_file.exists():
            return {}
        try:
            return json.loads(bench_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    # ------------------------------------------------------------------
    # Adaptive Routing — suggest_model based on history
    # ------------------------------------------------------------------

    def suggest_model(self, task_type: str, default_model: str = "claude-sonnet",
                      default_engine: str = "claude-agent") -> Dict[str, str]:
        """Suggest the best model for a task type based on routing history.

        Algorithm:
        1. Get all routing entries for this task_type
        2. If <3 entries, return default (not enough data)
        3. Among models with >=2 uses, pick the one with highest success_rate
        4. On tie, prefer lower avg_cost
        """
        history = self.get_routing_history(task_type=task_type, limit=100)
        if len(history) < 3:
            return {"model": default_model, "engine": default_engine,
                    "source": "default", "reason": "insufficient history"}

        # Aggregate per model
        model_data: Dict[str, Dict[str, Any]] = {}
        for e in history:
            m = e.get("model", "")
            if not m:
                continue
            if m not in model_data:
                model_data[m] = {"total": 0, "successes": 0, "total_cost": 0.0, "engine": e.get("engine", "")}
            model_data[m]["total"] += 1
            if e.get("success"):
                model_data[m]["successes"] += 1
            model_data[m]["total_cost"] += e.get("cost_usd", 0)

        # Filter models with >=2 uses
        candidates = {m: d for m, d in model_data.items() if d["total"] >= 2}
        if not candidates:
            return {"model": default_model, "engine": default_engine,
                    "source": "default", "reason": "no model has >=2 uses"}

        # Score: success_rate first, then lower cost breaks ties
        def score(item: Any) -> Any:
            m, d = item
            rate = d["successes"] / d["total"]
            avg_cost = d["total_cost"] / d["total"]
            return (-rate, avg_cost)  # negative rate for descending sort

        best_model, best_data = min(candidates.items(), key=score)
        rate = best_data["successes"] / best_data["total"]
        avg_cost = best_data["total_cost"] / best_data["total"]

        return {
            "model": best_model,
            "engine": best_data["engine"],
            "source": "history",
            "reason": f"{rate:.0%} success rate over {best_data['total']} uses, avg ${avg_cost:.2f}",
        }

    # ------------------------------------------------------------------
    # Claude Code Memory Sync
    # ------------------------------------------------------------------

    def sync_to_claude_code(self) -> int:
        """Write key project patterns to Claude Code's memory directory.
        Returns number of memories written."""
        cc_memory_dir = self._get_claude_code_memory_dir()
        if not cc_memory_dir:
            return 0
        cc_memory_dir.mkdir(parents=True, exist_ok=True)

        patterns = self.read_patterns()
        count = 0
        for key, content in patterns.items():
            safe_key = _sanitize_key(key)
            memory_file = cc_memory_dir / f"aya-{safe_key}.md"
            frontmatter = (
                f"---\n"
                f"name: aya-{safe_key}\n"
                f"description: \"AYA learned pattern: {safe_key}\"\n"
                f"metadata:\n"
                f"  type: project\n"
                f"---\n\n"
            )
            self._write_file_atomic(memory_file, frontmatter + content + "\n")
            count += 1

        # Update MEMORY.md index
        memory_index = cc_memory_dir / "MEMORY.md"
        existing_lines = []
        if memory_index.exists():
            existing_lines = [l for l in memory_index.read_text().splitlines()
                              if not l.strip().startswith("- [aya-")]
        for key in sorted(patterns.keys()):
            safe_key = _sanitize_key(key)
            existing_lines.append(f"- [aya-{safe_key}](aya-{safe_key}.md) — AYA learned: {safe_key}")
        self._write_file_atomic(memory_index, "\n".join(existing_lines) + "\n")

        return count

    def read_claude_code_memory(self) -> Dict[str, str]:
        """Read relevant memories from Claude Code's memory directory."""
        cc_memory_dir = self._get_claude_code_memory_dir()
        if not cc_memory_dir or not cc_memory_dir.exists():
            return {}
        memories = {}
        for f in cc_memory_dir.glob("*.md"):
            if f.name == "MEMORY.md":
                continue
            content = f.read_text()
            # Skip frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            memories[f.stem] = content
        return memories

    def _get_claude_code_memory_dir(self) -> Optional[Path]:
        """Get the Claude Code memory directory for this project."""
        # Claude Code uses the project path encoded in the directory name
        cc_home = Path.home() / ".claude"
        if not cc_home.exists():
            return None
        projects_dir = cc_home / "projects"
        if not projects_dir.exists():
            return None
        # CC encodes the path: /home/user/project → -home-user-project
        encoded = str(self.project_dir).replace("/", "-").lstrip("-")
        cc_project_dir = projects_dir / encoded / "memory"
        return cc_project_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_file_atomic(self, path: Path, content: str) -> None:
        """Write content to file atomically via tmp + os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content)
        os.replace(str(tmp), str(path))

    def _append_jsonl(self, path: Path, data: Dict[str, Any]) -> None:
        """Append a JSON line to a file with file locking."""
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(data, ensure_ascii=False) + "\n"
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            fcntl.flock(f, fcntl.LOCK_UN)

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        """Read all lines from a JSONL file."""
        if not path.exists():
            return []
        result = []
        try:
            for line in path.read_text().strip().splitlines():
                if line.strip():
                    result.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass
        return result

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Write JSON to a file atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(str(tmp), str(path))
