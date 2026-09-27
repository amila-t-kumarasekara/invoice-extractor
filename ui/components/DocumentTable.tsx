import Link from "next/link";
import { DocumentSummary } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import StatusBadge from "./StatusBadge";
import ConfidenceMeter from "./ConfidenceMeter";

export default function DocumentTable({ documents }: { documents: DocumentSummary[] }) {
  if (documents.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-sand bg-surface/60 px-6 py-14 text-center text-sm text-ink-faint">
        No documents yet for this tenant. Upload one to get started.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-sand bg-surface-raised shadow-sm shadow-black/5">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-sand bg-sand-soft/60 text-xs uppercase tracking-wide text-ink-faint">
            <th className="px-4 py-3 font-medium">Document</th>
            <th className="px-4 py-3 font-medium">Type</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Confidence</th>
            <th className="px-4 py-3 font-medium">Uploaded</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr
              key={doc.document_id}
              className="border-b border-sand/70 last:border-0 hover:bg-sand-soft/40"
            >
              <td className="px-4 py-3">
                <Link
                  href={`/documents/${doc.document_id}`}
                  className="font-medium text-ink hover:text-terracotta"
                >
                  {doc.filename}
                </Link>
              </td>
              <td className="px-4 py-3 text-ink-soft">{doc.doc_type ?? "—"}</td>
              <td className="px-4 py-3">
                <StatusBadge status={doc.status} />
              </td>
              <td className="px-4 py-3">
                <ConfidenceMeter score={doc.confidence} size="sm" />
              </td>
              <td className="px-4 py-3 whitespace-nowrap text-ink-faint">
                {formatRelativeTime(doc.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
