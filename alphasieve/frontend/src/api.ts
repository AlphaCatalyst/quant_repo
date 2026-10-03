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

export type ThesisSummary = {
  id: string; title: string; status: string; assets: { code: string; name: string }[];
  base_valuation: number | null; market_price: number | null;
  implied_core_value: number | null;
  evidence_counts: Record<string, number>; falsifiers_count: number; linked_forecasts_count: number;
};
export type ThesisDetail = {
  thesis: { thesis_id: string; title: string; status: string; as_of: string; assets: ThesisSummary["assets"];
    argument: string[]; core_variables: string[]; valuation: { formula: string; output_unit: string; market_price: number | null };
    parameters: Record<string, { base: number; low: number; high: number; unit: string; evidence: string[] }> };
  base_valuation: number; scenarios: Record<string, number>; sensitivity: {
    parameter: string; low: number; high: number; low_impact: number; high_impact: number; absolute_impact: number;
  }[];
  evidence: { id: string; claim: string; value: string | number | null; source: string; grade: string; accessed: string }[];
  falsifiers: { id: string; condition: string; variable: string; action: string }[];
  proposed_forecasts: Record<string, unknown>[]; registered_forecasts: ForecastItem[];
  journal_entries: { entry_id: string; action?: string; thesis_id?: string; created_at?: string; payload?: Record<string, unknown> }[];
};
export type ForecastItem = {
  forecast_id: string; thesis_id: string | null; status: string; created_at: string;
  spec: { statement: string; p: number; deadline: string; resolver_params: { settle_date: string }; source_of_truth: string };
  resolution?: { outcome?: boolean; observed_value?: number; source?: string };
};
export type ForecastScore = { count: number; brier_score: number | null;
  calibration: { range: string; count: number; observed_frequency: number | null }[] };
export type ForecastsResponse = { forecasts: ForecastItem[]; score: ForecastScore };
export type BookReport = { snapshot_id: string; total_value: number; weights: Record<string, number>;
  industry_weights: Record<string, number>; top_n_concentration: number; hhi: number;
  portfolio_beta_60d: number | null; benchmark: string; stress_returns: {
    benchmark_down_10pct: number | null; industry_down_20pct: Record<string, number>;
    largest_position_down_30pct: number | null }; stress_note?: string };
export type BookSnapshot = { snapshot_id: string; account: string; as_of: string;
  positions_count: number; total_value: number; report: BookReport | null };
