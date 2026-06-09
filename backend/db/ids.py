"""
UUIDv7 generation — time-ordered UUIDs.

UUIDv7 (RFC 9562) embeds a millisecond Unix timestamp in the high bits, so the
ids sort chronologically. This gives us naturally time-ordered primary keys
that index well in Postgres (no random-insert B-tree fragmentation) while
remaining globally unique. See docs/standards.md §2.6.

Layout (128 bits):
  unix_ts_ms  : 48 bits   millisecond timestamp
  version     :  4 bits   0b0111 (7)
  rand_a      : 12 bits   random
  variant     :  2 bits   0b10
  rand_b      : 62 bits   random
"""
from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7."""
    unix_ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand = int.from_bytes(os.urandom(10), "big")           # 80 random bits

    # rand_a (12 bits) + rand_b (62 bits) = 74 bits, drawn from the 80 we have
    rand_a = (rand >> 62) & 0x0FFF
    rand_b = rand & 0x3FFFFFFFFFFFFFFF

    value = (
        (unix_ts_ms << 80)
        | (0x7 << 76)            # version 7
        | (rand_a << 64)
        | (0b10 << 62)           # variant
        | rand_b
    )
    return uuid.UUID(int=value)


def uuid7_str() -> str:
    """UUIDv7 as a canonical string."""
    return str(uuid7())
