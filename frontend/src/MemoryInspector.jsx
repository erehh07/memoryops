import { useEffect, useState } from "react";
import { api } from "./api";
import { Button, TYPE_LABEL } from "./ui.jsx";

export default function MemoryInspector({ status }) {
  const [vendors, setVendors] = useState([]);
  const [vendor, setVendor] = useState("V01");
  const [type, setType] = useState("price_variance");
  const [variance, setVariance] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => { api.vendors().then(setVendors).catch((e) => setErr(e.message)); }, []);

  const search = async (ev) => {
    ev?.preventDefault(); setErr("");
    try {
      const p = { vendor_code: vendor, exception_type: type };
      if (variance !== "") p.variance_pct = variance;
      setRes(await api.recall(p));
    } catch (e) { setErr(e.message); }
  };

  const backendBadge = status?.memory?.backend === "hindsight"
    ? "text-emerald-700" : "text-amber-700";

  return (
    <div className="bg-white border border-slate-200 rounded-md divide-y divide-slate-100 text-sm">
      {/* Header */}
      <div className="px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h2 className="font-semibold text-slate-800">Memory inspector</h2>
          <span className={`text-xs font-medium ${backendBadge}`}>
            {status?.memory?.backend ?? "…"}
          </span>
        </div>
        <p className="text-xs text-slate-500 mt-0.5">
          Runs the exact recall the agent uses — up to 5 results. {status?.memory?.detail}
        </p>
      </div>

      {/* Query form */}
      <form onSubmit={search} className="px-4 py-3 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-slate-500">Vendor</label>
          <select
            className="border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-400"
            value={vendor} onChange={(e) => setVendor(e.target.value)}
          >
            {vendors.map((v) => (
              <option key={v.code} value={v.code}>{v.code} · {v.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-slate-500">Exception type</label>
          <select
            className="border border-slate-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-400"
            value={type} onChange={(e) => setType(e.target.value)}
          >
            {Object.entries(TYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-slate-500">Variance % (optional)</label>
          <input
            className="border border-slate-300 rounded px-2.5 py-1.5 text-sm w-28 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            placeholder="e.g. 3.5"
            value={variance} onChange={(e) => setVariance(e.target.value)}
          />
        </div>
        <Button type="submit" className="self-end">Recall</Button>
      </form>

      {/* Error */}
      {err && <div className="px-4 py-2 text-xs text-red-700">{err}</div>}

      {/* Results */}
      {res && (
        <div className="divide-y divide-slate-50">
          <div className="px-4 py-2 bg-slate-50 text-xs text-slate-500">
            Query: <span className="text-slate-700 font-medium">"{res.query}"</span>
          </div>
          {!res.results.length && (
            <div className="px-4 py-4 text-sm text-slate-500">
              Nothing recalled. The agent would escalate this case.
            </div>
          )}
          {res.results.map((r) => (
            <div key={r.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <span className="font-mono font-semibold text-xs text-slate-800">{r.id}</span>
                <span className="text-xs text-slate-400">{r.kind.replace("_", " ")}</span>
                <span className="ml-auto text-xs text-slate-400">{r.source} · score {Number(r.score).toFixed(3)}</span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">{r.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
