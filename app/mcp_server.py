"""DPE RePEc fetch and incremental model tools over MCP stdio."""
from typing import Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import mcp_jobs

server = MCPServer('dpe', instructions=(
    'Fetch a complete Defence and Peace Economics ReDIF snapshot, then pass its snapshot_id '
    'to dpe_update_model. Updates preserve the trained model and select a local release only '
    'after citation review and browser checks pass. Use dpe_update_status to follow background '
    'jobs and dpe_resume_update after correcting a failure or reviewing citation decisions. '
    'These tools do not publish the website or retrain topic clusters.'), version='1.0.0')


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
async def dpe_fetch_redif() -> dict[str, Any]:
    """Download the complete DPE catalogue from RePEc FTP into a new local snapshot.

    Returns snapshot_id, SHA-256, provenance, article count and changes versus the
    current model. Validates all records; never overwrites the bundled source.
    """
    return await anyio.to_thread.run_sync(mcp_jobs.fetch_snapshot)


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
async def dpe_update_model(snapshot_id: str, through: str | None = None,
                           device: Literal['auto', 'cpu', 'mps', 'cuda'] = 'auto',
                           without_citations: bool = False, allow_removals: bool = False) -> dict[str, Any]:
    """Start an incremental model update from a fetched snapshot and return workspace_id.

    Includes every issue unless through (e.g. 37:6) is supplied. Preserves model
    identity, labels and historical assignments. Rejects removals unless explicitly
    allowed. Citation review and browser verification precede local activation.
    without_citations explicitly omits citation analysis. Poll dpe_update_status.
    """
    from functools import partial
    return await anyio.to_thread.run_sync(partial(mcp_jobs.update_model, snapshot_id,
        through=through, device=device, without_citations=without_citations, allow_removals=allow_removals))


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def dpe_update_status(workspace_id: str | None = None) -> dict[str, Any]:
    """Read job stages, errors, review/report paths; omit workspace_id for the current model."""
    return await anyio.to_thread.run_sync(mcp_jobs.update_status, workspace_id)


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
async def dpe_resume_update(workspace_id: str) -> dict[str, Any]:
    """Resume a failed/interrupted update after resolving errors or citation review.

    Completed stages are hash-verified and reused. To resolve review_required,
    inspect the returned review path, edit that workspace's inputs/decisions.json,
    then resume. Corrupt completed artifacts require a fresh workspace.
    """
    return await anyio.to_thread.run_sync(mcp_jobs.resume_update, workspace_id)


if __name__ == '__main__':
    server.run(transport='stdio')
