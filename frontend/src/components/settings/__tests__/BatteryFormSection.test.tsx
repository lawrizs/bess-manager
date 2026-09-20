import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { BatteryFormSection } from '../BatteryFormSection';
import type { BatteryForm } from '../BatteryFormSection';

const BASE_FORM: BatteryForm = {
  totalCapacity: 30,
  minSoc: 10,
  maxSoc: 100,
  maxChargePowerKw: 15,
  maxDischargePowerKw: 15,
  maxBatteryToGridEnabled: false,
  maxBatteryToGridPowerKw: 0,
  cycleCostPerKwh: 0.4,
  efficiencyCharge: 97,
  efficiencyDischarge: 95,
  temperatureDeratingEnabled: false,
  inverterMaxAcPowerKw: 0,
  inverterAcPowerMargin: 0.05,
  exportCurtailmentEnabled: false,
  exportCurtailmentPriceFloor: 0,
};

describe('BatteryFormSection', () => {
  it('offers charge and discharge power as two separate fields', () => {
    render(<BatteryFormSection form={BASE_FORM} onChange={vi.fn()} />);

    expect(screen.getByLabelText(/Max Charge Power/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Max Discharge Power/i)).toBeInTheDocument();
  });

  it('edits the charge limit without touching the discharge limit', () => {
    const onChange = vi.fn();
    render(<BatteryFormSection form={BASE_FORM} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(/Max Charge Power/i), {
      target: { value: '5' },
    });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxChargePowerKw: 5, maxDischargePowerKw: 15 }),
    );
  });

  it('edits the discharge limit without touching the charge limit', () => {
    const onChange = vi.fn();
    render(<BatteryFormSection form={BASE_FORM} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(/Max Discharge Power/i), {
      target: { value: '5' },
    });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxChargePowerKw: 15, maxDischargePowerKw: 5 }),
    );
  });

  it('displays an asymmetric pair as configured, not collapsed to one value', () => {
    render(
      <BatteryFormSection
        form={{ ...BASE_FORM, maxChargePowerKw: 5, maxDischargePowerKw: 10 }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/Max Charge Power/i)).toHaveValue(5);
    expect(screen.getByLabelText(/Max Discharge Power/i)).toHaveValue(10);
  });


  it('hides the battery-to-grid cap until it is switched on', () => {
    render(<BatteryFormSection form={BASE_FORM} onChange={vi.fn()} />);

    expect(screen.queryByLabelText(/Max Battery-to-Grid Power/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole('switch', { name: /Limit battery export to grid/i }),
    ).toHaveAttribute('aria-checked', 'false');
  });

  it('accepts 0, which means the battery never exports', () => {
    const onChange = vi.fn();
    render(
      <BatteryFormSection
        form={{ ...BASE_FORM, maxBatteryToGridEnabled: true, maxBatteryToGridPowerKw: 3 }}
        onChange={onChange}
      />,
    );

    const input = screen.getByLabelText(/Max Battery-to-Grid Power/i);
    expect(input).toHaveAttribute('min', '0');

    fireEvent.change(input, { target: { value: '0' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxBatteryToGridPowerKw: 0, maxBatteryToGridEnabled: true }),
    );
  });

  it('leaves the charge and discharge limits alone', () => {
    const onChange = vi.fn();
    render(
      <BatteryFormSection
        form={{ ...BASE_FORM, maxBatteryToGridEnabled: true }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Max Battery-to-Grid Power/i), {
      target: { value: '2.5' },
    });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        maxBatteryToGridPowerKw: 2.5,
        maxChargePowerKw: 15,
        maxDischargePowerKw: 15,
      }),
    );
  });
});
