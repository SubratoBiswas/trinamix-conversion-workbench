import React, { useEffect, useRef, useState } from "react";
import { apiActivity } from "@/api/client";

/**
 * App-wide processing indicator. Subscribes to the axios activity tracker, so it
 * lights up for ANY API call — AI mapping, live EBS column/row fetches, Fusion
 * loads, status polls, etc. A short delay avoids flicker on instant requests.
 *
 * Visual: a glowing indigo "comet" progress bar across the top, plus a frosted
 * glass pill with a smooth conic-gradient spinner and a soft breathing glow.
 */
export const GlobalActivityBar: React.FC = () => {
  const [busy, setBusy] = useState(false);
  const showTimer = useRef<number | null>(null);
  const maxTimer = useRef<number | null>(null);
  const shown = useRef(false);
  const capped = useRef(false); // suppress re-show until activity fully settles

  useEffect(() => {
    const clearTimers = () => {
      if (showTimer.current != null) { clearTimeout(showTimer.current); showTimer.current = null; }
      if (maxTimer.current != null) { clearTimeout(maxTimer.current); maxTimer.current = null; }
    };
    return apiActivity.subscribe((n) => {
      if (n > 0) {
        // Already showing, capped this cycle, or a show is pending — do nothing.
        if (shown.current || capped.current || showTimer.current != null) return;
        showTimer.current = window.setTimeout(() => {
          showTimer.current = null;
          shown.current = true;
          setBusy(true);
          // Never let the indicator linger on a single slow request: auto-hide
          // after a cap, then stay hidden until all requests settle.
          maxTimer.current = window.setTimeout(() => {
            maxTimer.current = null;
            shown.current = false;
            capped.current = true;
            setBusy(false);
          }, 12000);
        }, 130);
      } else {
        // Everything settled — reset and hide.
        clearTimers();
        shown.current = false;
        capped.current = false;
        setBusy(false);
      }
    });
  }, []);

  if (!busy) return null;

  return (
    <>
      <style>{`
        @keyframes txgSweep{0%{left:-42%}100%{left:100%}}
        @keyframes txgSpin{to{transform:rotate(1turn)}}
        @keyframes txgPop{0%{opacity:0;transform:translateY(10px) scale(.94)}100%{opacity:1;transform:translateY(0) scale(1)}}
        @keyframes txgGlow{0%,100%{box-shadow:0 8px 28px -8px rgba(15,23,42,.22),0 0 0 0 rgba(99,102,241,0)}50%{box-shadow:0 10px 30px -8px rgba(79,70,229,.30),0 0 0 5px rgba(99,102,241,.08)}}
        @keyframes txgDot{0%,70%,100%{opacity:.2;transform:translateY(0)}35%{opacity:1;transform:translateY(-1px)}}
        @keyframes txgPulse{0%,100%{opacity:.55;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
        .txg-track{position:fixed;top:0;left:0;right:0;height:3px;z-index:9999;overflow:hidden;
          background:linear-gradient(90deg,rgba(99,102,241,.10),rgba(99,102,241,.16),rgba(99,102,241,.10));pointer-events:none}
        .txg-comet{position:absolute;top:0;height:100%;width:42%;border-radius:999px;
          background:linear-gradient(90deg,rgba(129,140,248,0) 0%,#818CF8 30%,#6366F1 68%,#4F46E5 100%);
          box-shadow:0 0 12px 1px rgba(99,102,241,.6),0 0 4px 0 rgba(79,70,229,.8);
          animation:txgSweep 1.15s cubic-bezier(.45,.05,.3,1) infinite}
        .txg-badge{position:fixed;bottom:22px;right:22px;z-index:9999;display:flex;align-items:center;gap:11px;
          padding:10px 16px 10px 13px;border-radius:999px;pointer-events:none;
          background:rgba(255,255,255,.78);backdrop-filter:blur(12px) saturate(1.4);-webkit-backdrop-filter:blur(12px) saturate(1.4);
          border:1px solid rgba(226,232,240,.85);
          animation:txgPop .3s cubic-bezier(.2,.8,.2,1) both,txgGlow 2.4s ease-in-out infinite .3s;
          font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
        .txg-spin{position:relative;width:20px;height:20px;flex:0 0 auto}
        .txg-ring{position:absolute;inset:0;border-radius:50%;
          background:conic-gradient(from 90deg,rgba(99,102,241,0) 8%,#818CF8 52%,#4F46E5 100%);
          -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 3px));
          mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 3px));
          animation:txgSpin .8s linear infinite}
        .txg-core{position:absolute;top:50%;left:50%;width:5px;height:5px;margin:-2.5px 0 0 -2.5px;border-radius:50%;
          background:#6366F1;animation:txgPulse 1.4s ease-in-out infinite}
        .txg-text{font-size:13px;font-weight:500;color:#0F172A;letter-spacing:.1px;white-space:nowrap}
        .txg-dots{display:inline-block;width:14px;text-align:left}
        .txg-dot{display:inline-block;animation:txgDot 1.4s infinite}
        .txg-dot:nth-child(2){animation-delay:.18s}
        .txg-dot:nth-child(3){animation-delay:.36s}
        @media (prefers-reduced-motion: reduce){
          .txg-comet,.txg-ring,.txg-core,.txg-dot,.txg-badge{animation-duration:0s !important}
        }
      `}</style>

      <div className="txg-track"><div className="txg-comet" /></div>

      <div className="txg-badge" role="status" aria-live="polite" aria-label="Processing">
        <span className="txg-spin" aria-hidden="true">
          <span className="txg-ring" />
          <span className="txg-core" />
        </span>
        <span className="txg-text">
          Working<span className="txg-dots"><span className="txg-dot">.</span><span className="txg-dot">.</span><span className="txg-dot">.</span></span>
        </span>
      </div>
    </>
  );
};
