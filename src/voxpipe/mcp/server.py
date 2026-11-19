"""VoxPipe MCP stdio adapter over the authenticated HTTP API."""
from __future__ import annotations
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Any, TextIO

PROTOCOL_VERSION = "2025-03-26"
ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "contracts" / "mcp" / "capabilities.v1.json"

class APIError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class HTTPAPIClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not token:
            raise ValueError("MCP API URL and bearer token are required")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("HTTPS is required for non-loopback MCP API URLs")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, path: str, *, payload=None, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, separators=(",", ":")).encode()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise APIError("RESPONSE_TOO_LARGE", "API response exceeded the configured limit")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(65536))
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = {}
            raise APIError(detail.get("code", "UPSTREAM_HTTP_ERROR"),
                           "The VoxPipe API rejected the request",
                           exc.code in {408, 425, 429} or exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError("UPSTREAM_UNAVAILABLE", "The VoxPipe API is temporarily unavailable", True) from None

class VoxPipeMCP:
    def __init__(self, client: Any, contract_path: Path = CONTRACT_PATH):
        self.client = client
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))

    def handle(self, message: dict[str, Any]):
        method, request_id = message.get("method"), message.get("id")
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return {"jsonrpc":"2.0","id":request_id,"result":{}}
        if method == "initialize":
            return {"jsonrpc":"2.0","id":request_id,"result":{
                "protocolVersion":PROTOCOL_VERSION,"capabilities":{"tools":{"listChanged":False}},
                "serverInfo":{"name":"voxpipe","version":self.contract["contract_version"]}}}
        if method == "tools/list":
            return {"jsonrpc":"2.0","id":request_id,"result":{"tools":[
                {"name":t["name"],"description":t["description"],"inputSchema":t["inputSchema"],
                 "annotations":{"readOnlyHint":t["name"]!="create_ingestion","destructiveHint":False,
                                "idempotentHint":True,"openWorldHint":True}}
                for t in self.contract["tools"]]}}
        if method == "tools/call":
            params = message.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in self.contract["tools"]}:
                return self._error(request_id,-32602,"Unknown tool")
            try:
                result = self.call_tool(name,args)
                return {"jsonrpc":"2.0","id":request_id,"result":{
                    "content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],
                    "isError":False}}
            except (APIError,ValueError) as exc:
                result={"code":exc.code if isinstance(exc,APIError) else "INVALID_TOOL_ARGUMENTS",
                        "message":str(exc),"retryable":exc.retryable if isinstance(exc,APIError) else False}
                return {"jsonrpc":"2.0","id":request_id,"result":{
                    "content":[{"type":"text","text":json.dumps(result)}],
                    "isError":True}}
        return self._error(request_id,-32601,"Method not found")

    def call_tool(self,name,args):
        if not isinstance(args,dict):
            raise ValueError("tool arguments must be an object")
        if name=="create_ingestion":
            required={"idempotency_key","expected_size_bytes","expected_sha256","source"}
            if set(args)-(required|{"content_type_hint"}) or not required.issubset(args):
                raise ValueError("create_ingestion arguments do not match contract")
            key,size,digest,source=args["idempotency_key"],args["expected_size_bytes"],args["expected_sha256"],args["source"]
            if not isinstance(key,str) or not 16<=len(key)<=128 or not re.fullmatch(r"[A-Za-z0-9._:-]+",key):
                raise ValueError("idempotency_key must be 16-128 safe characters")
            if type(size) is not int or size<1:
                raise ValueError("expected_size_bytes must be a positive integer")
            if not isinstance(digest,str) or not re.fullmatch(r"[a-fA-F0-9]{64}",digest):
                raise ValueError("expected_sha256 must be a SHA-256 hex digest")
            if not isinstance(source,dict) or set(source)-{"kind","external_reference","recorded_at","declared_by"}:
                raise ValueError("source metadata contains unsupported fields")
            if source.get("kind") not in {"file_upload","device_capture","telephony","live_stream","import"}:
                raise ValueError("source.kind is invalid")
            content_type=args.get("content_type_hint")
            if content_type is not None and (not isinstance(content_type,str) or len(content_type)>128):
                raise ValueError("content_type_hint is invalid")
            payload={k:args[k] for k in ("expected_size_bytes","expected_sha256","source","content_type_hint") if k in args}
            return self.client.request("POST","/v1/ingestions",payload=payload,idempotency_key=key)
        if name in {"get_ingestion_status","get_asset"}:
            key="session_id" if name=="get_ingestion_status" else "asset_id"
            if set(args)!={key}:
                raise ValueError("tool arguments do not match contract")
            identifier=self._uuid(args[key])
            path="/v1/ingestions/"+identifier if name=="get_ingestion_status" else "/v1/assets/"+identifier
            return self.client.request("GET",path)
        if name=="search_assets":
            if set(args)-{"query","limit"} or "query" not in args:
                raise ValueError("search_assets requires query and optional limit")
            query,limit=args["query"],args.get("limit",20)
            if not isinstance(query,str) or not 2<=len(query.strip())<=128:
                raise ValueError("query must contain between 2 and 128 characters")
            if type(limit) is not int or not 1<=limit<=50:
                raise ValueError("limit must be between 1 and 50")
            return self.client.request("GET","/v1/assets/search?"+urllib.parse.urlencode({"q":query.strip(),"limit":limit}))
        raise ValueError("unsupported tool")

    @staticmethod
    def _uuid(value):
        import uuid
        if not isinstance(value,str):
            raise ValueError("identifier must be a UUID")
        try:
            return str(uuid.UUID(value))
        except ValueError:
            raise ValueError("identifier must be a UUID") from None

    @staticmethod
    def _error(request_id,code,message):
        return {"jsonrpc":"2.0","id":request_id,"error":{"code":code,"message":message}}

def run_stdio(input_stream:TextIO,output_stream:TextIO,server:VoxPipeMCP):
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message=json.loads(line)
            response=server.handle(message) if isinstance(message,dict) and message.get("jsonrpc")=="2.0" else VoxPipeMCP._error(None,-32600,"Invalid JSON-RPC request")
        except json.JSONDecodeError:
            response=VoxPipeMCP._error(None,-32700,"Parse error")
        if response is not None:
            output_stream.write(json.dumps(response,separators=(",",":"),ensure_ascii=False)+"\n")
            output_stream.flush()

def main():
    base=os.environ.get("VOXPIPE_MCP_API_URL","http://127.0.0.1:8080").strip()
    token=os.environ.get("VOXPIPE_MCP_API_TOKEN","")
    run_stdio(sys.stdin,sys.stdout,VoxPipeMCP(HTTPAPIClient(base,token)))

if __name__=="__main__":
    main()
