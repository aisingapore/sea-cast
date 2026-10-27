import { useMemo } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { Line } from "react-chartjs-2";
import {
  METRIC_KINDS,
  type MetricKind,
  type MetricSeries,
  type MetricsStore,
} from "./metrics";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
);

const METRIC_LABELS: Record<MetricKind, string> = {
  ttfb: "Time to first byte",
  processing: "Processing time",
};

const METRIC_COLORS: Record<MetricKind, string> = {
  ttfb: "#2563eb",
  processing: "#d97706",
};

const CHART_OPTIONS: ChartOptions<"line"> = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { intersect: false, mode: "index" },
  scales: {
    x: { display: false },
    y: { beginAtZero: true, ticks: { callback: (v) => `${v} ms` } },
  },
  plugins: { legend: { position: "bottom" } },
};

// Turn a Pipecat processor id (e.g. "OpenAICompatibleBatchSTT#0") into a readable label.
function friendlyName(processor: string): string {
  const name = processor.replace(/#\d+$/, "");
  const labels: Record<string, string> = {
    OpenAICompatibleBatchSTT: "Speech-to-text (OpenAI-compatible)",
    ElevenLabsRealtimeSTT: "Speech-to-text (ElevenLabs)",
    LLMTranslator: "Translation",
  };
  return labels[name] ?? name;
}

// Latency rises through the pipeline, so show speech-to-text before translation.
// Translation cards are "LLMTranslator#0" or, with a room, "<translator> · <lang>".
function processorRank(processor: string): number {
  return processor.includes("STT") ? 0 : 1;
}

function stats(values: number[]) {
  return {
    last: values[values.length - 1],
    avg: values.reduce((a, b) => a + b, 0) / values.length,
    min: Math.min(...values),
    max: Math.max(...values),
    count: values.length,
  };
}

function MetricCard({
  processor,
  series,
}: {
  processor: string;
  series: MetricSeries;
}) {
  const kinds = METRIC_KINDS.filter((k) => (series[k]?.length ?? 0) > 0);
  const points = Math.max(...kinds.map((k) => series[k]!.length));

  const chartData = {
    labels: Array.from({ length: points }, (_, i) => `${i + 1}`),
    datasets: kinds.map((kind) => ({
      label: METRIC_LABELS[kind],
      data: series[kind]!,
      borderColor: METRIC_COLORS[kind],
      backgroundColor: METRIC_COLORS[kind],
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    })),
  };

  return (
    <section className="metrics-card">
      <h3>{friendlyName(processor)}</h3>
      <div className="metrics-chart">
        <Line data={chartData} options={CHART_OPTIONS} />
      </div>
      <table className="metrics-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Last</th>
            <th>Avg</th>
            <th>Min</th>
            <th>Max</th>
            <th>n</th>
          </tr>
        </thead>
        <tbody>
          {kinds.map((kind) => {
            const s = stats(series[kind]!);
            return (
              <tr key={kind}>
                <td>{METRIC_LABELS[kind]}</td>
                <td className="metric-value">{s.last.toFixed(0)} ms</td>
                <td className="metric-value">{s.avg.toFixed(0)} ms</td>
                <td className="metric-value">{s.min.toFixed(0)} ms</td>
                <td className="metric-value">{s.max.toFixed(0)} ms</td>
                <td className="metric-value">{s.count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

// The Metrics tab: one latency card per processor.
export function MetricsPanel({ metrics }: { metrics: MetricsStore }) {
  const processors = useMemo(
    () => Object.keys(metrics).sort((a, b) => processorRank(a) - processorRank(b)),
    [metrics],
  );

  if (processors.length === 0) {
    return <p className="empty metrics-empty">No metrics yet — start a session.</p>;
  }

  return (
    <div className="metrics">
      {processors.map((processor) => (
        <MetricCard key={processor} processor={processor} series={metrics[processor]} />
      ))}
    </div>
  );
}
