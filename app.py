"""CloudSentry: explainable, graph-powered incident investigation demo."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from falkordb import FalkorDB


DEFAULT_INCIDENT = (
    "Service payments-api is returning HTTP 500 errors. Investigate the impact "
    "and identify the likely cause and responsible team."
)
SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class InvestigationError(Exception):
    """An expected validation or graph-query failure during investigation."""


def connect_graph() -> Any:
    """Connect using environment settings, with local demo defaults."""
    host = os.getenv("FALKORDB_HOST", "localhost")
    graph_name = os.getenv("FALKORDB_GRAPH", "track1_demo")
    try:
        port = int(os.getenv("FALKORDB_PORT", "6379"))
        return FalkorDB(host=host, port=port).select_graph(graph_name)
    except (TypeError, ValueError) as exc:
        raise InvestigationError("FALKORDB_PORT must be a valid port number.") from exc
    except Exception as exc:
        raise InvestigationError(
            f"Could not connect to FalkorDB at {host}:{os.getenv('FALKORDB_PORT', '6379')}: {exc}"
        ) from exc


def seed_graph(graph: Any) -> None:
    """Merge synthetic demo nodes and relationships without clearing the graph."""
    nodes = (
        ("Service", "name", "checkout-web", {"tier": "tier-1"}),
        ("Service", "name", "cart-service", {"tier": "tier-1"}),
        ("Service", "name", "payments-api", {"tier": "tier-0"}),
        ("Service", "name", "fraud-detection", {"tier": "tier-2"}),
        ("Service", "name", "status-page", {"tier": "tier-3"}),
        ("Team", "name", "checkout-team", {}),
        ("Person", "name", "Alice", {"email": "alice@example.test", "on_call": "primary"}),
        ("Incident", "id", "INC-101", {
            "summary": "High latency and HTTP 500 errors on payments-api",
            "at": "2026-10-18T10:00:00Z",
        }),
        ("Deployment", "id", "DEP-204", {
            "version": "payments-api 2.8.1",
            "at": "2026-10-18T09:42:00Z",
            "summary": "Payments API release 2.8.1",
        }),
        ("Alert", "id", "ALT-778", {
            "at": "2026-10-18T09:58:00Z",
            "summary": "Elevated HTTP 5xx rate",
            "signal": "http_5xx_rate",
            "value": "12%",
        }),
        ("Metric", "id", "MET-332", {
            "at": "2026-10-18T09:59:00Z",
            "summary": "Payments API p95 latency elevated",
            "signal": "http_p95_latency_ms",
            "value": "1850",
        }),
    )
    for label, key, value, properties in nodes:
        _seed_query(
            graph,
            f"MERGE (n:{label} {{{key}: $identity}}) SET n += $properties",
            {"identity": value, "properties": properties},
        )

    relationships = (
        ("Service", "name", "checkout-web", "DEPENDS_ON", "Service", "name", "payments-api", {"weight": 1}),
        ("Service", "name", "cart-service", "DEPENDS_ON", "Service", "name", "payments-api", {"weight": 1}),
        ("Service", "name", "payments-api", "DEPENDS_ON", "Service", "name", "fraud-detection", {"weight": 2}),
        ("Service", "name", "fraud-detection", "DEPENDS_ON", "Service", "name", "status-page", {"weight": 1}),
        ("Team", "name", "checkout-team", "OWNS", "Service", "name", "payments-api", {}),
        ("Person", "name", "Alice", "MEMBER_OF", "Team", "name", "checkout-team", {}),
        ("Incident", "id", "INC-101", "AFFECTED", "Service", "name", "payments-api", {}),
        ("Deployment", "id", "DEP-204", "DEPLOYED_TO", "Service", "name", "payments-api", {}),
        ("Alert", "id", "ALT-778", "AFFECTED", "Service", "name", "payments-api", {}),
        ("Metric", "id", "MET-332", "AFFECTED", "Service", "name", "payments-api", {}),
        ("Incident", "id", "INC-101", "TRIGGERED", "Alert", "id", "ALT-778", {}),
    )
    for source_label, source_key, source_id, relation, target_label, target_key, target_id, properties in relationships:
        _seed_query(
            graph,
            f"MATCH (a:{source_label} {{{source_key}: $source}}), "
            f"(b:{target_label} {{{target_key}: $target}}) "
            f"MERGE (a)-[r:{relation}]->(b) SET r += $properties",
            {"source": source_id, "target": target_id, "properties": properties},
        )


def _seed_query(graph: Any, query: str, parameters: dict[str, Any]) -> None:
    try:
        graph.query(query, parameters)
    except Exception as exc:
        raise InvestigationError(f"FalkorDB seed query failed: {exc}") from exc


TOOLS: dict[str, dict[str, Any]] = {
    "blast_radius": {
        "required": {"service", "depth"},
        "query": """
            MATCH path = (dependent:Service)-[:DEPENDS_ON*1..4]->(target:Service {name: $service})
            RETURN DISTINCT dependent.name AS service, length(path) AS hops,
                   [n IN nodes(path) | n.name] AS via
            ORDER BY hops, service
        """,
    },
    "dependencies": {
        "required": {"service"},
        "query": """
            MATCH path = (service:Service {name: $service})-[:DEPENDS_ON*1..4]->(dependency:Service)
            RETURN DISTINCT dependency.name AS service, length(path) AS hops,
                   [n IN nodes(path) | n.name] AS via
            ORDER BY hops, service
        """,
    },
    "ownership": {
        "required": {"service"},
        "query": """
            MATCH (team:Team)-[:OWNS]->(service:Service {name: $service})
            OPTIONAL MATCH (person:Person)-[:MEMBER_OF]->(team)
            RETURN team.name AS team, collect(DISTINCT person.name) AS members,
                   collect(DISTINCT person.on_call) AS on_call
            ORDER BY team
        """,
    },
    "recent_changes": {
        "required": {"service"},
        "query": """
            MATCH (change:Deployment)-[:DEPLOYED_TO]->(service:Service {name: $service})
            RETURN change.id AS id, change.version AS version, change.at AS at,
                   change.summary AS summary
            ORDER BY change.at DESC LIMIT 10
        """,
    },
    "related_alerts": {
        "required": {"service"},
        "query": """
            MATCH (event)-[:AFFECTED]->(service:Service {name: $service})
            WHERE event:Alert OR event:Metric
            RETURN labels(event) AS labels, event.id AS id, event.at AS at,
                   event.summary AS summary, event.signal AS signal, event.value AS value
            ORDER BY event.at DESC LIMIT 20
        """,
    },
}


def resolve_service(graph: Any, name: str) -> str | None:
    if not isinstance(name, str) or not SERVICE_NAME_PATTERN.fullmatch(name):
        return None
    try:
        rows = graph.ro_query(
            "MATCH (s:Service {name: $name}) RETURN s.name AS name",
            params={"name": name},
        ).result_set
    except Exception as exc:
        raise InvestigationError(f"FalkorDB service lookup failed: {exc}") from exc
    return rows[0][0] if rows else None


def run_agent_tool(graph: Any, tool_name: str, arguments: dict[str, Any]) -> list[list[Any]]:
    """Validate a fixed tool call and execute its parameterized read-only query."""
    tool = TOOLS.get(tool_name)
    if tool is None:
        raise InvestigationError(f"Unknown investigation tool: {tool_name!r}.")
    if not isinstance(arguments, dict) or set(arguments) != tool["required"]:
        expected = ", ".join(sorted(tool["required"]))
        raise InvestigationError(f"Tool {tool_name!r} requires exactly these arguments: {expected}.")
    service = arguments.get("service")
    if resolve_service(graph, service) is None:
        raise InvestigationError(f"Unknown or invalid service: {service!r}.")
    if tool_name == "blast_radius":
        depth = arguments.get("depth")
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 4:
            raise InvestigationError("Blast-radius depth must be an integer from 1 to 4.")
        query_params = {"service": service}
    else:
        query_params = arguments
    try:
        result = graph.ro_query(tool["query"], params=query_params)
        rows = [[value for value in row] for row in result.result_set]
        if tool_name == "blast_radius":
            rows = [row for row in rows if row[1] <= depth]
        return rows
    except Exception as exc:
        raise InvestigationError(f"FalkorDB query failed for {tool_name}: {exc}") from exc


def investigate(
    graph: Any,
    service: str,
    description: str,
    depth: int = 3,
) -> dict[str, Any]:
    """Gather graph evidence and return a deterministic, structured report."""
    if not isinstance(description, str) or not description.strip():
        raise InvestigationError("Incident description must not be empty.")
    target = resolve_service(graph, service)
    if target is None:
        raise InvestigationError(f"Unknown or invalid service: {service!r}.")
    blast_rows = run_agent_tool(graph, "blast_radius", {"service": target, "depth": depth})
    dependency_rows = run_agent_tool(graph, "dependencies", {"service": target})
    ownership_rows = run_agent_tool(graph, "ownership", {"service": target})
    deployment_rows = run_agent_tool(graph, "recent_changes", {"service": target})
    event_rows = run_agent_tool(graph, "related_alerts", {"service": target})

    blast_radius = [
        {"service": row[0], "hops": row[1], "path": row[2]} for row in blast_rows
    ]
    dependencies = [
        {"service": row[0], "hops": row[1], "path": row[2]} for row in dependency_rows
    ]
    ownership = [
        {"team": row[0], "members": row[1] or [], "on_call": row[2] or []}
        for row in ownership_rows
    ]
    deployments = [
        {"id": row[0], "version": row[1], "at": row[2], "summary": row[3]}
        for row in deployment_rows
    ]
    events = [
        {"labels": row[0], "id": row[1], "at": row[2], "summary": row[3], "signal": row[4], "value": row[5]}
        for row in event_rows
    ]
    possible_causes = []
    if deployments:
        possible_causes.append({
            "hypothesis": "A recent deployment may be related to the incident; this is not proven.",
            "evidence": deployments,
            "confidence": "unconfirmed",
        })
    if events:
        possible_causes.append({
            "hypothesis": "Alert or metric signals correlate with the incident but do not prove its cause.",
            "evidence": events,
            "confidence": "correlation_only",
        })
    missing_evidence = []
    if not deployments:
        missing_evidence.append("No deployment events connected to this service were found.")
    if not events:
        missing_evidence.append("No related alert or metric events connected to this service were found.")
    if not ownership:
        missing_evidence.append("No owning team was found for this service.")
    if ownership and not any(owner["members"] for owner in ownership):
        missing_evidence.append("No team members or on-call person were found.")
    if not blast_radius:
        missing_evidence.append("No dependent services were found within the requested traversal depth.")
    if not dependencies:
        missing_evidence.append("No downstream dependencies were found for this service.")

    return {
        "service": target,
        "incident_summary": description.strip(),
        "graph_facts": {
            "blast_radius": blast_radius,
            "dependencies": dependencies,
            "ownership": ownership,
            "recent_deployments": deployments,
            "related_events": events,
        },
        "possible_causes": possible_causes,
        "missing_evidence": missing_evidence,
        "limitations": [
            "Graph connectivity shows recorded relationships, not causal proof.",
            "The demo uses synthetic data; timestamps and signals are not live telemetry.",
            f"Blast-radius traversal was limited to {depth} hop(s).",
        ],
        "recommended_next_steps": [
            "Compare the deployment timestamp and version with the first observed errors.",
            "Inspect payments-api logs and traces around the alert window.",
            "Ask the owning team to verify rollback or mitigation options; no action is automated.",
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    facts = report["graph_facts"]
    lines = [
        "CLOUDSENTRY INCIDENT INVESTIGATION",
        f"Service: {report['service']}",
        f"Incident: {report['incident_summary']}",
        "",
        "CONFIRMED GRAPH FACTS",
        "Blast radius (dependent -> ... -> affected service):",
    ]
    if facts["blast_radius"]:
        for item in facts["blast_radius"]:
            lines.append(f"  {item['service']} ({item['hops']} hop(s)): {' -> '.join(item['path'])}")
    else:
        lines.append("  No dependent services returned.")
    lines.append("Dependencies (service -> ... -> dependency):")
    if facts["dependencies"]:
        for item in facts["dependencies"]:
            lines.append(f"  {item['service']} ({item['hops']} hop(s)): {' -> '.join(item['path'])}")
    else:
        lines.append("  No dependencies returned.")
    lines.append("Ownership:")
    if facts["ownership"]:
        for owner in facts["ownership"]:
            people = ", ".join(owner["members"]) or "no members recorded"
            on_call = ", ".join(str(value) for value in owner["on_call"] if value is not None)
            suffix = f"; on-call: {on_call}" if on_call else ""
            lines.append(f"  {owner['team']}: {people}{suffix}")
    else:
        lines.append("  No owning team recorded.")
    lines.append("Recent deployments:")
    lines.extend(
        f"  {item['id']} {item['version']} at {item['at']}: {item['summary']}"
        for item in facts["recent_deployments"]
    )
    if not facts["recent_deployments"]:
        lines.append("  None found.")
    lines.append("Related alerts and metrics:")
    lines.extend(
        f"  {item['id']} ({'/'.join(item['labels'])}) at {item['at']}: "
        f"{item['summary']} [{item['signal']}={item['value']}]"
        for item in facts["related_events"]
    )
    if not facts["related_events"]:
        lines.append("  None found.")
    lines.extend(("", "POSSIBLE CAUSES (not proven)"))
    if report["possible_causes"]:
        lines.extend(f"  {item['hypothesis']}" for item in report["possible_causes"])
    else:
        lines.append("  No evidence-backed cause hypothesis available.")
    lines.extend(("", "RECOMMENDED NEXT STEPS"))
    lines.extend(f"  {index}. {step}" for index, step in enumerate(report["recommended_next_steps"], 1))
    lines.extend(("", "MISSING EVIDENCE / LIMITATIONS"))
    lines.extend(f"  - {item}" for item in report["missing_evidence"])
    lines.extend(f"  - {item}" for item in report["limitations"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Investigate synthetic incidents using FalkorDB graph evidence.")
    parser.add_argument("--service", default="payments-api", help="Service to investigate (default: payments-api).")
    parser.add_argument("--incident", default=DEFAULT_INCIDENT, help="Incident description.")
    parser.add_argument("--depth", type=int, default=3, help="Blast-radius depth from 1 to 4 (default: 3).")
    parser.add_argument("--seed", action="store_true", help="Idempotently merge the synthetic demo dataset before investigating.")
    parser.add_argument("--seed-only", action="store_true", help="Seed the synthetic demo dataset and exit.")
    parser.add_argument("--json", action="store_true", help="Print the structured JSON report.")
    args = parser.parse_args(argv)
    try:
        graph = connect_graph()
        if args.seed or args.seed_only:
            seed_graph(graph)
            print("Seeded synthetic CloudSentry data in graph track1_demo.")
        if args.seed_only:
            return 0
        report = investigate(graph, args.service, args.incident, args.depth)
        print(json.dumps(report, indent=2) if args.json else format_report(report))
        return 0
    except InvestigationError as exc:
        print(f"CloudSentry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())