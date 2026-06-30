"""
/toolbox — the managed tool catalog and its per-role grants (plan §5, §8).

Read-only management view: the full catalog of tools, which role each is granted
to, and each role's validator policy. Demonstrates the catalog/grant/audit story
without exposing the live tool transport.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.agents.profiles import grant_map, validation_config
from backend.agents.toolbox import toolbox_catalog

router = APIRouter(prefix="/toolbox", tags=["toolbox"])


@router.get("")
async def get_toolbox() -> dict:
    """Catalog + per-role grants + per-role validator policy.

    Returns:
      catalog:    {nickname: {description, server, tool}}
      grants:     {role: [granted nicknames]}     — the audit grant
      granted_to: {nickname: [roles]}             — inverse, for the tool-centric view
      validation: {role: {level, tier, rubric, retries}}
    """
    catalog = toolbox_catalog()
    grants = grant_map()
    granted_to: dict[str, list[str]] = {nick: [] for nick in catalog}
    for role, nicks in grants.items():
        for nick in nicks:
            granted_to.setdefault(nick, []).append(role)
    return {
        "catalog": catalog,
        "grants": grants,
        "granted_to": granted_to,
        "validation": validation_config(),
    }
