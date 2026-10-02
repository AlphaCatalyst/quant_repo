import { useCallback, useEffect, useState } from "react";

const REFRESH_EVENT = "alphasieve-refresh";
let paused = localStorage.getItem("alphasieve-refresh-paused") === "1";
export function refreshPaused() { return paused; }
export function setRefreshPaused(value: boolean) {
  paused = value;
  localStorage.setItem("alphasieve-refresh-paused", value ? "1" : "0");
  window.dispatchEvent(new Event(REFRESH_EVENT));
}
export function useRefreshPaused() {
  const [value, setValue] = useState(paused);
  useEffect(() => {
    const update = () => setValue(paused);
    window.addEventListener(REFRESH_EVENT, update);
    return () => window.removeEventListener(REFRESH_EVENT, update);
  }, []);
  return value;
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export async function getText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.text();
}

export function useApi<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const isPaused = useRefreshPaused();
  const [isHidden, setIsHidden] = useState(document.hidden);
  const load = useCallback(() => {
    if (!path) return;
    setLoading(true);
    getJSON<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
        setUpdatedAt(Date.now());
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [path]);
  useEffect(() => {
    setData(null);
    load();
  }, [load]);
  useEffect(() => {
    const onVisibilityChange = () => {
      setIsHidden(document.hidden);
      if (!document.hidden && refreshMs && !paused) load();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [load, refreshMs]);
  useEffect(() => {
    if (!refreshMs || isPaused || isHidden) return;
    const id = window.setInterval(load, refreshMs);
    return () => window.clearInterval(id);
  }, [load, refreshMs, isPaused, isHidden]);
  return { data, error, loading, updatedAt, reload: load };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;
