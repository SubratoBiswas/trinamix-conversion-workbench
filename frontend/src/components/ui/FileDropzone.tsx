import React, { useRef, useState } from "react";
import { Upload, FileSpreadsheet } from "lucide-react";
import { cn } from "@/lib/utils";

export const FileDropzone: React.FC<{
  accept: string;
  onFile: (f: File) => void;
  helper?: string;
  disabled?: boolean;
}> = ({ accept, onFile, helper, disabled }) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [name, setName] = useState<string | null>(null);

  const pick = (file: File) => {
    setName(file.name);
    onFile(file);
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        if (disabled) return;
        const f = e.dataTransfer.files?.[0];
        if (f) pick(f);
      }}
      onClick={() => !disabled && inputRef.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center transition",
        drag ? "border-brand bg-brand-subtle" : "border-line bg-white hover:border-brand-light",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-subtle text-brand">
        {name ? <FileSpreadsheet className="h-5 w-5" /> : <Upload className="h-5 w-5" />}
      </div>
      <div className="mt-3 text-sm font-medium text-ink">
        {name ? name : "Drop file here or click to browse"}
      </div>
      {helper && <div className="mt-1 text-xs text-ink-muted">{helper}</div>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) pick(f);
        }}
      />
    </div>
  );
};
