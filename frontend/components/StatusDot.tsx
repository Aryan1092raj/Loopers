"use client";
import { useEffect, useState } from "react";
import { get } from "@/lib/api";

export default function StatusDot() {
  const [up, setUp] = useState<boolean | null>(null);
  useEffect(() => {
    const ping = () => get("/api/status").then(() => setUp(true), () => setUp(false));
    ping();
    const t = setInterval(ping, 10_000);
    return () => clearInterval(t);
  }, []);
  const label = up === null ? "checking API" : up ? "API up" : "API down";
  const color = up === null ? "bg-neutral-300" : up ? "bg-green-500" : "bg-red-500";
  return (
    <span role="status" className="flex items-center gap-1 text-xs">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
      {process.env.NEXT_PUBLIC_MOCK === "1" && " · mock"}
    </span>
  );
}
