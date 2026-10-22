"""Unified LLM client module for TickTock Meals.

Handles all LLM interactions with:
- Retry with exponential backoff (tenacity)
- Per-minute rate limiter
- On-disk response cache keyed by SHA-256 of (model, prompt, image bytes)
- Call counters
- Deterministic MOCK mode for offline execution and testing
- Isolated live mode via google-genai
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union
import numpy as np
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import AppConfig, get_config

T = TypeVar("T", bound=BaseModel)


class RateLimiter:
    """Sliding-window per-minute rate limiter."""

    def __init__(
        self,
        calls_per_minute: int = 10,
        time_func: Optional[Callable[[], float]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ) -> None:
        import time

        self.calls_per_minute = calls_per_minute
        self.time_func = time_func or time.monotonic
        self.sleep_func = sleep_func or time.sleep
        self.timestamps: List[float] = []

    def acquire(self) -> float:
        """Enforce rate limit. Returns elapsed sleep delay (seconds)."""
        now = self.time_func()
        # Evict timestamps older than 60 seconds
        self.timestamps = [t for t in self.timestamps if now - t < 60.0]

        delay = 0.0
        if len(self.timestamps) >= self.calls_per_minute:
            oldest = self.timestamps[0]
            wait_time = 60.0 - (now - oldest)
            if wait_time > 0:
                delay = wait_time
                self.sleep_func(wait_time)
                now = self.time_func()
                self.timestamps = [t for t in self.timestamps if now - t < 60.0]

        self.timestamps.append(now)
        return delay


class DiskCache:
    """Disk-backed JSON cache for LLM responses."""

    def __init__(self, cache_dir: Union[str, Path] = ".cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(
        model: str,
        prompt: str,
        images_bytes: Optional[List[bytes]] = None,
        extra: str = "",
    ) -> str:
        """Compute deterministic SHA-256 cache key."""
        hasher = hashlib.sha256()
        hasher.update(model.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(prompt.encode("utf-8"))
        hasher.update(b"::")
        hasher.update(extra.encode("utf-8"))
        if images_bytes:
            for b in images_bytes:
                hasher.update(b"::img::")
                hasher.update(hashlib.sha256(b).digest())
        return hasher.hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Fetch cached payload if file exists."""
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.is_file():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def set(self, key: str, data: Any) -> None:
        """Store payload to disk cache."""
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except OSError:
            pass


@dataclass
class CallCounters:
    """Tracks LLM usage across execution."""
    total_calls: int = 0
    live_calls: int = 0
    mock_calls: int = 0
    cached_calls: int = 0
    failed_calls: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "total_calls": self.total_calls,
            "live_calls": self.live_calls,
            "mock_calls": self.mock_calls,
            "cached_calls": self.cached_calls,
            "failed_calls": self.failed_calls,
        }

    def reset(self) -> None:
        self.total_calls = 0
        self.live_calls = 0
        self.mock_calls = 0
        self.cached_calls = 0
        self.failed_calls = 0


def _build_mock_structured(schema: Type[T], seed_text: str = "") -> T:
    """Construct a deterministic valid instance of schema for mock fallback."""
    seed_int = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:4], "big")
    mock_data: Dict[str, Any] = {}

    for field_name, field_info in schema.model_fields.items():
        if field_info.default is not None and field_info.default is not ...:
            mock_data[field_name] = field_info.default
            continue

        ann = field_info.annotation
        # Simple heuristic fallback based on annotation type
        if ann is str or ann == "str" or getattr(ann, "__name__", "") == "str":
            mock_data[field_name] = f"mock_{field_name}_{seed_int % 100}"
        elif ann is int or ann == "int" or getattr(ann, "__name__", "") == "int":
            mock_data[field_name] = (seed_int % 50) + 1
        elif ann is float or ann == "float" or getattr(ann, "__name__", "") == "float":
            mock_data[field_name] = round(0.5 + (seed_int % 50) / 100.0, 2)
        elif ann is bool or ann == "bool" or getattr(ann, "__name__", "") == "bool":
            mock_data[field_name] = True
        elif getattr(ann, "__origin__", None) is list:
            mock_data[field_name] = []
        elif getattr(ann, "__origin__", None) is dict:
            mock_data[field_name] = {}
        else:
            mock_data[field_name] = None

    try:
        return schema.model_validate(mock_data)
    except Exception:
        # Fall back to schema default or blank validation
        try:
            return schema.model_construct(**mock_data)
        except Exception:
            raise ValueError(f"Could not construct mock instance of schema {schema}")


def _pseudo_embed(text: str, dim: int = 768) -> List[float]:
    """Generate a deterministic keyword-aware unit-length pseudo-embedding for mock mode."""
    import re
    vec = np.zeros(dim, dtype=np.float32)
    tokens = re.findall(r"\w+", text.lower())
    for token in tokens:
        # Hash token to a bucket
        token_hash = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
        idx = token_hash % dim
        vec[idx] += 1.0
        # Character trigrams for subword robustness
        if len(token) >= 3:
            for i in range(len(token) - 2):
                ngram = token[i : i + 3]
                ng_hash = int(hashlib.md5(ngram.encode("utf-8")).hexdigest()[:8], 16)
                vec[ng_hash % dim] += 0.35

    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    else:
        vec[0] = 1.0
    return vec.tolist()


class LLMClient:
    """Unified client providing cached, rate-limited access to Gemini or mock stubs."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        rate_limiter: Optional[RateLimiter] = None,
        cache: Optional[DiskCache] = None,
    ) -> None:
        self.config = config or get_config()
        self.cache = cache or DiskCache(cache_dir=self.config.llm.cache_dir)
        self.rate_limiter = rate_limiter or RateLimiter(
            calls_per_minute=self.config.llm.rate_limit_per_minute
        )
        self.counters = CallCounters()
        self._genai_client = None

    @property
    def mode(self) -> str:
        return self.config.llm.mode

    def _get_genai_client(self) -> Any:
        """Lazily initialize google-genai client."""
        if self._genai_client is None:
            if not self.config.gemini_api_key:
                raise ValueError("GEMINI_API_KEY is not configured for live LLM mode.")
            from google import genai
            self._genai_client = genai.Client(api_key=self.config.gemini_api_key)
        return self._genai_client

    def _lookup_fixture(self, key: str, fixture_name: Optional[str] = None) -> Optional[Any]:
        """Check fixture locations for pre-recorded response."""
        candidates = []
        if fixture_name:
            candidates.extend([
                Path("tests/fixtures") / f"{fixture_name}.json",
                Path("fixtures") / f"{fixture_name}.json",
                Path(fixture_name),
            ])
        candidates.extend([
            Path("tests/fixtures") / f"{key}.json",
            Path("fixtures") / f"{key}.json",
        ])

        for c in candidates:
            if c.is_file():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return None

    def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        fixture_name: Optional[str] = None,
    ) -> str:
        """Generate text from a prompt, honoring mock mode, cache, and rate limit."""
        target_model = model or self.config.llm.models.MODEL_FAST
        self.counters.total_calls += 1

        cache_key = self.cache.compute_key(
            model=target_model,
            prompt=prompt,
            extra=system_instruction or "",
        )

        cached = self.cache.get(cache_key)
        if cached is not None and isinstance(cached, dict) and "text" in cached:
            self.counters.cached_calls += 1
            return cached["text"]

        # Enforce rate limiter in live mode
        if self.mode != "mock":
            self.rate_limiter.acquire()

        if self.mode == "mock":
            self.counters.mock_calls += 1
            fixture_data = self._lookup_fixture(cache_key, fixture_name)
            if fixture_data and isinstance(fixture_data, dict) and "text" in fixture_data:
                result = fixture_data["text"]
            elif fixture_data and isinstance(fixture_data, str):
                result = fixture_data
            else:
                prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
                result = f"[MOCK:{prompt_hash}] Response for prompt: {prompt[:60]}..."

            self.cache.set(cache_key, {"text": result})
            return result

        # Live Mode
        return self._live_generate_text(
            prompt=prompt,
            model=target_model,
            system_instruction=system_instruction,
            cache_key=cache_key,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _live_generate_text(
        self,
        prompt: str,
        model: str,
        system_instruction: Optional[str],
        cache_key: str,
    ) -> str:
        try:
            client = self._get_genai_client()
            config_kwargs: Dict[str, Any] = {}
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction

            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config_kwargs or None,
            )
            text_result = response.text or ""
            self.counters.live_calls += 1
            self.cache.set(cache_key, {"text": text_result})
            return text_result
        except Exception:
            self.counters.failed_calls += 1
            raise

    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        fixture_name: Optional[str] = None,
    ) -> T:
        """Generate structured Pydantic object, validated against schema."""
        target_model = model or self.config.llm.models.MODEL_FAST
        self.counters.total_calls += 1

        schema_name = getattr(schema, "__name__", "Model")
        cache_key = self.cache.compute_key(
            model=target_model,
            prompt=prompt,
            extra=f"{system_instruction or ''}::{schema_name}",
        )

        cached = self.cache.get(cache_key)
        if cached is not None and isinstance(cached, dict) and "data" in cached:
            self.counters.cached_calls += 1
            return schema.model_validate(cached["data"])

        if self.mode != "mock":
            self.rate_limiter.acquire()

        if self.mode == "mock":
            self.counters.mock_calls += 1
            fixture_data = self._lookup_fixture(cache_key, fixture_name)
            if fixture_data is not None:
                if isinstance(fixture_data, dict) and "data" in fixture_data:
                    data = fixture_data["data"]
                else:
                    data = fixture_data
                parsed = schema.model_validate(data)
            else:
                parsed = _build_mock_structured(schema, seed_text=prompt)

            self.cache.set(cache_key, {"data": parsed.model_dump()})
            return parsed

        # Live Mode
        return self._live_generate_structured(
            prompt=prompt,
            schema=schema,
            model=target_model,
            system_instruction=system_instruction,
            cache_key=cache_key,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _live_generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        model: str,
        system_instruction: Optional[str],
        cache_key: str,
    ) -> T:
        try:
            client = self._get_genai_client()
            config_kwargs: Dict[str, Any] = {
                "response_mime_type": "application/json",
                "response_schema": schema,
            }
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction

            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config_kwargs,
            )
            text_result = response.text or "{}"
            parsed = schema.model_validate_json(text_result)
            self.counters.live_calls += 1
            self.cache.set(cache_key, {"data": parsed.model_dump()})
            return parsed
        except Exception:
            self.counters.failed_calls += 1
            raise

    def generate_with_images(
        self,
        prompt: str,
        images: List[Union[bytes, Path, str]],
        schema: Optional[Type[T]] = None,
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        fixture_name: Optional[str] = None,
    ) -> Any:
        """Process multimodal vision queries with 1 or more images."""
        target_model = model or self.config.llm.models.MODEL_VISION
        self.counters.total_calls += 1

        # Read image bytes for cache key computation
        raw_bytes_list: List[bytes] = []
        for img in images:
            if isinstance(img, bytes):
                raw_bytes_list.append(img)
            elif isinstance(img, (Path, str)):
                p = Path(img)
                if p.is_file():
                    with open(p, "rb") as f:
                        raw_bytes_list.append(f.read())
                else:
                    raw_bytes_list.append(str(p).encode("utf-8"))

        extra_tag = f"{system_instruction or ''}::{getattr(schema, '__name__', 'none')}"
        cache_key = self.cache.compute_key(
            model=target_model,
            prompt=prompt,
            images_bytes=raw_bytes_list,
            extra=extra_tag,
        )

        cached = self.cache.get(cache_key)
        if cached is not None and isinstance(cached, dict):
            self.counters.cached_calls += 1
            if schema and "data" in cached:
                return schema.model_validate(cached["data"])
            elif "text" in cached:
                return cached["text"]

        if self.mode != "mock":
            self.rate_limiter.acquire()

        if self.mode == "mock":
            self.counters.mock_calls += 1
            fixture_data = self._lookup_fixture(cache_key, fixture_name)
            if fixture_data is not None:
                if schema:
                    data = fixture_data.get("data", fixture_data) if isinstance(fixture_data, dict) else fixture_data
                    result = schema.model_validate(data)
                    self.cache.set(cache_key, {"data": result.model_dump()})
                    return result
                else:
                    result_text = fixture_data.get("text", str(fixture_data)) if isinstance(fixture_data, dict) else str(fixture_data)
                    self.cache.set(cache_key, {"text": result_text})
                    return result_text

            if schema:
                result = _build_mock_structured(schema, seed_text=prompt)
                self.cache.set(cache_key, {"data": result.model_dump()})
                return result
            else:
                prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
                result_text = f"[MOCK_VISION:{prompt_hash}] Vision response for {len(images)} image(s)."
                self.cache.set(cache_key, {"text": result_text})
                return result_text

        # Live vision call
        return self._live_generate_with_images(
            prompt=prompt,
            raw_bytes_list=raw_bytes_list,
            schema=schema,
            model=target_model,
            system_instruction=system_instruction,
            cache_key=cache_key,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _live_generate_with_images(
        self,
        prompt: str,
        raw_bytes_list: List[bytes],
        schema: Optional[Type[T]],
        model: str,
        system_instruction: Optional[str],
        cache_key: str,
    ) -> Any:
        try:
            client = self._get_genai_client()
            from google.genai import types

            contents: List[Any] = [prompt]
            for b in raw_bytes_list:
                contents.append(
                    types.Part.from_bytes(
                        data=b,
                        mime_type="image/jpeg",
                    )
                )

            config_kwargs: Dict[str, Any] = {}
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction
            if schema:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = schema

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config_kwargs or None,
            )
            text_result = response.text or ""
            self.counters.live_calls += 1

            if schema:
                parsed = schema.model_validate_json(text_result)
                self.cache.set(cache_key, {"data": parsed.model_dump()})
                return parsed
            else:
                self.cache.set(cache_key, {"text": text_result})
                return text_result
        except Exception:
            self.counters.failed_calls += 1
            raise

    def embed(
        self,
        text: str,
        model: Optional[str] = None,
        fixture_name: Optional[str] = None,
    ) -> List[float]:
        """Compute embedding vector for text."""
        target_model = model or self.config.llm.models.MODEL_EMBED
        self.counters.total_calls += 1

        cache_key = self.cache.compute_key(
            model=target_model,
            prompt=text,
            extra="embed",
        )

        cached = self.cache.get(cache_key)
        if cached is not None and isinstance(cached, dict) and "embedding" in cached:
            self.counters.cached_calls += 1
            return cached["embedding"]

        if self.mode != "mock":
            self.rate_limiter.acquire()

        if self.mode == "mock":
            self.counters.mock_calls += 1
            fixture_data = self._lookup_fixture(cache_key, fixture_name)
            if fixture_data and isinstance(fixture_data, dict) and "embedding" in fixture_data:
                vec = fixture_data["embedding"]
            else:
                vec = _pseudo_embed(text)
            self.cache.set(cache_key, {"embedding": vec})
            return vec

        # Live Mode
        return self._live_embed(text=text, model=target_model, cache_key=cache_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _live_embed(self, text: str, model: str, cache_key: str) -> List[float]:
        try:
            client = self._get_genai_client()
            response = client.models.embed_content(
                model=model,
                contents=text,
            )
            # Support both object and list attributes based on SDK format
            embedding: List[float] = []
            if hasattr(response, "embedding") and hasattr(response.embedding, "values"):
                embedding = list(response.embedding.values)
            elif hasattr(response, "embeddings") and len(response.embeddings) > 0:
                embedding = list(response.embeddings[0].values)
            else:
                raise ValueError("Unexpected response structure from embed_content")

            self.counters.live_calls += 1
            self.cache.set(cache_key, {"embedding": embedding})
            return embedding
        except Exception:
            self.counters.failed_calls += 1
            raise
