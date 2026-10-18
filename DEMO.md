# CloudSentry Terminal Demo

This is a 2–3 minute walkthrough of the CLI against the synthetic FalkorDB graph. It uses no credentials and performs no remediation.

## Before the Demo

Use Windows PowerShell from the project directory. Confirm Python and Docker are available and FalkorDB is listening:

```powershell
py --version
docker ps
Test-NetConnection localhost -Port 6379
```

The project was exercised with Python 3.12.1 and the `falkordb` Python package. Install the client if needed:

```powershell
py -m pip install falkordb
```

If FalkorDB is not already running, start the container as described in [README.md](README.md#setup-windows). This folder does not contain Docker Compose configuration.

## Demo Sequence

1. **Seed the graph (about 20 seconds).** Explain that this merges stable synthetic identities and does not clear existing graph data.

   ```powershell
   py .\app.py --seed-only
   ```

2. **Run the incident investigation (about 30 seconds).** This seeds again, then investigates the affected service.

   ```powershell
   py .\app.py --seed --service payments-api --depth 3 --incident "Service payments-api is returning HTTP 500 errors. Investigate the impact and identify the likely cause and responsible team."
   ```

3. **Walk through the report (about 60 seconds).** Start with the two blast-radius paths pointing into `payments-api`; then show its downstream dependency chain through `fraud-detection` to `status-page`. Point out `checkout-team` and Alice's recorded `primary` on-call role, followed by the deployment and alert/metric records.

4. **Explain evidence versus hypothesis (about 30 seconds).** The path `cart-service -> payments-api` is a fact returned by the `DEPENDS_ON` traversal. `DEP-204` is a deployment connected to `payments-api`; its timing makes it worth investigating, but does not prove it caused the HTTP 500 errors. The alert and metric records are also correlations, not causal proof.

5. **Close (about 15 seconds).** CloudSentry uses the graph to connect impact, dependencies, ownership, and operational signals in one explainable report, while keeping uncertainty visible and leaving all actions to the operator.

## What the Graph Queries Show

- `blast_radius` follows incoming `DEPENDS_ON` paths up to the user-selected depth (validated from one to four hops).
- `dependencies` follows outgoing `DEPENDS_ON` paths from the affected service.
- `ownership` resolves the owning team and members through `OWNS` and `MEMBER_OF`.
- `recent_changes` resolves deployments through `DEPLOYED_TO`.
- `related_alerts` resolves `Alert` and `Metric` nodes connected by `AFFECTED`.

The report prints the actual returned paths, not a generated narrative. With the included seed, the report contains the paths and values shown in the [README example](README.md#example-report). If the graph has been changed, show the live CLI output rather than treating that example as a guaranteed result.