import { useEffect, useState } from "react";
import { api } from "./api";

export default function AuditLog() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => { api.audit().then(setRows).catch((e) => setErr(e.message)); }, []);

  return (
    <div className="bg-white border border-slate-200 rounded-md overflow-x-auto">
      {err && <p className="px-4 py-2 text-xs text-red-700 border-b border-slate-100">{err}</p>}
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left">
            <th className="px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide whitespace-nowrap">Timestamp (UTC)</th>
            <th className="px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide">Actor</th>
            <th className="px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide">Event</th>
            <th className="px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide">Entity</th>
            <th className="px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide">Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((a) => (
            <tr key={a.id} className="align-top hover:bg-slate-50 transition-colors">
              <td className="px-4 py-2.5 text-slate-400 tabular-nums whitespace-nowrap font-mono">
                {a.ts.replace("T", " ").slice(0, 19)}
              </td>
              <td className="px-4 py-2.5 text-slate-600 font-medium">{a.actor}</td>
              <td className="px-4 py-2.5 text-slate-700">{a.action}</td>
              <td className="px-4 py-2.5 text-slate-500 whitespace-nowrap">{a.entity} {a.entity_id}</td>
              <td className="px-4 py-2.5 font-mono text-slate-500 break-all max-w-xs">{JSON.stringify(a.details)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
