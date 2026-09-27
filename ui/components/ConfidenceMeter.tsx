import { cn } from "@/lib/format";

function tone(score: number) {
  if (score >= 0.75) return { text: "text-sage", bar: "bg-sage" };
  if (score >= 0.5) return { text: "text-amber", bar: "bg-amber" };
  return { text: "text-brick", bar: "bg-brick" };
}

export default function ConfidenceMeter({
  score,
  size = "md",
}: {
  score: number | null;
  size?: "sm" | "md";
}) {
  if (score === null) {
    return <span className="text-sm text-ink-faint">—</span>;
  }
  const { text, bar } = tone(score);
  const pct = Math.round(score * 100);

  return (
    <div className="flex items-center gap-2">
      <div
        className={cn(
          "overflow-hidden rounded-full bg-sand-soft",
          size === "sm" ? "h-1.5 w-14" : "h-2 w-24"
        )}
      >
        <div className={cn("h-full rounded-full", bar)} style={{ width: `${pct}%` }} />
      </div>
      <span className={cn("font-mono text-xs font-medium tabular-nums", text)}>{pct}%</span>
    </div>
  );
}
