import React from "react";
import { AlertTriangle, RefreshCw, Copy, ChevronDown } from "lucide-react";

/**
 * A page that throws while rendering shows what broke, instead of nothing.
 *
 * There was no error boundary anywhere in this app, and React's behaviour
 * without one is to unmount the entire tree — so a single bad render produced a
 * white screen with no message, no component name and nothing in the UI to act
 * on. The console carried a minified code (`Minified React error #310`) and a
 * stack of one-letter names, which is not something an analyst can report and
 * not something a developer can act on quickly either.
 *
 * That cost real time: Output Preview went blank and the first three
 * explanations considered — a broken build, a failed deploy, a 404 from the
 * static host — were all wrong, because the screen looked identical in every
 * one of those cases. A white page is the least informative failure a UI can
 * have, and it is entirely avoidable.
 *
 * So: catch it, name the component, keep the rest of the app usable, and make
 * the details copyable in one click so a bug report contains the stack rather
 * than the word "blank".
 *
 * Deliberately a class. Error boundaries have no hook equivalent —
 * `componentDidCatch` and `getDerivedStateFromError` exist only on classes, and
 * that is still true in React 18.
 */

type Props = {
  children: React.ReactNode;
  /** Shown in the panel and in the copied report, e.g. the route that failed. */
  where?: string;
  /** Reset when this changes — so navigating away from a broken page recovers. */
  resetKey?: string;
};

type State = { error: Error | null; info: React.ErrorInfo | null; open: boolean };

/** The component nearest the throw. The first frame of React's component stack
 *  looks like "\n    at OutputPreviewPage (http://…)", and that name is the
 *  single most useful thing in the whole report. */
function culprit(info: React.ErrorInfo | null): string | null {
  const stack = info?.componentStack ?? "";
  const m = /^\s*(?:in|at)\s+([A-Za-z0-9_$.]+)/m.exec(stack);
  return m ? m[1] : null;
}

/** React ships production errors as a numbered code. Decoding the handful this
 *  app can realistically hit turns "#310" into a sentence someone can act on. */
const REACT_CODES: Record<string, string> = {
  "300": "Rendered fewer hooks than expected — usually a hook placed after an "
       + "early return, so one render runs fewer hooks than the last.",
  "310": "Rendered more hooks than during the previous render — a hook is "
       + "running conditionally, or sits after an early return that only "
       + "happens on the first render (typically while data is still loading).",
  "321": "A hook was called outside a component, or two copies of React are "
       + "loaded.",
  "425": "Server and client rendered different content.",
};

function decode(error: Error | null): string | null {
  const m = /Minified React error #(\d+)/.exec(error?.message ?? "");
  return m ? (REACT_CODES[m[1]] ?? null) : null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, info: null, open: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.setState({ info });
    // Keep the un-minified detail somewhere reachable from the console, since
    // the panel deliberately does not dump a wall of stack into the page.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", this.props.where ?? "", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    // Navigating to another page must clear the error, or one broken route
    // holds the whole app hostage until a manual reload.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null, info: null, open: false });
    }
  }

  private report() {
    const { error, info } = this.state;
    return [
      `Page: ${this.props.where ?? window.location.pathname}`,
      `Component: ${culprit(info) ?? "unknown"}`,
      `Error: ${error?.message ?? ""}`,
      decode(error) ? `Meaning: ${decode(error)}` : "",
      "",
      "Component stack:",
      (info?.componentStack ?? "").trim(),
      "",
      "JS stack:",
      (error?.stack ?? "").trim(),
    ].filter(Boolean).join("\n");
  }

  render() {
    const { error, info, open } = this.state;
    if (!error) return this.props.children;

    const who = culprit(info);
    const meaning = decode(error);

    return (
      <div className="p-6">
        <div className="mx-auto max-w-3xl rounded-lg border border-rose-200 bg-rose-50 p-5">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-rose-900">
                This section failed to render
              </h2>
              <p className="mt-1 text-sm text-rose-800">
                The rest of the app still works — use the menu to carry on. Nothing
                you were looking at has been changed or lost.
              </p>

              <dl className="mt-3 space-y-1 text-xs text-rose-900">
                {who && (
                  <div className="flex gap-2">
                    <dt className="w-20 shrink-0 font-semibold">Component</dt>
                    <dd className="font-mono">{who}</dd>
                  </div>
                )}
                <div className="flex gap-2">
                  <dt className="w-20 shrink-0 font-semibold">Error</dt>
                  <dd className="font-mono break-all">{error.message}</dd>
                </div>
                {meaning && (
                  <div className="flex gap-2">
                    <dt className="w-20 shrink-0 font-semibold">Meaning</dt>
                    <dd>{meaning}</dd>
                  </div>
                )}
              </dl>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button
                  onClick={() => window.location.reload()}
                  className="inline-flex items-center gap-1.5 rounded-md bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700"
                >
                  <RefreshCw className="h-3.5 w-3.5" /> Reload the page
                </button>
                <button
                  onClick={() => navigator.clipboard?.writeText(this.report())}
                  className="inline-flex items-center gap-1.5 rounded-md border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-800 hover:bg-rose-100"
                  title="Copy the component, the error and both stacks — paste this into a bug report."
                >
                  <Copy className="h-3.5 w-3.5" /> Copy details
                </button>
                <button
                  onClick={() => this.setState({ open: !open })}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-rose-700 hover:underline"
                >
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`}
                  />
                  {open ? "Hide" : "Show"} technical detail
                </button>
              </div>

              {open && (
                <pre className="mt-3 max-h-72 overflow-auto rounded border border-rose-200 bg-white p-3 text-[11px] leading-relaxed text-rose-900">
                  {this.report()}
                </pre>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
