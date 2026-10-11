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
  octopusFreeImportPrice: 0,
  octopusPowerUpCalendarEntity: '',
  octopusPowerDownEnabled: false,
  octopusPowerDownCalendarEntity: '',
  octopusPowerDownExportKw: 1.0,
  octopusPowerDownExportMinutes: 15,
  octopusPowerDownEventsEntity: '',
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

  it('offers both spot multipliers on Nord Pool, not only on ENTSO-e', () => {
    render(<PricingFormSection form={BASE_FORM} onChange={vi.fn()} />);

    expect(screen.getByLabelText(/Import Spot Multiplier/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Export Spot Multiplier/i)).toBeInTheDocument();
  });

  it('accepts an export multiplier of 0, which a min of 0.5 used to block', () => {
    const onChange = vi.fn();
    render(<PricingFormSection form={BASE_FORM} onChange={onChange} />);

    const input = screen.getByLabelText(/Export Spot Multiplier/i);
    expect(input).toHaveAttribute('min', '0');

    fireEvent.change(input, { target: { value: '0' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ exportSpotMultiplier: 0 }),
    );
  });

  it('previews a flat sell price when the export multiplier is 0', () => {
    // The Lithuanian case: spot drops out, leaving the compensation alone.
    render(
      <PricingFormSection
        form={{ ...BASE_FORM, exportSpotMultiplier: 0, taxReduction: 0.0726 }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText('0.07 SEK/kWh')).toBeInTheDocument();
  });

  it('still tracks spot when the export multiplier is 1', () => {
    render(
      <PricingFormSection
        form={{ ...BASE_FORM, exportSpotMultiplier: 1, taxReduction: 0.0726 }}
        onChange={vi.fn()}
      />,
    );

    // preview spot is 1.00, so 1.00 * 1 + 0.0726
    expect(screen.getByText('1.07 SEK/kWh')).toBeInTheDocument();
  });

  it('hides Power Down sessions for a non-Octopus provider', () => {
    render(<PricingFormSection form={BASE_FORM} onChange={vi.fn()} />);
    expect(screen.queryByText(/Octoplus Power Down sessions/i)).not.toBeInTheDocument();
  });

  it('shows Power Down sessions for the Octopus provider', () => {
    render(
      <PricingFormSection form={{ ...BASE_FORM, provider: 'octopus' }} onChange={vi.fn()} />,
    );
    expect(screen.getByText(/Octoplus Power Down sessions/i)).toBeInTheDocument();
  });

  it('derives the per-session kWh from export power and duration', () => {
    render(
      <PricingFormSection
        form={{
          ...BASE_FORM,
          provider: 'octopus',
          octopusPowerDownExportKw: 2,
          octopusPowerDownExportMinutes: 30,
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/1\.00 kWh per session/)).toBeInTheDocument();
  });

  it('sends the Power Down fields to onChange when edited', () => {
    const onChange = vi.fn();
    render(
      <PricingFormSection form={{ ...BASE_FORM, provider: 'octopus' }} onChange={onChange} />,
    );

    fireEvent.click(screen.getByLabelText(/Export during Power Down sessions/i));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ octopusPowerDownEnabled: true }),
    );

    fireEvent.change(screen.getByLabelText(/Export power/i), { target: { value: '1.5' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ octopusPowerDownExportKw: 1.5 }),
    );

    fireEvent.change(screen.getByLabelText(/Export duration/i), { target: { value: '30' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ octopusPowerDownExportMinutes: 30 }),
    );

    fireEvent.change(screen.getByLabelText(/Power Down events entity/i), {
      target: { value: 'event.octopus_energy_a_982b3d40_octoplus_power_down_events' },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        octopusPowerDownEventsEntity: 'event.octopus_energy_a_982b3d40_octoplus_power_down_events',
      }),
    );
  });

  it('does not show the disabled-calendar notice when not disabled', () => {
    render(
      <PricingFormSection form={{ ...BASE_FORM, provider: 'octopus' }} onChange={vi.fn()} />,
    );
    expect(screen.queryByTestId('power-down-calendar-disabled-warning')).not.toBeInTheDocument();
  });

  it('shows the disabled-calendar notice when HA has the entity disabled', () => {
    render(
      <PricingFormSection
        form={{
          ...BASE_FORM,
          provider: 'octopus',
          octopusPowerDownCalendarEntity: 'calendar.octopus_energy_a_982b3d40_octoplus_power_down',
        }}
        onChange={vi.fn()}
        powerDownCalendarDisabledBy="integration"
      />,
    );
    const notice = screen.getByTestId('power-down-calendar-disabled-warning');
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent('calendar.octopus_energy_a_982b3d40_octoplus_power_down');
  });
});
