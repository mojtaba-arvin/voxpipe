---
name: voxpipe-asset-discovery
description: Use when an agent needs tenant-scoped VoxPipe asset metadata lookup or metadata search through MCP.
---

# VoxPipe asset discovery
- Use get_asset for a known asset UUID and search_assets for bounded metadata discovery.
- Search covers asset ID, source kind, MIME type, container format, and codec only. It is not transcript, vector, or semantic search.
- Results omit tenant identifiers and private storage references.
- Read-only discovery requires ingestion:read. Do not request broader scopes.
- An absent result does not prove that an asset never existed; it may be outside the authenticated tenant or no longer retained.
