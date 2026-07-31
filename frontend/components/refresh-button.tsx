"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export function RefreshButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const interval = window.setInterval(() => router.refresh(), 60_000);
    return () => window.clearInterval(interval);
  }, [router]);

  function refresh() {
    setPending(true);
    router.refresh();
    window.setTimeout(() => setPending(false), 500);
  }

  return (
    <button
      className={pending ? "scan-button scanning" : "scan-button"}
      type="button"
      onClick={refresh}
      disabled={pending}
    >
      <span aria-hidden="true">↻</span>
      {pending ? "Refreshing" : "Refresh data"}
    </button>
  );
}
