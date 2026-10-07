import type { LabData } from "@/lib/paperlab-api";

export function SourceBanner({ data }: { data: LabData }) {
  if (data.source === "live") {
    return (
      <div className="mb-6 inline-flex items-center gap-2 rounded-full bg-profit/10 px-3 py-1 text-xs text-profit">
        <span className="live-dot !h-1.5 !w-1.5" /> Live from PaperLab · updates every 15 seconds
      </div>
    );
  }
  return (
    <div role="alert" className="mb-6 rounded-xl border border-loss/30 bg-loss/10 p-3 text-sm text-loss">
      Couldn't load live data{data.error ? `: ${data.error}` : "."} Retrying every 15 seconds.
    </div>
  );
}
