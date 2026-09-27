import { DocumentStatus, TERMINAL_STATUSES } from "@/lib/api";
import { STATUS_LABEL, cn } from "@/lib/format";

const STYLES: Record<string, string> = {
  approved: "bg-sage-soft text-sage",
  needs_review: "bg-amber-soft text-amber",
  failed: "bg-brick-soft text-brick",
  rejected: "bg-brick-soft text-brick",
};

export default function StatusBadge({ status }: { status: DocumentStatus }) {
  const isInProgress = !TERMINAL_STATUSES.includes(status);
  const style = STYLES[status] ?? "bg-taupe-soft text-taupe";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        style
      )}
    >
      {isInProgress && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}
