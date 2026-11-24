#!/usr/bin/env python3
"""Render CLI/SDK-facing MCP documentation from the canonical JSON contract."""
import argparse
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/"contracts/mcp/capabilities.v1.json"
OUTPUT=ROOT/"docs/architecture/mcp-capabilities.md"
def render(data):
    lines=["# MCP Capability Contract","",
      "Contract version: "+data["contract_version"]+". Transport: "+data["transport"]+".","",
      "The MCP server is a thin authenticated adapter over the Go HTTP API. Tenant identity is derived from the bearer token; callers cannot supply tenant IDs.","",
      "| Tool | Scope | HTTP mapping | Retry behavior |","|---|---|---|---|"]
    for t in data["tools"]:
        lines.append("| "+t["name"]+" | "+t["scope"]+" | "+t["method"]+" "+t["path"]+" | "+t["retry"]+" |")
    lines.extend(["","## Tool contracts",""])
    for t in data["tools"]:
        lines.extend(["### "+t["name"],"",t["description"],"",
          "Required scope: "+t["scope"]+".","","Input schema:","",
          json.dumps(t["inputSchema"],indent=2),"","Output schema:","",
          json.dumps(t["outputSchema"],indent=2),"","Stable errors:"])
        for e in t["errors"]:
            lines.append("- "+e["code"]+"; retryable: "+str(e["retryable"]).lower())
        lines.append("")
    lines.extend(["## Security and search boundaries","",
      "- Tenant identity comes from the authenticated principal; caller-supplied tenant IDs are forbidden.",
      "- Asset responses omit tenant identifiers and private storage/object references.",
      "- search_assets performs metadata search only. search_transcripts performs PostgreSQL lexical full-text search over transcript segments. Vector/hybrid semantic search is not yet exposed through the Go API or MCP.",
      "- MCP creates ingestion sessions; audio bytes are transferred separately through the authenticated HTTP upload API.",""])
    return "\n".join(lines)
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--check",action="store_true"); args=parser.parse_args()
    expected=render(json.loads(CONTRACT.read_text(encoding="utf-8")))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8")!=expected:
            raise SystemExit("MCP capability documentation is stale")
        print("MCP capability documentation is up to date."); return
    OUTPUT.parent.mkdir(parents=True,exist_ok=True); OUTPUT.write_text(expected,encoding="utf-8")
    print("Generated docs/architecture/mcp-capabilities.md")
if __name__=="__main__": main()
