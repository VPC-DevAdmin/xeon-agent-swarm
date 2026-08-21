"""Cloud model catalog and per-run endpoint resolution for capacity tests.

Prices are public list prices in USD per one million text tokens, captured on
2026-08-20.  They are deliberately resolved on the server so a client cannot
silently relabel a preset's price.  Custom endpoints are assumed to implement
the OpenAI Chat Completions API and carry user-supplied prices.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse


PRICING_AS_OF = "2026-08-20"

MODEL_CATALOG: list[dict] = [
    {"id": "openai:gpt-5.4", "provider": "openai", "name": "GPT-5.4",
     "model": "gpt-5.4", "base_url": "https://api.openai.com/v1",
     "input_per_mtok": 2.50, "output_per_mtok": 15.00,
     "pricing_url": "https://developers.openai.com/api/docs/models/gpt-5.4"},
    {"id": "openai:gpt-5.4-mini", "provider": "openai", "name": "GPT-5.4 mini",
     "model": "gpt-5.4-mini", "base_url": "https://api.openai.com/v1",
     "input_per_mtok": 0.75, "output_per_mtok": 4.50,
     "pricing_url": "https://developers.openai.com/api/docs/models/gpt-5.4-mini"},
    {"id": "openai:gpt-5.4-nano", "provider": "openai", "name": "GPT-5.4 nano",
     "model": "gpt-5.4-nano", "base_url": "https://api.openai.com/v1",
     "input_per_mtok": 0.20, "output_per_mtok": 1.25,
     "pricing_url": "https://developers.openai.com/api/docs/models/gpt-5.4-nano"},
    {"id": "anthropic:claude-sonnet-5", "provider": "anthropic", "name": "Claude Sonnet 5",
     "model": "claude-sonnet-5", "base_url": "https://api.anthropic.com",
     "input_per_mtok": 2.00, "output_per_mtok": 10.00,
     "pricing_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
    {"id": "anthropic:claude-opus-5", "provider": "anthropic", "name": "Claude Opus 5",
     "model": "claude-opus-5", "base_url": "https://api.anthropic.com",
     "input_per_mtok": 5.00, "output_per_mtok": 25.00,
     "pricing_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
    {"id": "anthropic:claude-haiku-4-5", "provider": "anthropic", "name": "Claude Haiku 4.5",
     "model": "claude-haiku-4-5-20251001", "base_url": "https://api.anthropic.com",
     "input_per_mtok": 1.00, "output_per_mtok": 5.00,
     "pricing_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
    {"id": "google:gemini-3.1-pro-preview", "provider": "google", "name": "Gemini 3.1 Pro Preview",
     "model": "gemini-3.1-pro-preview", "base_url": "https://generativelanguage.googleapis.com",
     "input_per_mtok": 2.00, "output_per_mtok": 12.00,
     "pricing_note": "standard prompt price up to 200K tokens",
     "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing"},
    {"id": "google:gemini-3-flash-preview", "provider": "google", "name": "Gemini 3 Flash Preview",
     "model": "gemini-3-flash-preview", "base_url": "https://generativelanguage.googleapis.com",
     "input_per_mtok": 0.50, "output_per_mtok": 3.00,
     "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing"},
    {"id": "google:gemini-3.1-flash-lite", "provider": "google", "name": "Gemini 3.1 Flash-Lite",
     "model": "gemini-3.1-flash-lite", "base_url": "https://generativelanguage.googleapis.com",
     "input_per_mtok": 0.25, "output_per_mtok": 1.50,
     "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing"},
]

_KEY_ENVS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def catalog_for_api() -> list[dict]:
    """Public catalog with key availability, never key contents."""
    out = []
    for item in MODEL_CATALOG:
        configured = any(os.getenv(k, "").strip() for k in _KEY_ENVS[item["provider"]])
        out.append({**item, "pricing_as_of": PRICING_AS_OF,
                    "api_key_configured": configured})
    return out


def resolve_endpoint(model_id: str | None, *, api_key: str | None = None,
                     custom_base_url: str | None = None,
                     custom_model: str | None = None,
                     input_per_mtok: float | None = None,
                     output_per_mtok: float | None = None) -> dict:
    """Resolve a preset or custom OpenAI-compatible endpoint for one run."""
    if model_id == "custom":
        if not (custom_base_url or "").strip() or not (custom_model or "").strip():
            raise ValueError("custom endpoint address and model ID are required")
        parsed = urlparse(custom_base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("custom endpoint must be a valid http(s) address")
        if input_per_mtok is None or output_per_mtok is None:
            raise ValueError("custom input and output prices are required")
        return {
            "id": "custom", "provider": "custom", "name": "Custom endpoint",
            "model": custom_model.strip(), "base_url": custom_base_url.strip().rstrip("/"),
            "api_key": api_key or "", "input_per_mtok": float(input_per_mtok),
            "output_per_mtok": float(output_per_mtok), "pricing_as_of": PRICING_AS_OF,
            "pricing_url": None, "pricing_note": "user-supplied price",
        }
    item = next((m for m in MODEL_CATALOG if m["id"] == model_id), None)
    if item is None:
        raise ValueError("select a cloud model")
    env_key = next((os.getenv(k, "").strip() for k in _KEY_ENVS[item["provider"]]
                    if os.getenv(k, "").strip()), "")
    key = (api_key or "").strip() or env_key
    if not key:
        raise ValueError(f"API key required for {item['name']}")
    return {**item, "api_key": key, "pricing_as_of": PRICING_AS_OF}


def public_endpoint(endpoint: dict | None) -> dict | None:
    """Remove the secret before status, history, JSON files, or logs."""
    if not endpoint:
        return None
    return {k: v for k, v in endpoint.items() if k != "api_key"}
