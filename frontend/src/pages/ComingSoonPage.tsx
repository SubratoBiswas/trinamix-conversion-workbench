import React from "react";
import { Sparkles } from "lucide-react";
import { PageTitle } from "@/components/ui/Primitives";

export const ComingSoonPage: React.FC<{ title: string; subtitle?: string }> = ({ title, subtitle }) => (
  <>
    <PageTitle title={title} />
    <div className="rounded-lg border border-dashed border-line bg-white px-6 py-16 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md bg-brand-subtle text-brand">
        <Sparkles className="h-5 w-5" />
      </div>
      <div className="mt-4 text-base font-semibold text-ink">Coming in a later phase</div>
      {subtitle && (
        <p className="mx-auto mt-2 max-w-lg text-sm text-ink-muted">{subtitle}</p>
      )}
    </div>
  </>
);
