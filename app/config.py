"""Configuration management for TickTock Meals.

Loads settings from config.yaml and environment variables (.env).
Environment variables take precedence where applicable (e.g., LLM_MODE).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
import yaml


class LLMModels(BaseModel):
    """Named model identifiers, specified ONLY in config.yaml."""
    MODEL_FAST: str = Field(..., description="Fast Flash-class model for text/reasoning")
    MODEL_VISION: str = Field(..., description="Vision-capable model for food images")
    MODEL_EMBED: str = Field(..., description="Embedding model for vector retrieval")


class LLMConfig(BaseModel):
    """LLM execution settings."""
    mode: Literal["mock", "live"] = Field(default="mock", description="Execution mode: mock or live")
    models: LLMModels
    rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    cache_dir: str = Field(default=".cache")


class SpeedConfig(BaseModel):
    """Transit speeds in km/h for ETA calculations."""
    bike: float = Field(default=22.0, gt=0)
    car: float = Field(default=25.0, gt=0)


class ScoringWeights(BaseModel):
    """Weights for candidate recipient/volunteer scoring."""
    urgency: float = Field(default=0.35, ge=0.0)
    proximity: float = Field(default=0.25, ge=0.0)
    capacity: float = Field(default=0.20, ge=0.0)
    diet: float = Field(default=0.10, ge=0.0)
    hours: float = Field(default=0.10, ge=0.0)


class ImpactFactors(BaseModel):
    """Configurable assumptions for impact calculations."""
    kg_per_meal: float = Field(default=0.4, gt=0)
    co2e_kg_per_kg_food: float = Field(default=2.5, gt=0)


class AppConfig(BaseModel):
    """Global typed configuration schema for TickTock Meals."""
    llm: LLMConfig
    db_path: str = Field(default="data/ticktockmeals.db")
    avg_speed_kmph: SpeedConfig = Field(default_factory=SpeedConfig)
    handover_buffer_minutes: int = Field(default=15, ge=0)
    min_human_window_minutes: int = Field(default=45, ge=0)
    vision_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    scoring_weights: ScoringWeights
    impact_factors: ImpactFactors = Field(default_factory=ImpactFactors)
    gemini_api_key: Optional[str] = Field(default=None, exclude=True)


_CONFIG_CACHE: Optional[AppConfig] = None


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path | None = ".env") -> AppConfig:
    """Load, validate, and return the application configuration.

    Reads from config.yaml, merges with .env, and checks environment variable overrides.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found at: {path.resolve()}")

    # Load environment variables if available
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML from {path}: {e}") from e

    if not isinstance(raw_data, dict):
        raise ValueError(f"Root of configuration file {path} must be a dictionary.")

    # Environment variable override: LLM_MODE overrides llm.mode
    env_llm_mode = os.getenv("LLM_MODE")
    if env_llm_mode:
        env_llm_mode = env_llm_mode.strip().lower()
        if "llm" not in raw_data or not isinstance(raw_data["llm"], dict):
            raw_data["llm"] = {}
        raw_data["llm"]["mode"] = env_llm_mode

    # Read API key securely into config model (excluded from repr/serialization)
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        raw_data["gemini_api_key"] = api_key.strip()

    try:
        config = AppConfig.model_validate(raw_data)
    except ValidationError as e:
        raise ValueError(f"Configuration validation failed: {e}") from e

    return config


def get_config(reload: bool = False, config_path: str | Path = "config.yaml") -> AppConfig:
    """Return the cached AppConfig instance, or load it if not yet loaded."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None or reload:
        _CONFIG_CACHE = load_config(config_path=config_path)
    return _CONFIG_CACHE
