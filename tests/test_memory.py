"""
Comprehensive tests for AyaMemory — three-layer persistent memory system.
"""
import json
from pathlib import Path

import pytest

from aya.memory import AyaMemory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Create AyaMemory with redirected AYA_HOME."""
    monkeypatch.setattr("aya.memory.AYA_HOME", tmp_path / "aya-home")
    m = AyaMemory(str(tmp_path / "project"))
    m.ensure_dirs()
    return m


# ---------------------------------------------------------------------------
# Layer 2 — Routing History
# ---------------------------------------------------------------------------

class TestRoutingHistory:
    def test_log_and_read(self, mem):
        mem.log_routing("t-001", "implementation", "deepseek-v4-pro", "claude-cli",
                        success=True, cost_usd=0.01, turns=5, duration_ms=1200)
        history = mem.get_routing_history()
        assert len(history) == 1
        e = history[0]
        assert e["task_id"] == "t-001"
        assert e["task_type"] == "implementation"
        assert e["model"] == "deepseek-v4-pro"
        assert e["engine"] == "claude-cli"
        assert e["success"] is True
        assert e["cost_usd"] == pytest.approx(0.01)
        assert e["turns"] == 5
        assert e["duration_ms"] == 1200
        assert "ts" in e

    def test_multiple_entries(self, mem):
        for i in range(5):
            mem.log_routing(f"t-{i:03d}", "testing", "gpt-5.5", "codex",
                            success=True, cost_usd=0.005)
        history = mem.get_routing_history()
        assert len(history) == 5

    def test_filter_by_task_type(self, mem):
        mem.log_routing("t-001", "implementation", "deepseek", "claude-cli", success=True)
        mem.log_routing("t-002", "architecture", "claude-opus", "claude-agent", success=True)
        mem.log_routing("t-003", "testing", "gpt-5.5", "codex", success=False)

        impl = mem.get_routing_history(task_type="implementation")
        assert len(impl) == 1
        assert impl[0]["task_id"] == "t-001"

        arch = mem.get_routing_history(task_type="architecture")
        assert len(arch) == 1
        assert arch[0]["model"] == "claude-opus"

        testing = mem.get_routing_history(task_type="testing")
        assert len(testing) == 1

    def test_limit(self, mem):
        for i in range(100):
            mem.log_routing(f"t-{i:03d}", "implementation", "model-A", "engine-A",
                            success=True)
        history = mem.get_routing_history(limit=10)
        assert len(history) == 10
        # Should be the *last* 10 entries
        assert history[-1]["task_id"] == "t-099"
        assert history[0]["task_id"] == "t-090"

    def test_append_only(self, mem):
        """Each log_routing call grows the file, not overwrites it."""
        mem.log_routing("t-001", "implementation", "deepseek", "cli", success=True)
        jsonl_file = mem.project_memory_dir / "routing-history.jsonl"
        size_after_one = jsonl_file.stat().st_size

        mem.log_routing("t-002", "implementation", "deepseek", "cli", success=False)
        size_after_two = jsonl_file.stat().st_size

        assert size_after_two > size_after_one
        # Both entries must still be present
        history = mem.get_routing_history()
        assert len(history) == 2

    def test_empty_history(self, mem):
        history = mem.get_routing_history()
        assert history == []


# ---------------------------------------------------------------------------
# Layer 2 — Model Stats
# ---------------------------------------------------------------------------

class TestModelStats:
    def test_stats_aggregation(self, mem):
        # 3 successes + 1 failure for model-A → 75% success rate
        for _ in range(3):
            mem.log_routing("t-ok", "impl", "model-A", "engine", success=True, cost_usd=0.10)
        mem.log_routing("t-fail", "impl", "model-A", "engine", success=False, cost_usd=0.10)

        stats = mem.get_model_stats()
        assert "model-A" in stats
        s = stats["model-A"]
        assert s["total"] == 4
        assert s["successes"] == 3
        assert s["failures"] == 1
        assert s["success_rate"] == pytest.approx(0.75)

    def test_multiple_models(self, mem):
        for model in ("alpha", "beta", "gamma"):
            mem.log_routing("t-x", "impl", model, "engine", success=True, cost_usd=0.05)
        stats = mem.get_model_stats()
        assert set(stats.keys()) == {"alpha", "beta", "gamma"}
        for s in stats.values():
            assert s["total"] == 1
            assert s["success_rate"] == pytest.approx(1.0)

    def test_avg_cost_calculation(self, mem):
        costs = [0.10, 0.20, 0.30]
        for c in costs:
            mem.log_routing("t-x", "impl", "model-B", "engine", success=True, cost_usd=c)
        stats = mem.get_model_stats()
        assert stats["model-B"]["avg_cost"] == pytest.approx(sum(costs) / len(costs))

    def test_empty_stats(self, mem):
        stats = mem.get_model_stats()
        assert stats == {}


# ---------------------------------------------------------------------------
# Layer 2 — Failures
# ---------------------------------------------------------------------------

class TestFailures:
    def test_log_and_read_failure(self, mem):
        mem.log_failure("t-001", "deepseek-v4-pro", "implementation",
                        "Timeout after 30s", resolution="Retry with shorter prompt")
        failures = mem.get_failures()
        assert len(failures) == 1
        f = failures[0]
        assert f["task_id"] == "t-001"
        assert f["model"] == "deepseek-v4-pro"
        assert f["task_type"] == "implementation"
        assert f["error_summary"] == "Timeout after 30s"
        assert f["resolution"] == "Retry with shorter prompt"

    def test_filter_by_task_type(self, mem):
        mem.log_failure("t-001", "model-A", "implementation", "err1")
        mem.log_failure("t-002", "model-B", "testing", "err2")
        mem.log_failure("t-003", "model-C", "implementation", "err3")

        impl_failures = mem.get_failures(task_type="implementation")
        assert len(impl_failures) == 2
        assert all(f["task_type"] == "implementation" for f in impl_failures)

        test_failures = mem.get_failures(task_type="testing")
        assert len(test_failures) == 1
        assert test_failures[0]["task_id"] == "t-002"

    def test_failure_fields(self, mem):
        mem.log_failure("t-999", "claude-sonnet", "architecture", "OOM error",
                        resolution="Split task")
        failures = mem.get_failures()
        f = failures[0]
        for field in ("ts", "task_id", "model", "task_type", "error_summary", "resolution"):
            assert field in f, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Layer 2 — Patterns
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_write_and_read(self, mem):
        mem.write_pattern("auth-flow", "Always use JWT with 24h expiry.")
        patterns = mem.read_patterns()
        assert "auth-flow" in patterns
        assert patterns["auth-flow"] == "Always use JWT with 24h expiry."

    def test_multiple_patterns(self, mem):
        mem.write_pattern("key-A", "Value A")
        mem.write_pattern("key-B", "Value B")
        mem.write_pattern("key-C", "Value C")
        patterns = mem.read_patterns()
        assert len(patterns) == 3
        assert patterns["key-A"] == "Value A"
        assert patterns["key-B"] == "Value B"
        assert patterns["key-C"] == "Value C"

    def test_overwrite_pattern(self, mem):
        mem.write_pattern("my-key", "first")
        mem.write_pattern("my-key", "second")
        patterns = mem.read_patterns()
        assert patterns["my-key"] == "second"
        assert len(patterns) == 1

    def test_empty_patterns(self, mem):
        patterns = mem.read_patterns()
        assert patterns == {}


# ---------------------------------------------------------------------------
# Layer 3 — Preferences
# ---------------------------------------------------------------------------

class TestPreferences:
    def test_set_and_get(self, mem):
        mem.set_preference("default_engine", "claude-agent")
        prefs = mem.get_preferences()
        assert prefs["default_engine"] == "claude-agent"

    def test_multiple_preferences(self, mem):
        mem.set_preference("pref-A", "val-A")
        mem.set_preference("pref-B", 42)
        mem.set_preference("pref-C", True)
        prefs = mem.get_preferences()
        assert prefs["pref-A"] == "val-A"
        assert prefs["pref-B"] == 42
        assert prefs["pref-C"] is True

    def test_overwrite(self, mem):
        mem.set_preference("timeout", 30)
        mem.set_preference("timeout", 60)
        prefs = mem.get_preferences()
        assert prefs["timeout"] == 60
        # Only one entry for this key
        assert len([k for k in prefs if k == "timeout"]) == 1

    def test_empty(self, mem):
        prefs = mem.get_preferences()
        assert prefs == {}


# ---------------------------------------------------------------------------
# Layer 3 — Model Benchmarks
# ---------------------------------------------------------------------------

class TestModelBenchmarks:
    def test_update_and_read(self, mem):
        mem.update_model_benchmark("deepseek-v4-pro", "implementation",
                                   success_rate=0.92, avg_cost=0.05)
        benchmarks = mem.get_model_benchmarks()
        key = "deepseek-v4-pro:implementation"
        assert key in benchmarks
        b = benchmarks[key]
        assert b["model"] == "deepseek-v4-pro"
        assert b["task_type"] == "implementation"
        assert b["success_rate"] == pytest.approx(0.92)
        assert b["avg_cost"] == pytest.approx(0.05)
        assert "updated_at" in b

    def test_multiple_benchmarks(self, mem):
        pairs = [
            ("claude-opus", "architecture"),
            ("gpt-5.5", "testing"),
            ("deepseek-v4-pro", "implementation"),
        ]
        for model, task_type in pairs:
            mem.update_model_benchmark(model, task_type, success_rate=0.80, avg_cost=0.10)
        benchmarks = mem.get_model_benchmarks()
        for model, task_type in pairs:
            key = f"{model}:{task_type}"
            assert key in benchmarks


# ---------------------------------------------------------------------------
# suggest_model
# ---------------------------------------------------------------------------

class TestSuggestModel:
    def test_insufficient_history(self, mem):
        # Only 2 entries → not enough data
        mem.log_routing("t-001", "implementation", "deepseek", "cli", success=True)
        mem.log_routing("t-002", "implementation", "deepseek", "cli", success=True)
        result = mem.suggest_model("implementation", default_model="claude-sonnet",
                                   default_engine="claude-agent")
        assert result["source"] == "default"
        assert result["model"] == "claude-sonnet"

    def test_suggests_best_model(self, mem):
        # deepseek: 5 successes; sonnet: 2 failures (out of 3 total for task_type)
        for _ in range(5):
            mem.log_routing("t-ok", "implementation", "deepseek", "cli", success=True, cost_usd=0.02)
        for _ in range(2):
            mem.log_routing("t-fail", "implementation", "sonnet", "agent", success=False, cost_usd=0.10)
        mem.log_routing("t-ok2", "implementation", "sonnet", "agent", success=True, cost_usd=0.10)

        result = mem.suggest_model("implementation")
        assert result["source"] == "history"
        assert result["model"] == "deepseek"

    def test_requires_minimum_uses(self, mem):
        # model-rare has 100% success but only 1 use → excluded
        # model-common has 80% (4/5) success with 5 uses → should be chosen
        mem.log_routing("t-r", "impl", "model-rare", "engine", success=True, cost_usd=0.01)
        for i in range(4):
            mem.log_routing(f"t-c{i}", "impl", "model-common", "engine", success=True, cost_usd=0.02)
        mem.log_routing("t-cf", "impl", "model-common", "engine", success=False, cost_usd=0.02)

        result = mem.suggest_model("impl")
        # model-rare is excluded (only 1 use < 2 threshold)
        assert result["model"] == "model-common"

    def test_breaks_tie_by_cost(self, mem):
        # model-cheap and model-expensive both have 100% success rate (2 uses each)
        # model-cheap should win
        for _ in range(2):
            mem.log_routing("tx", "impl", "model-cheap", "engine", success=True, cost_usd=0.01)
        for _ in range(2):
            mem.log_routing("ty", "impl", "model-expensive", "engine", success=True, cost_usd=0.50)

        result = mem.suggest_model("impl")
        assert result["source"] == "history"
        assert result["model"] == "model-cheap"

    def test_single_model_always_suggested(self, mem):
        # Only one model with >=2 uses
        for _ in range(3):
            mem.log_routing("t-x", "impl", "only-model", "engine", success=True, cost_usd=0.05)

        result = mem.suggest_model("impl")
        assert result["source"] == "history"
        assert result["model"] == "only-model"


# ---------------------------------------------------------------------------
# Claude Code Memory Sync
# ---------------------------------------------------------------------------

class TestClaudeCodeSync:
    def _fake_cc_dir(self, mem, tmp_path, monkeypatch):
        """Patch _get_claude_code_memory_dir to return a temp path."""
        cc_memory = tmp_path / "claude-code-memory"
        monkeypatch.setattr(mem, "_get_claude_code_memory_dir", lambda: cc_memory)
        return cc_memory

    def test_sync_creates_files(self, mem, tmp_path, monkeypatch):
        cc_memory = self._fake_cc_dir(mem, tmp_path, monkeypatch)
        mem.write_pattern("auth-pattern", "Use JWT tokens.")
        mem.write_pattern("db-pattern", "Always use transactions.")

        count = mem.sync_to_claude_code()
        assert count == 2
        assert (cc_memory / "aya-auth-pattern.md").exists()
        assert (cc_memory / "aya-db-pattern.md").exists()

        content = (cc_memory / "aya-auth-pattern.md").read_text()
        assert "Use JWT tokens." in content

    def test_sync_updates_memory_index(self, mem, tmp_path, monkeypatch):
        cc_memory = self._fake_cc_dir(mem, tmp_path, monkeypatch)
        mem.write_pattern("cache-strategy", "Use Redis for caching.")
        mem.sync_to_claude_code()

        memory_index = cc_memory / "MEMORY.md"
        assert memory_index.exists()
        index_text = memory_index.read_text()
        assert "aya-cache-strategy" in index_text

    def test_read_cc_memory(self, mem, tmp_path, monkeypatch):
        cc_memory = self._fake_cc_dir(mem, tmp_path, monkeypatch)
        cc_memory.mkdir(parents=True, exist_ok=True)

        # Write a file manually to the CC memory dir
        test_file = cc_memory / "my-note.md"
        test_file.write_text("---\nname: my-note\n---\n\nSome useful note here.")

        memories = mem.read_claude_code_memory()
        assert "my-note" in memories
        assert "Some useful note here." in memories["my-note"]

    def test_sync_with_no_cc_dir(self, mem, monkeypatch):
        # _get_claude_code_memory_dir returns None when ~/.claude doesn't exist
        monkeypatch.setattr(mem, "_get_claude_code_memory_dir", lambda: None)
        mem.write_pattern("some-key", "some content")

        count = mem.sync_to_claude_code()
        assert count == 0


# ---------------------------------------------------------------------------
# Layer 3 — User Profile
# ---------------------------------------------------------------------------

class TestProfile:
    def test_empty_profile(self, mem):
        assert mem.get_profile() == {}

    def test_update_and_read(self, mem):
        mem.update_profile("role", "Backend developer")
        profile = mem.get_profile()
        assert profile["role"] == "Backend developer"

    def test_multiple_sections(self, mem):
        mem.update_profile("role", "Dev")
        mem.update_profile("tech_stack", "Python, Go")
        mem.update_profile("preferences", "Pytest always")
        profile = mem.get_profile()
        assert len(profile) == 3
        assert "Python, Go" in profile["tech_stack"]

    def test_overwrite_section(self, mem):
        mem.update_profile("role", "Junior dev")
        mem.update_profile("role", "Senior dev")
        assert mem.get_profile()["role"] == "Senior dev"

    def test_multiline_content(self, mem):
        mem.update_profile("preferences", "Line 1\nLine 2\nLine 3")
        assert "Line 2" in mem.get_profile()["preferences"]


class TestObserveUserFeedback:
    def test_detects_preference(self, mem):
        result = mem.observe_user_feedback("I prefer small commits")
        assert result is not None
        assert "preferences" in result

    def test_detects_role(self, mem):
        result = mem.observe_user_feedback("I'm a data scientist")
        assert result is not None
        assert "role" in result

    def test_detects_tech_stack(self, mem):
        result = mem.observe_user_feedback("We use PostgreSQL and Redis")
        assert result is not None
        assert "tech_stack" in result

    def test_no_signal(self, mem):
        result = mem.observe_user_feedback("What's the status of task-001?")
        assert result is None

    def test_case_insensitive(self, mem):
        result = mem.observe_user_feedback("I PREFER typescript over javascript")
        assert result is not None


class TestWorkerContext:
    def test_empty_profile_returns_empty(self, mem):
        assert mem.get_worker_context() == ""

    def test_formats_profile(self, mem):
        mem.update_profile("role", "Backend dev")
        mem.update_profile("preferences", "Always write tests")
        ctx = mem.get_worker_context()
        assert "User Profile" in ctx
        assert "Backend dev" in ctx
        assert "Always write tests" in ctx

    def test_all_keys_present(self, mem):
        mem.update_profile("a", "val_a")
        mem.update_profile("b", "val_b")
        ctx = mem.get_worker_context()
        assert "**a**" in ctx
        assert "**b**" in ctx
