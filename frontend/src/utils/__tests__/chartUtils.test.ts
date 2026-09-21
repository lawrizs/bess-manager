import { describe, it, expect } from 'vitest';

import { normalizeEnergyFlowLineStyle } from '../chartUtils';

// The Energy Flow curve type is persisted server-side and handed to recharts'
// `type` prop verbatim. recharts ignores an unrecognised type silently rather
// than erroring, so a bad value would leave the chart looking subtly wrong
// with nothing to point at. Everything that is not an explicit 'monotone'
// must resolve to the 'step' default before it reaches the chart.
describe('normalizeEnergyFlowLineStyle', () => {
  it('keeps an explicit monotone', () => {
    expect(normalizeEnergyFlowLineStyle('monotone')).toBe('monotone');
  });

  it('keeps an explicit step', () => {
    expect(normalizeEnergyFlowLineStyle('step')).toBe('step');
  });

  it.each([
    ['undefined', undefined], // store predates the setting
    ['null', null], // settings fetch failed
    ['empty string', ''],
    ['unknown curve name', 'wobbly'],
    ['a real recharts type this UI does not offer', 'linear'],
    ['wrong case', 'Monotone'], // recharts is case-sensitive
    ['a number', 3],
    ['an object', { energyFlowLineStyle: 'monotone' }], // whole section passed by mistake
  ])('falls back to step for %s', (_label, value) => {
    expect(normalizeEnergyFlowLineStyle(value)).toBe('step');
  });
});
