import Link from "next/link";

export default function Header() {
  return (
    <header className="border-b border-sand bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-terracotta font-display text-lg font-semibold text-white">
            I
          </span>
          <span className="font-display text-lg text-ink">Invoice Extractor</span>
        </Link>
      </div>
    </header>
  );
}
