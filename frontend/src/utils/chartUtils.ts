import type { EnergyFlowLineStyle } from '../types';

/** Pick a nice step size and generate evenly-spaced ticks for a Y-axis range. */
export function niceYAxis(
  min: number,
  max: number,
  targetTicks = 5,
): { yMin: number; yMax: number; ceiling: number; ticks: number[] } {
  const range = max - min || 0.1;
  const niceSteps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];
  const rawStep = range / (targetTicks - 1);
  const step = niceSteps.find(s => s >= rawStep) ?? Math.ceil(rawStep);
  const yMin = Math.floor(min / step) * step;
  const yMax = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = yMin; v <= yMax + step * 0.01; v += step) {
    ticks.push(Math.round(v * 1000) / 1000);
  }
  return { yMin, yMax, ceiling: yMax, ticks };
}

/** Coerce a stored Energy Flow curve type into one the chart can use.
 *
 * The value is persisted server-side and reaches recharts' `type` prop
 * verbatim. recharts ignores an unrecognised type silently rather than
 * erroring, so anything unexpected — a store predating the setting, a
 * hand-edited settings file, a failed settings fetch — must resolve to the
 * 'step' default here rather than reaching the chart.
 *
 * Only an explicit 'monotone' opts out. The backend validates the same set
 * (ENERGY_FLOW_LINE_STYLES in settings_store.py); this is the client-side
 * backstop for values that never went through a PATCH.
 */
export function normalizeEnergyFlowLineStyle(value: unknown): EnergyFlowLineStyle {
  return value === 'monotone' ? 'monotone' : 'step';
}
