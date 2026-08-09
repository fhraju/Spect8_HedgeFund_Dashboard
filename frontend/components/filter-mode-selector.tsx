"use client";

import type { FilterMode } from "@/lib/api-types";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

export function FilterModeSelector({ activeMode }: { activeMode: FilterMode }) {
  const router = useRouter();
  const [selected, setSelected] = useState(activeMode);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function select(mode: FilterMode) {
    if (mode === selected || pending) return;
    setError(null);
    startTransition(async () => {
      const response = await fetch("/api/filter-mode", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!response.ok) {
        setError("Filter mode could not be changed.");
        return;
      }
      setSelected(mode);
      router.refresh();
    });
  }

  return (
    <div className="filter-mode-control" aria-label="Filter mode">
      <div className="filter-mode-segments">
        <button
          type="button"
          className={selected === "MICRO" ? "active" : ""}
          aria-pressed={selected === "MICRO"}
          disabled={pending}
          onClick={() => select("MICRO")}
        >
          <b>Micro</b><span>Daily Filter</span>
        </button>
        <button
          type="button"
          className={selected === "MACRO" ? "active" : ""}
          aria-pressed={selected === "MACRO"}
          disabled={pending}
          onClick={() => select("MACRO")}
        >
          <b>Macro</b><span>Weekly Filter</span>
        </button>
      </div>
      <small>{pending ? "Changing mode…" : error ?? `${selected === "MICRO" ? "D1" : "W1"} authority active`}</small>
    </div>
  );
}
