"""Chooses the memory backend. Hindsight when configured and reachable, else the local fallback."""
from __future__ import annotations

import logging

from ..config import Settings
from .base import MemoryStore
from .local_store import LocalMemoryStore

log = logging.getLogger("memoryops.memory")


def build_memory_store(s: Settings) -> MemoryStore:
    mode = s.memory_backend
    if mode == "local":
        return LocalMemoryStore(s.local_memory_path, reason="MEMORY_BACKEND=local")
    configured = bool(s.hindsight_base_url or s.hindsight_api_key)
    if not configured:
        if mode == "hindsight":
            log.warning("MEMORY_BACKEND=hindsight but no HINDSIGHT_BASE_URL/API key set")
        return LocalMemoryStore(s.local_memory_path, reason="Hindsight not configured (no URL or key set)")
    base_url = s.hindsight_base_url or "https://api.hindsight.vectorize.io"
    store = None
    try:
        from .hindsight_store import HindsightMemoryStore

        store = HindsightMemoryStore(base_url, s.hindsight_api_key, s.hindsight_bank_id, s.hindsight_timeout)
        store.connect()
        log.info("Memory: Hindsight at %s (bank %s)", base_url, s.hindsight_bank_id)
        return store
    except Exception as e:  # unreachable / auth failure / client missing
        # Never log the key; the exception type and a short message are enough.
        if store is not None:
            store.close()
        msg = f"Hindsight unreachable at {base_url}: {type(e).__name__}"
        log.warning(msg)
        return LocalMemoryStore(s.local_memory_path, reason=msg)
