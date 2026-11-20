from __future__ import annotations
import io,json,unittest
from voxpipe.mcp.server import HTTPAPIClient,VoxPipeMCP,run_stdio

class FakeAPI:
    def __init__(self): self.calls=[]
    def request(self,method,path,*,payload=None,idempotency_key=None):
        self.calls.append((method,path,payload,idempotency_key))
        return {"accepted":True,"path":path}

class MCPExternalClientTests(unittest.TestCase):
    def setUp(self): self.api=FakeAPI(); self.server=VoxPipeMCP(self.api)
    def exchange(self,messages):
        source=io.StringIO("".join(json.dumps(x)+"\n" for x in messages)); sink=io.StringIO()
        run_stdio(source,sink,self.server)
        return [json.loads(x) for x in sink.getvalue().splitlines()]
    def test_initialize_and_tool_discovery(self):
        result=self.exchange([{"jsonrpc":"2.0","id":1,"method":"initialize"},
                              {"jsonrpc":"2.0","method":"notifications/initialized"},
                              {"jsonrpc":"2.0","id":2,"method":"tools/list"}])
        self.assertEqual(result[0]["result"]["serverInfo"]["name"],"voxpipe")
        self.assertEqual({x["name"] for x in result[1]["result"]["tools"]},
                         {"create_ingestion","get_ingestion_status","get_asset","search_assets"})
    def test_external_agent_ingestion_lifecycle(self):
        session="11111111-1111-4111-8111-111111111111"
        result=self.exchange([
          {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_ingestion","arguments":{
            "idempotency_key":"agent-ingestion-key-0001","expected_size_bytes":12,
            "expected_sha256":"a"*64,"source":{"kind":"file_upload"}}}},
          {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_ingestion_status","arguments":{"session_id":session}}}])
        self.assertFalse(result[0]["result"]["isError"])
        self.assertEqual(self.api.calls[0][0:2],("POST","/v1/ingestions"))
        self.assertEqual(self.api.calls[0][3],"agent-ingestion-key-0001")
        self.assertEqual(self.api.calls[1][1],"/v1/ingestions/"+session)
    def test_asset_lookup_and_metadata_search(self):
        result=self.exchange([
          {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_asset","arguments":{"asset_id":"22222222-2222-4222-8222-222222222222"}}},
          {"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"search_assets","arguments":{"query":"wav","limit":5}}}])
        self.assertFalse(result[0]["result"]["isError"])
        self.assertIn("/v1/assets/22222222-2222-4222-8222-222222222222",self.api.calls[0][1])
        self.assertIn("q=wav",self.api.calls[1][1])
    def test_invalid_arguments_fail_before_api_call(self):
        result=self.exchange([{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"get_asset","arguments":{"asset_id":"bad"}}}])[0]
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(json.loads(result["result"]["content"][0]["text"])["code"],"INVALID_TOOL_ARGUMENTS")
        self.assertEqual(self.api.calls,[])
    def test_external_http_api_requires_tls(self):
        with self.assertRaises(ValueError):
            HTTPAPIClient("http://api.example.com", "secret")
        HTTPAPIClient("http://127.0.0.1:8080", "secret")

    def test_create_rejects_unsafe_idempotency_and_source_fields(self):
        result=self.exchange([{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"create_ingestion","arguments":{
            "idempotency_key":"too-short","expected_size_bytes":10,"expected_sha256":"a"*64,
            "source":{"kind":"file_upload","tenant_id":"attacker"}}}}])[0]
        self.assertTrue(result["result"]["isError"])
        self.assertEqual(self.api.calls, [])

    def test_notifications_have_no_response(self):
        self.assertEqual(self.exchange([{"jsonrpc":"2.0","method":"notifications/initialized"}]),[])

if __name__=="__main__": unittest.main()
