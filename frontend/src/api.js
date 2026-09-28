async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : res.statusText);
  }
  return data;
}

export const api = {
  status: () => req("GET", "/api/status"),
  batches: () => req("GET", "/api/batches"),
  process: (id) => req("POST", `/api/batches/${id}/process`),
  rerun: (id) => req("POST", `/api/batches/${id}/recommend`),
  exceptions: (id) => req("GET", `/api/batches/${id}/exceptions`),
  invoices: (id) => req("GET", `/api/batches/${id}/invoices`),
  exception: (id) => req("GET", `/api/exceptions/${id}`),
  recommend: (id) => req("POST", `/api/exceptions/${id}/recommend`),
  decide: (id, body) => req("POST", `/api/exceptions/${id}/decide`, body),
  replay: (batchId) => req("POST", "/api/replay-seniors", { batch_id: batchId }),
  recall: (params) => req("GET", "/api/memory/recall?" + new URLSearchParams(params)),
  vendors: () => req("GET", "/api/vendors"),
  audit: () => req("GET", "/api/audit?limit=150"),
  reset: () => req("POST", "/api/reset"),
  reconnect: () => req("POST", "/api/memory/reconnect"),
};
