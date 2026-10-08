import json
import threading
import ipaddress
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection

from tools.hermes_media_relay import RelayConfig, make_server


class Upstream(BaseHTTPRequestHandler):
    response = {"status": "ok", "operation_ok": True, "result": {"ok": True}}
    seen = []
    def do_POST(self):
        self.__class__.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        data = json.dumps(self.response).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *_): pass


class RelayTests(unittest.TestCase):
    def setUp(self):
        Upstream.seen = []
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.ut = threading.Thread(target=self.up.serve_forever, daemon=True); self.ut.start()
        self.relay = make_server(RelayConfig(token="relay", allowed_source="127.0.0.1/32",
            upstream=f"http://127.0.0.1:{self.up.server_port}", upstream_token="up", allow_test_upstream=True), "127.0.0.1", 0)
        # The fixed production host is not contacted in tests; HTTPConnection
        # is redirected to our local fake through the parsed test port.
        self.rt = threading.Thread(target=self.relay.serve_forever, daemon=True); self.rt.start()

    def tearDown(self):
        self.relay.shutdown(); self.relay.server_close()
        self.up.shutdown(); self.up.server_close()

    def request(self, method, path, body=None, token="relay"):
        c = HTTPConnection("127.0.0.1", self.relay.server_port)
        raw = None if body is None else json.dumps(body)
        headers = {"X-Hermes-Relay-Token": token}
        if raw is not None: headers["Content-Type"] = "application/json"
        c.request(method, path, raw, headers)
        r = c.getresponse(); return r.status, json.loads(r.read())

    def raw_request(self, method, path, body=b"{}", headers=None):
        c = HTTPConnection("127.0.0.1", self.relay.server_port)
        c.request(method, path, body, {"X-Hermes-Relay-Token": "relay", **(headers or {})})
        r = c.getresponse(); return r.status, r.read()

    def test_auth_and_source_fail_closed(self):
        status, _ = self.request("GET", "/media/requests", token="wrong")
        self.assertEqual(status, 403)
        self.assertNotIn(ipaddress.ip_address("127.0.0.1"), RelayConfig(token="x", allowed_source="10.0.0.0/8").allowed_source)

    def test_writes_and_camera_are_not_reachable(self):
        self.assertEqual(self.request("POST", "/home/light", {})[0], 404)
        self.assertEqual(self.request("POST", "/camera/snapshot", {})[0], 404)
        self.assertEqual(Upstream.seen, [])

    def test_fixed_args_and_output_projection(self):
        Upstream.response = {"status": "ok", "operation_ok": True, "result": {"requests": [{"requestedById": "secret", "title": "Dune", "file": "/x"}], "count": 1}}
        status, body = self.request("GET", "/media/requests")
        self.assertEqual(status, 200)
        self.assertNotIn("requestedById", json.dumps(body))
        self.assertNotIn("/x", json.dumps(body))
        self.assertEqual(Upstream.seen[-1]["name"], "overseerr_recent_requests")

    def test_status_schema_drops_nested_raw_fields_and_required_args(self):
        Upstream.response = {"status": "ok", "operation_ok": True, "result": {"found": True, "status": "ok", "canonical_state": "READY",
            "canonical_identity": {"title": "Dune", "year": 2021, "media_type": "movie", "path": "/secret"},
            "nested_secret": {"password": "bad"}, "evidence": {"raw": "bad"}}}
        status, body = self.request("POST", "/media/status", {})
        self.assertEqual(status, 400)
        self.assertEqual(Upstream.seen, [])
        status, body = self.request("POST", "/media/status", {"query": "Dune"})
        self.assertEqual(status, 200)
        text = json.dumps(body)
        self.assertNotIn("secret", text); self.assertNotIn("/secret", text)
        self.assertEqual(body["result"]["canonical_identity"]["title"], "Dune")
        status, _ = self.request("POST", "/media/library", {"query": "Dune", "evil": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(Upstream.seen[-1]["name"], "media_status")

    def test_upstream_operation_failure_is_not_reported_as_success(self):
        Upstream.response = {"status": "failed", "operation_ok": False,
                             "result": {"error": "private backend detail"}}
        status, body = self.request("GET", "/media/requests")
        self.assertEqual(status, 502)
        self.assertIn(body, ({"error": "invalid upstream response"}, {"error": "upstream unavailable"}))

    def test_diagnosis_is_read_only_and_drops_nested_status_evidence(self):
        Upstream.response = {"status": "ok", "operation_ok": True, "result": {
            "found": True, "workflow_id": "wf-123", "title": "Dune", "diagnosis": "COLLECTED_NOT_VISIBLE",
            "blocking_boundary": "plex_visibility", "next_read_only_action": "inspect_scan_or_library_path",
            "status": {"cli_debrid": {"token": "secret", "replica_paths": ["/private"]}}}}
        self.assertEqual(self.request("POST", "/media/diagnose", {})[0], 400)
        status, body = self.request("POST", "/media/diagnose", {"workflow_id": "wf-123"})
        self.assertEqual(status, 200)
        self.assertEqual(Upstream.seen[-1]["name"], "media_diagnose")
        self.assertEqual(body["result"]["diagnosis"], "COLLECTED_NOT_VISIBLE")
        self.assertNotIn("secret", json.dumps(body))
        self.assertNotIn("/private", json.dumps(body))

    def test_malformed_headers_unknown_routes_and_methods_do_not_call_upstream(self):
        self.assertEqual(self.raw_request("POST", "/media/library", b"{}", {"Content-Type": "application/json", "Content-Length": "bad"})[0], 400)
        self.assertEqual(self.raw_request("POST", "/media/library", b"{}", {"Content-Type": "application/json", "Content-Length": "9000"})[0], 400)
        self.assertEqual(self.request("GET", "/media/requests?x=1")[0], 404)
        self.assertEqual(self.request("POST", "/unknown", {})[0], 404)
        self.assertEqual(self.raw_request("PUT", "/media/requests")[0], 501)
        self.assertEqual(Upstream.seen, [])

    def test_malformed_projected_collections_fail_closed(self):
        Upstream.response = {"status": "ok", "operation_ok": True, "result": {"matches": {"path": "/bad"}}}
        status, body = self.request("POST", "/media/library", {"query": "Dune"})
        self.assertEqual(status, 502)
        self.assertIn(body, ({"error": "invalid upstream response"}, {"error": "upstream unavailable"}))


if __name__ == "__main__":
    unittest.main()
