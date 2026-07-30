"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function ReplayButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");

  async function replay() {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/dashboard/replay", { method: "POST" });
      if (!response.ok) {
        throw new Error("Replay failed");
      }
      setMessage("Replay safely deduplicated");
      router.refresh();
    } catch {
      setMessage("Replay unavailable");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="scan-action">
      <button
        className={pending ? "scan-button scanning" : "scan-button"}
        type="button"
        onClick={replay}
        disabled={pending}
      >
        <span aria-hidden="true">▶</span>
        {pending ? "Replaying" : "Replay Bars"}
      </button>
      <span className="scan-message" aria-live="polite">
        {message}
      </span>
    </div>
  );
}
