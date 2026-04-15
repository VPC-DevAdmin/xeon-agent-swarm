"""
Shared pytest fixtures and configuration.
Sets up environment variables so backend modules can be imported without
real services running.
"""
import os
import pytest

# Point all endpoints at localhost so imports don't fail
os.environ.setdefault("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1")
os.environ.setdefault("ORCHESTRATOR_MODEL", "test-model")
os.environ.setdefault("WORKER_CPU_ENDPOINT", "http://localhost:8081/v1")
os.environ.setdefault("WORKER_CPU_MODEL", "test-worker-model")
os.environ.setdefault("WORKER_GPU_ENDPOINT", "")
os.environ.setdefault("SINGLE_MODEL_ENDPOINT", "http://localhost:8083/v1")
os.environ.setdefault("SINGLE_MODEL", "test-single-model")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("CONFIG_DIR", str(
    os.path.join(os.path.dirname(__file__), "..", "config")
))
