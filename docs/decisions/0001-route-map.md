# 0001: Route maps

- Status: accepted
- Date: 2026-11-14

The full record, with the options considered, is kept with Detour's other
standards. This is the short version for contributors.

## Decision

- A route has at most one map, stored in `route_maps`. A map is one road from
  Start to End made of stops and gates, plus detours that skip forward.
- The portal edits it on the Route map page (React Flow). The server
  (`store.save_map`) is the only judge of whether a map is valid.
- Agents call `get_route_plan` and `report_route_progress`. Runs are stored in
  `map_runs`. A failed stop blocks later ones, and unreported stops are "not
  run".
- This is advisory. MCP can't stop an agent from skipping a stop, so the wording
  never says Detour forces or prevents anything.
- Reporting is a log, so read-only keys may report.

## Not done

- Branching roads. One road keeps the model simple to draw and to read back.
- Approval for detours. The user draws them in advance; that is the approval.
- Hard enforcement in CI or a hook.
