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
export type BookPosition = { code: string; name?: string; asset_class: string; weight: number; market_value?: number;
  industry?: string | null; market_cap_bucket?: string | null; quote?: {
    price?: number | null; time?: string | null; pe_ratio?: number | null; pb_ratio?: number | null;
    total_market_cap?: number | null; circulating_market_cap?: number | null;
    position_52week?: number | null } | null };
export type BookConvertibleBond = { code: string; name?: string;
  conversion_premium_pct?: number | null; pure_bond_premium_pct?: number | null;
  double_low?: number | null; ytm_pct?: number | null; rating?: string | null;
  remaining_size_yi?: number | null; remaining_term_years?: number | null;
  redeem_distance_pct?: number | null; buyback_distance_pct?: number | null;
  flags?: string[] };
export type BookReport = { snapshot_id: string; total_value: number; weights: Record<string, number>;
  holdings?: BookPosition[]; convertible_bonds?: BookConvertibleBond[];
  asset_class_weights?: Record<string, number>; industry_weights: Record<string, number>;
  market_cap_buckets?: Record<string, number> | null; market_cap_note?: string;
  industry_snapshot?: { label?: string };
  top_n_concentration: number; hhi: number;
  portfolio_beta_60d: number | null; benchmark: string; stress_returns: {
    benchmark_down_10pct: number | null; industry_down_20pct: Record<string, number>;
    largest_position_down_30pct: number | null }; stress_note?: string;
  red_flags?: { holdings?: { code: string; name?: string; flags?: string[] }[]; flagged_weight?: number } | null;
  announcements?: { since?: string; as_of?: string; items?: BookAnnouncement[] } | null };
export type BookAnnouncement = { code: string; type: string; title: string; date: string; url?: string; importance?: string };
export type BookSnapshotPosition = { code: string; name?: string; quantity: number;
  cost_price: number | null; price: number | null; market_value: number };
export type BookSnapshot = { snapshot_id: string; account: string; as_of: string; source?: string; created_at?: string;
  positions: BookSnapshotPosition[]; positions_count: number; total_value: number; report: BookReport | null };
export type BookHistoryDay = { date: string; nav: number | null; unit_nav: number | null;
  return: number | null; benchmark_return: number | null; excess_return: number | null;
  drawdown: number | null; cash_flow?: number; snapshot_id?: string | null;
  missing_prices?: string[] };
export type BookHistoryReport = { account: string; start: string; end: string;
  benchmark: { code: string; source: string }; rows: BookHistoryDay[];
  trades: { date: string; code: string; quantity_delta: number; estimated_value: number | null; inferred: boolean }[];
  summary: { total_return: number; benchmark_return: number | null;
    max_drawdown: number; volatility_annualized: number | null;
    turnover: number | null; start_nav: number; end_nav: number }; method: string };
export type BookHistoryResponse = { report: BookHistoryReport | null };
export type BookAttributionRow = { name: string; contribution: number;
  allocation?: number | null; selection?: number | null;
  portfolio_weight_start?: number; benchmark_weight?: number };
export type BookAttributionReport = { account: string; start: string; end: string; by: string;
  rows: BookAttributionRow[]; industry_meta: { source: string; as_of: string; fallback_codes: string[]; fallback_label?: string | null };
  benchmark_industry_meta: { status: string; source?: string; snapshot_date?: string; constituent_weight_with_prices?: number; reason?: string };
  summary: { position_contribution_sum: number; portfolio_return: number; benchmark_return: number | null; residual: number };
  method: string };
export type BookAttributionResponse = { report: BookAttributionReport | null };
export type BookRebalanceSuggestion = { code: string; name: string; industry: string;
  action: "trim" | "add" | "hold"; weight: number; cap: number; target_weight?: number | null;
  suggested_trim_value: number; suggested_add_value: number; reasons: string[] };
export type BookRebalanceReport = { snapshot_id: string; account: string; as_of: string;
  total_value: number; rows: BookRebalanceSuggestion[];
  limits: { single_name_max: number; industry_max: number; drift_absolute: number };
  summary: { flagged: number; suggested_trim_total: number }; method: string };
export type BookRebalanceResponse = { report: BookRebalanceReport | null };
export type BookBehaviorReport = { trades: number; buys: number; sells: number; horizon_days: number; method: string;
  disposition: { pgr: number | null; plr: number | null; pgr_minus_plr: number | null };
  repurchase_within_h: { count: number; share: number | null };
  chasing: { mean_prior_excess_h: number | null; n: number };
  switch_value_h: { n: number; mean: number | null; bootstrap_ci95: number[] | null; verdict: string | null;
    decisions: { date: string; sold: string; bought: string[]; value: number }[] };
  costs: { fees: number; gross_traded: number; fee_rate: number | null } };
export type BookBehaviorResponse = { report: BookBehaviorReport | null };
