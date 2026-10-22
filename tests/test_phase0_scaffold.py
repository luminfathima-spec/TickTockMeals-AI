"""Unit tests for Phase 0: Scaffold, Config, SimClock, and LLM Client Shell."""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from pydantic import BaseModel, Field

from app.clock import SimClock
from app.config import AppConfig, load_config
from app.llm.client import DiskCache, LLMClient, RateLimiter


# Sample schema for structured testing
class SampleFoodItem(BaseModel):
    name: str = Field(default="test_food")
    servings: int = Field(default=10)
    is_safe: bool = Field(default=True)


def test_config_loads_valid(tmp_path: Path):
    """Config loads cleanly from standard config.yaml."""
    config = load_config("config.yaml")
    assert isinstance(config, AppConfig)
    assert config.llm.mode in ("mock", "live")
    assert config.llm.models.MODEL_FAST
    assert config.llm.models.MODEL_VISION
    assert config.llm.models.MODEL_EMBED
    assert config.avg_speed_kmph.bike > 0
    assert config.avg_speed_kmph.car > 0
    assert config.impact_factors.kg_per_meal > 0


def test_config_rejects_missing_required_field(tmp_path: Path):
    """Config loader raises clear error when a required field is missing."""
    invalid_yaml = tmp_path / "invalid_config.yaml"
    # Missing 'models' section entirely
    invalid_yaml.write_text(
        """
llm:
  mode: mock
  rate_limit_per_minute: 10
db_path: "data/test.db"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Configuration validation failed"):
        load_config(config_path=invalid_yaml)


def test_config_env_override(tmp_path: Path, monkeypatch):
    """Environment variable LLM_MODE overrides config.yaml setting."""
    monkeypatch.setenv("LLM_MODE", "live")
    config = load_config("config.yaml")
    assert config.llm.mode == "live"

    monkeypatch.setenv("LLM_MODE", "mock")
    config_mock = load_config("config.yaml")
    assert config_mock.llm.mode == "mock"


def test_clock_advance_and_reset():
    """SimClock advance, set_fixed, and reset work as expected."""
    clock = SimClock()
    base_time = datetime(2026, 10, 22, 12, 0, 0)
    clock.set_fixed(base_time)
    assert clock.now() == base_time

    # Advance 45 minutes
    t1 = clock.advance(45)
    assert t1 == base_time + timedelta(minutes=45)
    assert clock.now() == t1

    # Advance another 75 minutes
    t2 = clock.advance(75)
    assert t2 == base_time + timedelta(minutes=120)

    # Reset resets to real current system time
    now_real = datetime.now()
    t_reset = clock.reset()
    assert abs((t_reset - now_real).total_seconds()) < 2.0


def test_mock_client_deterministic_and_no_network(tmp_path: Path, monkeypatch):
    """Mock client produces deterministic outputs and NEVER touches the network."""
    # Monkeypatch socket to ensure offline safety
    def mock_socket_call(*args, **kwargs):
        raise RuntimeError("Network access attempted in MOCK mode!")

    monkeypatch.setattr(socket, "socket", mock_socket_call)

    # Ensure mock mode with isolated cache dir
    cache = DiskCache(cache_dir=tmp_path / "cache")
    config = load_config("config.yaml")
    config.llm.mode = "mock"

    client = LLMClient(config=config, cache=cache)

    # 1. Text generation determinism
    res1 = client.generate_text("List surplus food guidelines")
    res2 = client.generate_text("List surplus food guidelines")
    assert res1 == res2
    assert "MOCK:" in res1

    # 2. Embedding determinism
    vec1 = client.embed("Khara bath and sambar")
    vec2 = client.embed("Khara bath and sambar")
    assert len(vec1) == 768
    assert vec1 == vec2

    # 3. Structured output determinism
    struct1 = client.generate_structured("Extract food", schema=SampleFoodItem)
    struct2 = client.generate_structured("Extract food", schema=SampleFoodItem)
    assert isinstance(struct1, SampleFoodItem)
    assert struct1.name == struct2.name

    # Check counters
    assert client.counters.live_calls == 0
    assert client.counters.failed_calls == 0
    assert client.counters.mock_calls >= 1


def test_cache_returns_same_result_without_second_call(tmp_path: Path):
    """DiskCache returns the same result on second call, avoiding recomputation."""
    cache = DiskCache(cache_dir=tmp_path / "test_cache")
    config = load_config("config.yaml")
    config.llm.mode = "mock"

    client = LLMClient(config=config, cache=cache)

    prompt = "Check vegetable cutlet shelf life"
    # First call: executes mock generator and writes to cache
    first_res = client.generate_text(prompt)
    assert client.counters.mock_calls == 1
    assert client.counters.cached_calls == 0

    # Second call: served directly from disk cache
    second_res = client.generate_text(prompt)
    assert second_res == first_res
    assert client.counters.mock_calls == 1
    assert client.counters.cached_calls == 1


def test_rate_limiter_delays_correctly():
    """RateLimiter pauses when the call budget per minute is reached."""
    fake_time = [100.0]
    sleep_calls = []

    def fake_now() -> float:
        return fake_time[0]

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        fake_time[0] += secs

    # 3 calls per minute
    limiter = RateLimiter(
        calls_per_minute=3,
        time_func=fake_now,
        sleep_func=fake_sleep,
    )

    # First 3 calls immediate
    d1 = limiter.acquire()
    d2 = limiter.acquire()
    d3 = limiter.acquire()
    assert d1 == 0.0 and d2 == 0.0 and d3 == 0.0
    assert len(sleep_calls) == 0

    # 4th call must delay until 60 seconds after call 1
    d4 = limiter.acquire()
    assert d4 == 60.0
    assert len(sleep_calls) == 1
    assert fake_time[0] == 160.0


def test_no_hardcoded_model_names_in_app():
    """Verify Rule 4: No model names are hardcoded in application code."""
    app_dir = Path("app")
    python_files = list(app_dir.rglob("*.py"))

    forbidden_patterns = [
        "gemini-1.5",
        "gemini-2.0",
        "gemini-2.5",
        "text-embedding-",
    ]

    for py_file in python_files:
        content = py_file.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert pattern not in content, (
                f"Rule 4 violation in {py_file}: model name '{pattern}' hardcoded in code!"
            )
