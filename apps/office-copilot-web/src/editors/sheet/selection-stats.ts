/** 选区数值特征：均值/中位/环比/趋势，供读数据弹窗与 AI surrounding。 */

export type SelectionStats = {
  count: number;
  sum: number | null;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
  stdev: number | null;
  /** 相邻点环比（后/前 - 1）；非有限值跳过 */
  mom_rates: number[];
  mom_mean: number | null;
  mom_median: number | null;
  /** 简单线性趋势：上升 / 下降 / 持平 / 不足 */
  trend: 'up' | 'down' | 'flat' | 'insufficient';
  trend_slope: number | null;
};

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

function medianOf(sorted: number[]): number | null {
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2) return sorted[mid];
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

/** 从选区矩阵抽出有限数值（行优先）。 */
export function extractNumbers(matrix: unknown[][]): number[] {
  const out: number[] = [];
  for (const row of matrix || []) {
    for (const cell of row || []) {
      if (typeof cell === 'number' && Number.isFinite(cell)) {
        out.push(cell);
        continue;
      }
      if (typeof cell === 'string') {
        const s = cell.trim().replace(/,/g, '');
        if (!s) continue;
        const pct = /%$/.test(s);
        const raw = pct ? s.slice(0, -1) : s;
        if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) continue;
        const n = Number(raw);
        if (!Number.isFinite(n)) continue;
        out.push(pct ? n / 100 : n);
      }
    }
  }
  return out;
}

export function computeSelectionStats(numbers: number[]): SelectionStats {
  const count = numbers.length;
  if (!count) {
    return {
      count: 0,
      sum: null,
      mean: null,
      median: null,
      min: null,
      max: null,
      stdev: null,
      mom_rates: [],
      mom_mean: null,
      mom_median: null,
      trend: 'insufficient',
      trend_slope: null,
    };
  }

  const sum = numbers.reduce((a, b) => a + b, 0);
  const mean = sum / count;
  const sorted = [...numbers].sort((a, b) => a - b);
  const median = medianOf(sorted)!;
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const variance = numbers.reduce((a, b) => a + (b - mean) ** 2, 0) / count;
  const stdev = Math.sqrt(variance);

  const mom_rates: number[] = [];
  for (let i = 1; i < numbers.length; i++) {
    const prev = numbers[i - 1];
    if (!Number.isFinite(prev) || prev === 0) continue;
    const rate = numbers[i] / prev - 1;
    if (Number.isFinite(rate)) mom_rates.push(rate);
  }
  const momSorted = [...mom_rates].sort((a, b) => a - b);
  const mom_mean = mom_rates.length ? mom_rates.reduce((a, b) => a + b, 0) / mom_rates.length : null;
  const mom_median = medianOf(momSorted);

  let trend_slope: number | null = null;
  let trend: SelectionStats['trend'] = 'insufficient';
  if (count >= 2) {
    const n = count;
    let sumX = 0;
    let sumY = 0;
    let sumXY = 0;
    let sumXX = 0;
    for (let i = 0; i < n; i++) {
      sumX += i;
      sumY += numbers[i];
      sumXY += i * numbers[i];
      sumXX += i * i;
    }
    const denom = n * sumXX - sumX * sumX;
    trend_slope = denom === 0 ? 0 : (n * sumXY - sumX * sumY) / denom;
    const amp = Math.max(Math.abs(mean), Math.abs(max - min), 1e-9);
    const rel = Math.abs(trend_slope) / amp;
    if (rel < 0.01) trend = 'flat';
    else trend = trend_slope > 0 ? 'up' : 'down';
  }

  return {
    count,
    sum: round6(sum),
    mean: round6(mean),
    median: round6(median),
    min: round6(min),
    max: round6(max),
    stdev: round6(stdev),
    mom_rates: mom_rates.map(round6),
    mom_mean: mom_mean == null ? null : round6(mom_mean),
    mom_median: mom_median == null ? null : round6(mom_median),
    trend,
    trend_slope: trend_slope == null ? null : round6(trend_slope),
  };
}

export function formatStatsBrief(stats: SelectionStats): string {
  if (!stats.count) return '选区内无可用数值';
  const parts = [
    `n=${stats.count}`,
    `均值=${stats.mean}`,
    `中位=${stats.median}`,
    `最小=${stats.min}`,
    `最大=${stats.max}`,
  ];
  if (stats.mom_median != null) parts.push(`环比中位=${stats.mom_median}`);
  if (stats.mom_mean != null) parts.push(`环比均值=${stats.mom_mean}`);
  const trendLabel = { up: '上升', down: '下降', flat: '持平', insufficient: '不足' }[stats.trend];
  parts.push(`趋势=${trendLabel}`);
  return parts.join(' · ');
}
