import { IssueOut } from "@/lib/api";
import { cn } from "@/lib/format";

export default function IssuesList({ issues }: { issues: IssueOut[] }) {
  if (issues.length === 0) {
    return (
      <p className="text-sm text-ink-faint">No validation issues on this attempt.</p>
    );
  }

  return (
    <ul className="space-y-2">
      {issues.map((issue, i) => (
        <li
          key={i}
          className={cn(
            "flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm",
            issue.severity === "error"
              ? "border-brick/30 bg-brick-soft text-brick"
              : "border-amber/30 bg-amber-soft text-amber"
          )}
        >
          <span className="mt-0.5 text-xs font-semibold uppercase tracking-wide opacity-80">
            {issue.severity}
          </span>
          <span className="flex-1 text-ink">
            {issue.field && <span className="font-medium">{issue.field}: </span>}
            {issue.message}
            <span className="ml-1.5 text-xs text-ink-faint">({issue.rule})</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
