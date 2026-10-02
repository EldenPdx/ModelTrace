import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import app


class ExternalTestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference = Path(__file__).resolve().parents[1] / "data/gpt_reference.jsonl"
        with reference.open(encoding="utf-8") as rows:
            cls.sample = next(json.loads(row)["text"] for row in rows if json.loads(row).get("strict_valid"))

    def setUp(self):
        self.calls = []
        self.anthropic_only = False
        self.failures = 0
        case = self

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.calls.append((self.path, body, self.headers))
                fail = case.failures > 0 or (case.anthropic_only and self.path != "/v1/messages")
                case.failures = max(0, case.failures - 1)
                self.send_response(400 if fail else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if fail:
                    result = {"error": {"message": "upstream error for test-secret"}}
                elif case.anthropic_only:
                    result = {"content": [{"type": "text", "text": case.sample}], "stop_reason": "end_turn"}
                else:
                    result = {"choices": [{"message": {"content": case.sample}, "finish_reason": "stop"}]}
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.client = app.test_client()
        self.payload = {"baseurl": f"http://127.0.0.1:{self.server.server_port}", "api_model": "upstream-alias", "api_key": "test-secret"}

    def test_returns_most_likely_model_and_full_automatic_test_details(self):
        response = self.client.post("/api/v1/test", json=self.payload)
        self.assertEqual(response.status_code, 200)
        result = response.json
        self.assertEqual(set(result), {"model_name", "addtional_data"})
        self.assertEqual(result["model_name"], "gpt-5.4")
        details = result["addtional_data"]
        self.assertEqual(details["prediction_name"], "gpt-5.4")
        self.assertEqual(details["api_test"]["received"], 3)
        self.assertEqual(details["probability"], max(item["probability"] for item in details["results"]))
        self.assertIn("bank", details)
        self.assertNotIn("test-secret", response.get_data(as_text=True))
        self.assertEqual(len(self.calls), 3)
        for path, body, headers in self.calls:
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(body["model"], "upstream-alias")
            self.assertEqual(headers["Authorization"], "Bearer test-secret")
            self.assertNotIn("temperature", body)

    def test_rejects_invalid_input_before_contacting_upstream(self):
        invalid = [None, [], "text", {}, *[
            {**self.payload, field: value}
            for field, values in {
                "baseurl": [None, "", "file:///etc/passwd", "https://", "http://user:password@example.com", "http://example.com/#fragment", "http://example.com:bad"],
                "api_model": [None, [], "  "],
                "api_key": [None, 12, "", "key\r\ninjected: yes"],
                "temperature": [True, "hot", [], -1, 3, float("nan"), float("inf")],
            }.items()
            for value in values
        ]]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/test", data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIsInstance(response.json["error"], str)
        for options in ({"data": "{" , "content_type": "application/json"}, {"data": "plain"}):
            response = self.client.post("/api/v1/test", **options)
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json)
        self.assertEqual(self.calls, [])

    def test_no_usable_upstream_output_returns_json_failure(self):
        self.sample = "I cannot generate numbers. test-secret"
        response = self.client.post("/api/v1/test", json=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json)
        self.assertNotIn("test-secret", response.get_data(as_text=True))
        self.assertEqual(len(self.calls), 6)

    def test_partial_failures_are_reported_without_exposing_api_key(self):
        self.failures = 2
        response = self.client.post("/api/v1/test", json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["addtional_data"]["api_test"]["attempted"], 4)
        self.assertEqual(len(response.json["addtional_data"]["api_test"]["errors"]), 1)
        self.assertNotIn("test-secret", response.get_data(as_text=True))

    def test_temperature_and_base_url_alias_with_anthropic_fallback(self):
        self.anthropic_only = True
        for temperature in (0, 0.7, 2, None):
            with self.subTest(temperature=temperature):
                self.calls.clear()
                payload = {**self.payload, "temperature": temperature}
                payload["base_url"] = payload.pop("baseurl") + "/v1/"
                response = self.client.post("/api/v1/test", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["model_name"], "gpt-5.4")
                self.assertEqual(len(self.calls), 6)
                for path, body, headers in self.calls:
                    if temperature is None:
                        self.assertNotIn("temperature", body)
                    else:
                        self.assertEqual(body["temperature"], temperature)
                    if path == "/v1/messages":
                        self.assertEqual(headers["x-api-key"], "test-secret")

    def test_existing_automatic_test_response_remains_compatible(self):
        payload = dict(self.payload)
        payload["base_url"] = payload.pop("baseurl")
        response = self.client.post("/api/test/auto", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["prediction_name"], "gpt-5.4")
        self.assertEqual(response.json["api_test"]["received"], 3)

    def test_upstream_http_failure_returns_json_failure(self):
        self.failures = 12
        response = self.client.post("/api/v1/test", json=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json)
        self.assertNotIn("test-secret", response.get_data(as_text=True))

    def test_partial_samples_still_return_attribution_and_diagnostics(self):
        self.failures = 8
        response = self.client.post("/api/v1/test", json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["model_name"], "gpt-5.4")
        details = response.json["addtional_data"]
        self.assertEqual(details["api_test"]["received"], 2)
        self.assertEqual(details["api_test"]["attempted"], 6)
        self.assertEqual(details["calibration"]["queries"], "2")


if __name__ == "__main__":
    unittest.main()
