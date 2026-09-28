import { useEffect, useState } from "react";
import { api } from "./api";

export default function AuditLog() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.audit().then(setRows).catch((e) => setErr(e.message));
  }, []);
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-x-auto">
      {err && <p className="p-3 text-red-700 text-xs">{err}</p>}
      <table className="w-full text-xs">
        <thead className="bg-slate-50 text-slate-500 text-left">
          <tr>
            <th className="px-3 py-2">Time (UTC)</th>
            <th className="px-3 py-2">Actor</th>
            <th className="px-3 py-2">Action</th>
            <th className="px-3 py-2">Entity</th>
            <th className="px-3 py-2">Details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id} className="border-t border-slate-100 align-top">
              <td className="px-3 py-1.5 whitespace-nowrap text-slate-500">{a.ts.replace("T", " ").slice(0, 19)}</td>
              <td className="px-3 py-1.5">{a.actor}</td>
              <td className="px-3 py-1.5 font-medium">{a.action}</td>
              <td className="px-3 py-1.5">{a.entity} {a.entity_id}</td>
              <td className="px-3 py-1.5 font-mono text-slate-600 break-all">{JSON.stringify(a.details)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
