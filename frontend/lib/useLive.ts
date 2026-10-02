"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, CaseSummary, Stats, api } from "./api";

type LiveEvent =
  | { type: "case"; case: CaseSummary }
  | { type: "status"; case_id: number; status: string }
  | { type: "agent"; case_id: number; agent: string; state: "running" | "done" };

export function useLiveCases() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [activity, setActivity] = useState<Record<number, string>>({});
  const [connected, setConnected] = useState(false);
  const statsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    const [list, summary] = await Promise.all([api<CaseSummary[]>("/cases?limit=500"), api<Stats>("/stats")]);
    setCases(list);
    setStats(summary);
  }, []);

  const refreshStatsSoon = useCallback(() => {
    if (statsTimer.current) clearTimeout(statsTimer.current);
    statsTimer.current = setTimeout(() => api<Stats>("/stats").then(setStats).catch(() => undefined), 400);
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const open = () => {
      socket = new WebSocket(API_URL.replace(/^http/, "ws") + "/ws");
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(open, 2000);
      };
      socket.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as LiveEvent;
        if (event.type === "case") {
          setCases((prev) => [event.case, ...prev.filter((c) => c.id !== event.case.id)]);
          setActivity((prev) => {
            const { [event.case.id]: _done, ...rest } = prev;
            return rest;
          });
          refreshStatsSoon();
        } else if (event.type === "status") {
          setCases((prev) => prev.map((c) => (c.id === event.case_id ? { ...c, status: event.status } : c)));
        } else if (event.type === "agent" && event.state === "running") {
          setActivity((prev) => ({ ...prev, [event.case_id]: event.agent }));
        }
      };
    };
    open();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [refreshStatsSoon]);

  return { cases, stats, activity, connected, refresh };
}
