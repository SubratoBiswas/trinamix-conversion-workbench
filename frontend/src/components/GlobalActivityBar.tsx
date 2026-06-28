import React, { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiActivity } from "@/api/client";

/**
 * App-wide processing indicator. Subscribes to the axios activity tracker, so it
 * lights up for ANY API call — AI mapping, live EBS column/row fetches, Fusion
 * loads, status polls, etc. A short delay avoids flicker on instant requests.
 */
export const GlobalActivityBar: React.FC = () => {
  const [busy, setBusy] = useState(false);
  const showTimer = useRef<number | null>(null);

  useEffect(() => {
    return apiActivity.subscribe((n) => {
      if (n > 0) {
        if (showTimer.current == null) {
          showTimer.current = window.setTimeout(() => {
            showTimer.current = null;
            setBusy(true);
          }, 130);
        }
      } else {
        if (showTimer.current != null) {
          clearTimeout(showTimer.current);
          showTimer.current = null;
        }
        setBusy(false);
      }
    });
  }, []);

  if (!busy) return null;

  return (
    <>
      {/* Indeterminate progress bar pinned to the very top of the viewport */}
      <div className="pointer-events-none fixed inset-x-0 top-0 z-[200] h-0.5 overflow-hidden">
        <div
          className="h-full w-1/3 rounded-full bg-brand"
          style={{ animation: "txActivity 1.1s ease-in-out infinite" }}
        />
      </div>
      {/* Small "Working…" badge so the user always sees the tool is busy */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[200] flex items-center gap-2 rounded-full border border-line bg-white px-3 py-1.5 text-xs font-medium text-ink shadow-soft">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
        Working…
      </div>
      <style>{`@keyframes txActivity{0%{transform:translateX(-110%)}60%{transform:translateX(220%)}100%{transform:translateX(320%)}}`}</style>
    </>
  );
};
