import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PricingFormSection } from '../PricingFormSection';
import type { PricingForm } from '../PricingFormSection';

const BASE_FORM: PricingForm = {
  currency: 'SEK',
  provider: 'nordpool_official',
  nordpoolConfigEntryId: '',
  nordpoolEntity: '',
  octopusImportTodayEntity: '',
  octopusImportTomorrowEntity: '',
  octopusExportTodayEntity: '',
  octopusExportTomorrowEntity: '',
  entsoeEntity: '',
  area: '',
  markupRate: 0,
  vatMultiplier: 1.25,
  additionalCosts: 0,
  taxReduction: 0,
  spotMultiplier: 1.0,
  exportSpotMultiplier: 1.0,
  gridFeeEnabled: false,
  gridFeeNight: 0.06292,
  gridFeeMorning: 0.08349,
  gridFeeDay: 0.10406,
  gridFeeEvening: 0.14641,
};

describe('PricingFormSection', () => {
  it('allows a negative Export Compensation value (nätnytta can be a cost, not just a credit)', () => {
    const onChange = vi.fn();
    render(<PricingFormSection form={BASE_FORM} onChange={onChange} />);

    const input = screen.getByLabelText(/Export Compensation/i);
    expect(input).not.toHaveAttribute('min', '0');

    fireEvent.change(input, { target: { value: '-0.05' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ taxReduction: -0.05 }));
  });

  it('reflects a negative Export Compensation in the sell price preview', () => {
    render(<PricingFormSection form={{ ...BASE_FORM, taxReduction: -0.05 }} onChange={vi.fn()} />);

    expect(screen.getByText('0.95 SEK/kWh')).toBeInTheDocument();
  });

  it('hides the per-zone rates until the time-of-use grid fee is switched on', () => {
    render(<PricingFormSection form={BASE_FORM} onChange={vi.fn()} />);

    expect(screen.queryByLabelText(/Evening Rate/i)).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /Time-of-use grid fee/i })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('shows a separate buy price per zone once enabled', () => {
    render(
      <PricingFormSection form={{ ...BASE_FORM, gridFeeEnabled: true }} onChange={vi.fn()} />,
    );

    // Buy price at spot 1.00 is 1.25 before the fee (VAT 1.25, no markup).
    expect(screen.getByText('1.31 SEK/kWh')).toBeInTheDocument(); // night
    expect(screen.getByText('1.33 SEK/kWh')).toBeInTheDocument(); // morning
    expect(screen.getByText('1.35 SEK/kWh')).toBeInTheDocument(); // day
    expect(screen.getByText('1.40 SEK/kWh')).toBeInTheDocument(); // evening
  });

  it('edits the rate for a single zone without touching the others', () => {
    const onChange = vi.fn();
    render(
      <PricingFormSection form={{ ...BASE_FORM, gridFeeEnabled: true }} onChange={onChange} />,
    );

    fireEvent.change(screen.getByLabelText(/Evening Rate/i), { target: { value: '0.2' } });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ gridFeeEvening: 0.2, gridFeeNight: 0.06292 }),
    );
  });
});
