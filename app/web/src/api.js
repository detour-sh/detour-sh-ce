// Thin client for the portal API in app/server.py.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    // Non-JSON error page; fall through with an empty body.
  }
  if (!response.ok) {
    throw new ApiError(data.error || `Request failed (${response.status})`, response.status);
  }
  return data;
}

export const api = {
  session: () => request("GET", "/api/session"),

  routes: () => request("GET", "/api/routes"),
  route: (id) => request("GET", `/api/routes/${encodeURIComponent(id)}`),
  createRoute: (body) => request("POST", "/api/routes", body),
  updateRoute: (id, body) => request("PUT", `/api/routes/${encodeURIComponent(id)}`, body),
  deleteRoute: (id) => request("POST", `/api/routes/${encodeURIComponent(id)}/delete`, {}),
  routeContext: (id) => request("GET", `/api/routes/${encodeURIComponent(id)}/context`),
  routeEvents: (id) => request("GET", `/api/routes/${encodeURIComponent(id)}/events`),

  createItem: (routeId, body) => request("POST", `/api/routes/${encodeURIComponent(routeId)}/items`, body),
  updateItem: (id, body) => request("PUT", `/api/items/${encodeURIComponent(id)}`, body),
  retireItem: (id) => request("POST", `/api/items/${encodeURIComponent(id)}/retire`, {}),
  restoreItem: (id) => request("POST", `/api/items/${encodeURIComponent(id)}/restore`, {}),
  moveItem: (id, routeId) => request("POST", `/api/items/${encodeURIComponent(id)}/move`, { route_id: routeId }),

  routeChecks: (id) => request("GET", `/api/routes/${encodeURIComponent(id)}/checks`),
  checks: () => request("GET", "/api/checks"),
  addSkip: (routeId, body) => request("POST", `/api/routes/${encodeURIComponent(routeId)}/skips`, body),
  removeSkip: (id) => request("POST", `/api/skips/${encodeURIComponent(id)}/remove`, {}),
  addInclude: (routeId, includedId) => request("POST", `/api/routes/${encodeURIComponent(routeId)}/includes`, { route_id: includedId }),
  removeInclude: (routeId, includedId) =>
    request("POST", `/api/routes/${encodeURIComponent(routeId)}/includes/${encodeURIComponent(includedId)}/remove`, {}),
  checkRuns: () => request("GET", "/api/check-runs"),

  addTarget: (routeId, body) => request("POST", `/api/routes/${encodeURIComponent(routeId)}/targets`, body),
  removeTarget: (id) => request("POST", `/api/targets/${encodeURIComponent(id)}/remove`, {}),

  proposals: (status) => request("GET", `/api/proposals?status=${encodeURIComponent(status)}`),
  approve: (id, changes) => request("POST", `/api/proposals/${encodeURIComponent(id)}/approve`, { changes }),
  reject: (id, note) => request("POST", `/api/proposals/${encodeURIComponent(id)}/reject`, { note }),
  reopen: (id) => request("POST", `/api/proposals/${encodeURIComponent(id)}/reopen`, {}),

  events: () => request("GET", "/api/events"),
  undo: (id) => request("POST", `/api/events/${encodeURIComponent(id)}/undo`, {}),

  keys: () => request("GET", "/api/keys"),
  createKey: (body) => request("POST", "/api/keys", body),
  updateKey: (id, body) => request("PUT", `/api/keys/${encodeURIComponent(id)}`, body),
  revokeKey: (id) => request("POST", `/api/keys/${encodeURIComponent(id)}/revoke`, {}),

  importAll: (body) => request("POST", "/api/import", body),
};
