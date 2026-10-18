import unittest
from types import SimpleNamespace
from uuid import uuid4

import app


class FakeGraph:
    def __init__(self, *, deployments=True, events=True, ownership=True, fail_read=False):
        self.calls = []
        self.read_calls = []
        self.fail_read = fail_read
        self.deployments = deployments
        self.events = events
        self.ownership = ownership

    def query(self, query, params=None):
        self.calls.append((query, params))
        return SimpleNamespace(result_set=[])

    def ro_query(self, query, **kwargs):
        self.read_calls.append((query, kwargs.get("params"), kwargs))
        params = kwargs.get("params", {})
        self.calls.append((query, params))
        if self.fail_read and "dependent:Service" in query:
            raise RuntimeError("synthetic query failure")
        if "RETURN s.name AS name" in query:
            return SimpleNamespace(result_set=[[params["name"]]] if params["name"] == "payments-api" else [])
        if "dependent:Service" in query:
            rows = [["checkout-web", 1, ["checkout-web", "payments-api"]],
                    ["cart-service", 1, ["cart-service", "payments-api"]],
                    ["portal", 2, ["portal", "checkout-web", "payments-api"]]]
            return SimpleNamespace(result_set=rows)
        if "dependency:Service" in query:
            return SimpleNamespace(result_set=[
                ["fraud-detection", 1, ["payments-api", "fraud-detection"]],
                ["status-page", 2, ["payments-api", "fraud-detection", "status-page"]],
            ])
        if "team:Team" in query:
            return SimpleNamespace(result_set=[[
                "checkout-team", ["Alice"] if self.ownership else [], ["primary"] if self.ownership else []
            ]] if self.ownership else [])
        if "change:Deployment" in query:
            rows = [["DEP-204", "payments-api 2.8.1", "2026-10-18T09:42:00Z", "Payments API release 2.8.1"]]
            return SimpleNamespace(result_set=rows if self.deployments else [])
        if "event)-[:AFFECTED]" in query:
            rows = [
                [["Alert"], "ALT-778", "2026-10-18T09:58:00Z", "Elevated HTTP 5xx rate", "http_5xx_rate", "12%"],
                [["Metric"], "MET-332", "2026-10-18T09:59:00Z", "Payments API p95 latency elevated", "http_p95_latency_ms", "1850"],
            ]
            return SimpleNamespace(result_set=rows if self.events else [])
        raise AssertionError(f"Unexpected query: {query}")


class CloudSentryTests(unittest.TestCase):
    def test_seed_merges_nodes_and_relationships_without_deleting(self):
        graph = FakeGraph()
        app.seed_graph(graph)
        statements = [query for query, _ in graph.calls]
        first_seed_calls = len(graph.calls)
        app.seed_graph(graph)
        self.assertTrue(all("MERGE" in query for query in statements))
        self.assertFalse(any("DETACH DELETE" in query or "CREATE " in query for query in statements))
        self.assertTrue(any(":DEPLOYED_TO" in query for query in statements))
        self.assertTrue(any(":TRIGGERED" in query for query in statements))
        self.assertTrue(any(":MEMBER_OF" in query for query in statements))
        self.assertEqual(len(graph.calls), first_seed_calls * 2)

    def test_known_and_unknown_service_validation(self):
        graph = FakeGraph()
        self.assertEqual(app.resolve_service(graph, "payments-api"), "payments-api")
        self.assertIsNone(app.resolve_service(graph, "banana-service"))
        self.assertIsNone(app.resolve_service(graph, "payments-api MATCH (n)"))

    def test_blast_radius_points_toward_affected_service_and_uses_depth(self):
        query = app.TOOLS["blast_radius"]["query"]
        self.assertIn("(dependent:Service)-[:DEPENDS_ON*1..4]->(target:Service", query)
        self.assertNotIn("$depth", query)
        graph = FakeGraph()
        rows = app.run_agent_tool(graph, "blast_radius", {"service": "payments-api", "depth": 1})
        self.assertEqual([row[0] for row in rows], ["checkout-web", "cart-service"])
        self.assertEqual(graph.read_calls[-1][2], {"params": {"service": "payments-api"}})
        two_hop_rows = app.run_agent_tool(
            graph, "blast_radius", {"service": "payments-api", "depth": 2}
        )
        self.assertEqual([row[0] for row in two_hop_rows], ["checkout-web", "cart-service", "portal"])

    def test_report_includes_paths_evidence_and_structured_sections(self):
        report = app.investigate(FakeGraph(), "payments-api", "HTTP 500 errors")
        self.assertEqual(report["service"], "payments-api")
        self.assertEqual(report["graph_facts"]["blast_radius"][0]["path"], ["checkout-web", "payments-api"])
        self.assertEqual(report["graph_facts"]["dependencies"][1]["path"], ["payments-api", "fraud-detection", "status-page"])
        self.assertEqual(report["graph_facts"]["recent_deployments"][0]["id"], "DEP-204")
        self.assertIn("not proven", " ".join(item["hypothesis"] for item in report["possible_causes"]))
        formatted_report = app.format_report(report)
        self.assertIn("checkout-team", formatted_report)
        self.assertIn("2.8.1\nRelated alerts and metrics:", formatted_report)
        self.assertIn("live telemetry.\n  - Blast-radius", formatted_report)

    def test_missing_deployment_and_alert_evidence_is_reported(self):
        report = app.investigate(FakeGraph(deployments=False, events=False, ownership=False), "payments-api", "HTTP 500")
        self.assertEqual(report["graph_facts"]["recent_deployments"], [])
        self.assertEqual(report["graph_facts"]["related_events"], [])
        self.assertEqual(report["possible_causes"], [])
        self.assertTrue(any("No deployment" in item for item in report["missing_evidence"]))
        self.assertTrue(any("No related alert" in item for item in report["missing_evidence"]))

    def test_unknown_service_and_invalid_tool_arguments_are_rejected(self):
        graph = FakeGraph()
        with self.assertRaisesRegex(app.InvestigationError, "Unknown or invalid service"):
            app.investigate(graph, "banana-service", "incident")
        with self.assertRaisesRegex(app.InvestigationError, "Unknown investigation tool"):
            app.run_agent_tool(graph, "arbitrary_cypher", {})
        with self.assertRaisesRegex(app.InvestigationError, "depth must be an integer"):
            app.run_agent_tool(graph, "blast_radius", {"service": "payments-api", "depth": 5})
        with self.assertRaisesRegex(app.InvestigationError, "depth must be an integer"):
            app.run_agent_tool(graph, "blast_radius", {"service": "payments-api", "depth": True})
        with self.assertRaisesRegex(app.InvestigationError, "exactly these arguments"):
            app.run_agent_tool(graph, "dependencies", {"service": "payments-api", "extra": "x"})

    def test_investigation_uses_only_read_only_registered_queries(self):
        graph = FakeGraph()
        app.investigate(graph, "payments-api", "HTTP 500 errors")
        investigation_queries = [query for query, _, _ in graph.read_calls]
        tool_queries = [query for query in investigation_queries if "RETURN s.name AS name" not in query]
        self.assertEqual(len(tool_queries), 5)
        self.assertTrue(all("RETURN" in query for query in investigation_queries))
        self.assertFalse(any("CREATE " in query or "MERGE " in query or "DELETE" in query for query in investigation_queries))
        self.assertFalse(any("CREATE " in query for query, _ in graph.calls))

    def test_query_failures_are_wrapped_with_tool_context(self):
        with self.assertRaisesRegex(app.InvestigationError, "FalkorDB query failed for blast_radius: synthetic query failure"):
            app.run_agent_tool(
                FakeGraph(fail_read=True),
                "blast_radius",
                {"service": "payments-api", "depth": 3},
            )

    def test_missing_depth_returns_only_one_hop_for_fake_graph(self):
        self.assertEqual(app.run_agent_tool(
            FakeGraph(), "blast_radius", {"service": "payments-api", "depth": 1}
        )[0][1], 1)


class FalkorDBIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.graph = app.connect_graph()
            cls.graph.ro_query("RETURN 1 AS ok")
        except app.InvestigationError as exc:
            raise unittest.SkipTest(f"FalkorDB is not available: {exc}") from exc

    def test_seed_and_investigate_against_falkordb(self):
        marker = f"preserve-{uuid4()}"
        self.graph.query(
            "CREATE (:CloudSentryTest {id: $id})", params={"id": marker}
        )
        try:
            app.seed_graph(self.graph)
            report = app.investigate(self.graph, "payments-api", "Integration test incident", depth=3)
            self.assertIn("checkout-web", {item["service"] for item in report["graph_facts"]["blast_radius"]})
            self.assertTrue(report["graph_facts"]["recent_deployments"])
            self.assertTrue(report["graph_facts"]["related_events"])
            seeded_counts_query = """
                MATCH (n:Service {name: 'payments-api'})
                OPTIONAL MATCH (t:Team {name: 'checkout-team'})-[:OWNS]->(n)
                OPTIONAL MATCH (d:Deployment {id: 'DEP-204'})-[:DEPLOYED_TO]->(n)
                RETURN count(DISTINCT n), count(DISTINCT t), count(DISTINCT d)
            """
            counts_before = self.graph.ro_query(seeded_counts_query).result_set[0]
            app.seed_graph(self.graph)
            counts_after = self.graph.ro_query(seeded_counts_query).result_set[0]
            marker_count = self.graph.ro_query(
                "MATCH (n:CloudSentryTest {id: $id}) RETURN count(n)", params={"id": marker}
            ).result_set[0][0]
            self.assertEqual(counts_after, counts_before)
            self.assertEqual(marker_count, 1, "Seeding must preserve unrelated graph nodes.")
        finally:
            self.graph.query(
                "MATCH (n:CloudSentryTest {id: $id}) DELETE n", params={"id": marker}
            )


if __name__ == "__main__":
    unittest.main()