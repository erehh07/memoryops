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

  useEffect(() => {
    api.vendors().then(setVendors).catch((e) => setErr(e.message));
  }, []);

  const search = async (ev) => {
    ev?.preventDefault();
    setErr("");
    try {
      const p = { vendor_code: vendor, exception_type: type };
      if (variance !== "") p.variance_pct = variance;
      setRes(await api.recall(p));
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 space-y-3 text-sm">
      <div>
        <h2 className="font-semibold">Memory inspector</h2>
        <p className="text-xs text-slate-500">
          Runs the same recall the agent uses (max 5 results). Backend: {status?.memory?.backend} — {status?.memory?.detail}
        </p>
      </div>
      <form onSubmit={search} className="flex flex-wrap gap-2 items-center">
        <select className="border rounded px-2 py-1.5" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          {vendors.map((v) => (
            <option key={v.code} value={v.code}>{v.code} · {v.name}</option>
          ))}
        </select>
        <select className="border rounded px-2 py-1.5" value={type} onChange={(e) => setType(e.target.value)}>
          {Object.entries(TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <input className="border rounded px-2 py-1.5 w-32" placeholder="variance %" value={variance} onChange={(e) => setVariance(e.target.value)} />
        <Button type="submit">Recall</Button>
      </form>
      {err && <p className="text-red-700 text-xs">{err}</p>}
      {res && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500">Query: “{res.query}”</p>
          {!res.results.length && <p className="text-slate-500">Nothing recalled. The agent would escalate.</p>}
          {res.results.map((r) => (
            <div key={r.id} className="rounded border border-slate-200 px-3 py-2">
              <div className="flex gap-2 text-xs mb-1">
                <span className="font-mono font-semibold">{r.id}</span>
                <span className="text-slate-500">{r.kind}</span>
                <span className="ml-auto text-slate-400">{r.source} · score {Number(r.score).toFixed(2)}</span>
              </div>
              <div>{r.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
