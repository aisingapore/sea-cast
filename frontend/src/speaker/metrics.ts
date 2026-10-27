import type { PipecatMetricsData } from "@pipecat-ai/client-js";

// Metrics accumulate per processor over the session. Each RTVI Metrics event
// carries data for only one processor at a time, so we merge into a store keyed
// by processor → metric kind → rolling history (in ms) rather than replacing.
export type MetricKind = "ttfb" | "processing";
export type MetricSeries = Partial<Record<MetricKind, number[]>>;
export type MetricsStore = Record<string, MetricSeries>;

export const METRIC_KINDS: MetricKind[] = ["ttfb", "processing"];
const MAX_POINTS = 100; // cap history per series

// Merge one RTVI Metrics payload into the store, converting seconds → ms and
// trimming each series to the last MAX_POINTS samples.
export function mergeMetrics(
  prev: MetricsStore,
  data: PipecatMetricsData,
): MetricsStore {
  const next: MetricsStore = { ...prev };
  for (const kind of METRIC_KINDS) {
    for (const { processor, value } of data[kind] ?? []) {
      const series = { ...(next[processor] ?? {}) };
      const history = [...(series[kind] ?? []), value * 1000].slice(-MAX_POINTS);
      next[processor] = { ...series, [kind]: history };
    }
  }
  return next;
}
