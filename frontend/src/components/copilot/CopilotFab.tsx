import React, { useEffect, useRef, useState } from "react";
import {
  MessageSquare, Send, X, Loader2, Sparkles, AlertTriangle,
} from "lucide-react";
import { CopilotApi } from "@/api";
import { cn } from "@/lib/utils";

/**
 * Floating Copilot widget — accessible from any project page.
 *
 * Stateless conversation: each Send sends the full message history
 * along with the project id. The server attaches grounded project
 * context (safeguards, readiness lenses, top risks / issues,
 * reconciliation status, discovery rollup) so Claude answers based on
 * actual project state, not training memory.
 *
 * Self-hides on 503 (no API key configured) — the floating bubble
 * doesn't render in that case. The component re-tries availability
 * silently on every project switch.
 */

interface Msg {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTED_PROMPTS = [
  "What's blocking us from cutover this Saturday?",
  "Which conversion has the highest risk right now?",
  "What's our readiness score and what's the biggest drag on it?",
  "Summarise open issues for tomorrow's steering committee.",
];

export const CopilotFab: React.FC<{ projectId: number | null }> = ({ projectId }) => {
  const [open, setOpen] = useState(false);
  const [available, setAvailable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest message.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, open]);

  // Clear conversation when project switches.
  useEffect(() => {
    setMessages([]);
    setInput("");
  }, [projectId]);

  if (!available || projectId == null) return null;

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || busy) return;
    const nextHistory: Msg[] = [...messages, { role: "user", content }];
    setMessages(nextHistory);
    setInput("");
    setBusy(true);
    try {
      const res = await CopilotApi.ask({
        project_id: projectId,
        messages: nextHistory,
      });
      setMessages((m) => [...m, { role: "assistant", content: res.answer }]);
    } catch (e: any) {
      const status = e?.response?.status;
      if (status === 503) {
        setAvailable(false);
        setOpen(false);
        return;
      }
      const detail = e?.response?.data?.detail || e?.message || "Copilot is unavailable right now.";
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${detail}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Floating button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-brand text-white shadow-soft transition hover:bg-brand-dark"
          title="Ask the Copilot"
        >
          <Sparkles className="h-5 w-5" />
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="fixed bottom-6 right-6 z-40 flex h-[600px] w-[400px] max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-line bg-white shadow-soft">
          <div className="flex items-center justify-between border-b border-line bg-gradient-to-br from-brand-subtle to-white px-4 py-3">
            <div className="inline-flex items-center gap-2">
              <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand text-white">
                <Sparkles className="h-3.5 w-3.5" />
              </div>
              <div>
                <div className="text-sm font-semibold text-ink">Copilot</div>
                <div className="text-[10.5px] text-ink-muted">Grounded in this project's state</div>
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="rounded p-1 text-ink-muted hover:bg-canvas hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {messages.length === 0 ? (
              <div className="space-y-2">
                <div className="rounded-md bg-canvas px-3 py-2 text-[12px] text-ink-muted">
                  Hi — I can answer questions about this project's safeguards, readiness score, top risks, open issues, reconciliation status, and discovery rollup. I can't take actions yet, but I can tell you the in-product path.
                </div>
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-muted">
                  Try:
                </div>
                {SUGGESTED_PROMPTS.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => send(p)}
                    className="block w-full rounded-md border border-line bg-white px-3 py-2 text-left text-[12px] text-ink hover:border-brand-dark/40 hover:shadow-soft"
                  >
                    {p}
                  </button>
                ))}
              </div>
            ) : (
              messages.map((m, i) => <Bubble key={i} role={m.role} content={m.content} />)
            )}
            {busy && (
              <div className="inline-flex items-center gap-2 text-[12px] text-ink-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking…
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
          <div className="border-t border-line p-3">
            <div className="flex items-center gap-2">
              <input
                className="input !h-9 flex-1 !text-[12.5px]"
                placeholder="Ask anything about this project…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                disabled={busy}
              />
              <button
                onClick={() => send()}
                disabled={busy || !input.trim()}
                className="flex h-9 w-9 items-center justify-center rounded-md bg-brand text-white hover:bg-brand-dark disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="mt-1.5 text-[10px] text-ink-muted">
              Read-only · audited · doesn't store chat history server-side.
            </div>
          </div>
        </div>
      )}
    </>
  );
};

const Bubble: React.FC<{ role: "user" | "assistant"; content: string }> = ({ role, content }) => {
  const isUser = role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div className={cn(
        "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[12.5px] leading-snug",
        isUser
          ? "bg-brand text-white"
          : "bg-canvas text-ink",
      )}>
        {content}
      </div>
    </div>
  );
};
