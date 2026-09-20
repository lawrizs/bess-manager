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
});
