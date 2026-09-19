import React from 'react';
import { numField, txtInput, radioGroup, toggle, SectionCard } from './FormHelpers';

export interface PricingForm {
  currency: string;
  provider: string;
  nordpoolConfigEntryId: string;
  nordpoolEntity: string;
  octopusImportTodayEntity: string;
  octopusImportTomorrowEntity: string;
  octopusExportTodayEntity: string;
  octopusExportTomorrowEntity: string;
  entsoeEntity: string;
  area: string;
  markupRate: number;
  vatMultiplier: number;
  additionalCosts: number;
  taxReduction: number;
  spotMultiplier: number;
  exportSpotMultiplier: number;
  gridFeeEnabled: boolean;
  gridFeeNight: number;
  gridFeeMorning: number;
  gridFeeDay: number;
  gridFeeEvening: number;
}

interface Props {
  form: PricingForm;
  onChange: (f: PricingForm) => void;
}

export function PricingFormSection({ form, onChange }: Props) {
  const isOctopus = form.provider === 'octopus';
  const isEntsoe = form.provider === 'entsoe';
  const currency = isOctopus ? 'GBP' : form.currency;

  const sm = form.spotMultiplier ?? 1.0;
  const esm = form.exportSpotMultiplier ?? 1.0;
  const previewSpot = 1.0;
  const previewBase = (previewSpot * sm + form.markupRate) * form.vatMultiplier + form.additionalCosts;
  const previewBuy = Number(previewBase.toFixed(4));
  const previewSell = Number((previewSpot * esm + form.taxReduction).toFixed(4));

  const gridFeeZones: Array<{ key: keyof PricingForm; label: string; hours: string }> = [
    { key: 'gridFeeNight', label: 'Night', hours: 'Mon–Fri 22:00–05:00 · Sat/Sun 22:00–07:00' },
    { key: 'gridFeeMorning', label: 'Morning', hours: 'Mon–Fri 05:00–07:00' },
    { key: 'gridFeeDay', label: 'Day', hours: 'Mon–Fri 07:00–17:00 · Sat/Sun 07:00–22:00' },
    { key: 'gridFeeEvening', label: 'Evening', hours: 'Mon–Fri 17:00–22:00' },
  ];

  return (
    <div className="space-y-3">
      <SectionCard
        title="Price Source"
        description="Configure where to fetch your electricity prices from. Choose your provider and the relevant Home Assistant entities."
      >
        {radioGroup(
          'Provider',
          [
            { value: 'nordpool_official', label: 'Nord Pool (official HA integration)' },
            { value: 'nordpool_hacs', label: 'Nord Pool (HACS custom sensor)' },
            { value: 'octopus', label: 'Octopus Energy' },
            { value: 'entsoe', label: 'ENTSO-e / Belpex (Transparency Platform)' },
          ],
          form.provider,
          v => onChange({ ...form, provider: v }),
        )}

        {form.provider === 'nordpool_official' && (
          <div className="space-y-3">
            {txtInput('Config Entry ID', form.nordpoolConfigEntryId,
              v => onChange({ ...form, nordpoolConfigEntryId: v }), 'Auto-detected…')}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {txtInput('Price Area', form.area, () => {}, 'Auto-detected…', { readOnly: true })}
              {txtInput('Currency', form.currency,
                v => onChange({ ...form, currency: v }), 'Auto-detected…',
                { readOnly: !!(form.area && form.area.length <= 5 && form.currency) })}
            </div>
          </div>
        )}

        {form.provider === 'nordpool_hacs' && (
          <div className="space-y-3">
            {txtInput('Sensor', form.nordpoolEntity,
              v => onChange({ ...form, nordpoolEntity: v }), 'sensor.nordpool_…')}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {txtInput('Price Area', form.area, () => {}, 'Auto-detected…', { readOnly: true })}
              {txtInput('Currency', form.currency,
                v => onChange({ ...form, currency: v }), 'Auto-detected…',
                { readOnly: !!(form.area && form.area.length <= 5 && form.currency) })}
            </div>
          </div>
        )}

        {isOctopus && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {txtInput('Import today', form.octopusImportTodayEntity,
              v => onChange({ ...form, octopusImportTodayEntity: v }))}
            {txtInput('Import tomorrow', form.octopusImportTomorrowEntity,
              v => onChange({ ...form, octopusImportTomorrowEntity: v }))}
            {txtInput('Export today', form.octopusExportTodayEntity,
              v => onChange({ ...form, octopusExportTodayEntity: v }))}
            {txtInput('Export tomorrow', form.octopusExportTomorrowEntity,
              v => onChange({ ...form, octopusExportTomorrowEntity: v }))}
          </div>
        )}

        {form.provider === 'entsoe' && (
          <div className="space-y-3">
            {txtInput('Sensor', form.entsoeEntity,
              v => onChange({ ...form, entsoeEntity: v }), 'sensor.…_average_electricity_price')}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {txtInput('Currency', form.currency, () => {}, 'Auto-detected…', { readOnly: true })}
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Price Calculation"
        description={isOctopus
          ? 'Octopus prices are already final (VAT-inclusive, GBP/kWh). Only tax reduction applies.'
          : isEntsoe
            ? 'Calculate your actual electricity costs from ENTSO-e/Belpex spot prices. Supports both additive markup and multiplicative spot adjustments.'
            : 'Calculate your actual electricity costs from spot prices, fees and taxes.'}
      >
        {!isOctopus && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {numField('Import Spot Multiplier', form.spotMultiplier,
                v => onChange({ ...form, spotMultiplier: v }),
                { unit: 'factor (1.0 = no adjustment)', min: 0, max: 2.0, step: 0.0001 })}
              {numField('Markup Rate', form.markupRate,
                v => onChange({ ...form, markupRate: v }),
                { unit: `${currency}/kWh (ex-VAT)`, min: 0, step: 0.001 })}
              {numField('VAT Multiplier', form.vatMultiplier,
                v => onChange({ ...form, vatMultiplier: v }),
                { unit: 'factor', min: 1, step: 0.01 })}
              {numField('Additional Costs', form.additionalCosts,
                v => onChange({ ...form, additionalCosts: v }),
                { unit: `${currency}/kWh`, min: 0, step: 0.001 })}
              {numField('Export Spot Multiplier', form.exportSpotMultiplier,
                v => onChange({ ...form, exportSpotMultiplier: v }),
                { unit: 'factor (0 = fixed sell price)', min: 0, max: 2.0, step: 0.0001 })}
              {numField('Export Compensation', form.taxReduction,
                v => onChange({ ...form, taxReduction: v }),
                { unit: `${currency}/kWh`, step: 0.001 })}
            </div>
            {isEntsoe ? (
              <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/40 px-4 py-3 space-y-3 text-xs text-gray-600 dark:text-gray-400">
                <div className="space-y-2">
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Import Spot Multiplier:</span> Contract-specific factor applied to raw spot price. E.g. Luminus Dynamic: 1.0175.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Markup Rate:</span> Fixed costs before VAT: supplier margin, excise duty, grid fees, etc.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">VAT Multiplier:</span> VAT factor on import. 1.06 = 6% (Belgium), 1.21 = 21% (Netherlands).</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Additional Costs:</span> Post-VAT fixed costs. Set to 0 if all costs are already included above.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Export Spot Multiplier:</span> Contract-specific factor on spot for export/injection. E.g. Luminus: 1.018.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Export Compensation:</span> Fixed per-kWh payment or deduction for exported energy. Use negative values for deductions.</div>
                </div>
                <div className="space-y-2 pt-2 border-t border-blue-200 dark:border-blue-700">
                  <p className="font-medium text-blue-900 dark:text-blue-200">How the raw spot price is converted:</p>
                  <p className="pl-2 border-l-2 border-blue-300 dark:border-blue-600"><strong>Buy price:</strong> (spot × import multiplier + markup) × VAT + additional costs</p>
                  <p className="pl-2 border-l-2 border-blue-300 dark:border-blue-600"><strong>Sell price:</strong> spot × export multiplier + export compensation</p>
                </div>
              </div>
            ) : (
              <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/40 px-4 py-3 space-y-3 text-xs text-gray-600 dark:text-gray-400">
                <div className="space-y-2">
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Import Spot Multiplier:</span> Contract-specific factor on the raw spot price. Leave at 1.0 unless your contract scales spot.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Markup Rate:</span> Energy provider margin fee. E.g. Tibber 0.08 (8 öre/kWh), Ellevio ~0.15. Applied before VAT.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">VAT Multiplier:</span> VAT factor. 1.25 = 25% (Sweden/Norway), 1.20 = 20% (UK/EU).</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Additional Costs:</span> Grid transfer fee + energy tax (sum ex-VAT, then VAT applied). E.g. E.ON: (0.2584 + 0.3600) × 1.25 = 0.773 SEK/kWh.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Export Spot Multiplier:</span> Factor on spot for exported energy. <strong>Set it to 0 for a fixed sell price</strong> — the spot term drops out and you earn exactly the Export Compensation below, in every period.</div>
                  <div><span className="font-medium text-blue-900 dark:text-blue-200">Export Compensation:</span> Per-kWh payment from grid operator (Nätnytta) when selling surplus electricity. Check your energy bill under "Producent/Självfaktura". E.g. E.ON: 0.1988 (19.88 öre/kWh). With the export multiplier at 0 this becomes your flat sell price — e.g. 0.0726 EUR/kWh in Lithuania.</div>
                </div>

                <div className="space-y-2 pt-2 border-t border-blue-200 dark:border-blue-700">
                  <p className="font-medium text-blue-900 dark:text-blue-200">How the raw spot price is converted:</p>
                  <p className="pl-2 border-l-2 border-blue-300 dark:border-blue-600"><strong>Buy price:</strong> (raw spot × import multiplier + markup) × VAT multiplier + grid fees</p>
                  <p className="pl-2 border-l-2 border-blue-300 dark:border-blue-600"><strong>Sell price:</strong> raw spot × export multiplier + export compensation</p>
                  <p className="text-gray-500 dark:text-gray-500 italic">Note: Markup is added before VAT (ex-VAT), while grid fees already include VAT.</p>
                </div>
              </div>
            )}
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3 space-y-3">
              {toggle('Time-of-use grid fee', form.gridFeeEnabled,
                v => onChange({ ...form, gridFeeEnabled: v }))}
              <p className="text-xs text-gray-500 dark:text-gray-400">
                For operators that bill distribution by time of day (e.g. ESO in Lithuania).
                The rate for each period is added on top of Additional Costs, so enter it as a
                final per-kWh amount including VAT. Times are your Home Assistant local time.
              </p>
              {form.gridFeeEnabled && (
                <div className="space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {gridFeeZones.map(zone => (
                      <div key={zone.key}>
                        {numField(`${zone.label} Rate`, form[zone.key] as number,
                          v => onChange({ ...form, [zone.key]: v }),
                          { unit: `${currency}/kWh (incl. VAT)`, min: 0, step: 0.00001 })}
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{zone.hours}</p>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 italic">
                    Public holidays are billed on the weekend schedule by most operators, but are
                    not detected here — on those days the morning and evening rates are still
                    applied.
                  </p>
                </div>
              )}
            </div>
            <div className="rounded-lg bg-gray-50 dark:bg-gray-700/50 px-4 py-3 text-sm space-y-1.5">
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Preview at spot = 1.00
              </p>
              {form.gridFeeEnabled ? (
                gridFeeZones.map(zone => (
                  <div key={zone.key} className="flex justify-between font-medium">
                    <span className="text-gray-700 dark:text-gray-200">Buy price · {zone.label}</span>
                    <span className="text-blue-600 dark:text-blue-400">
                      {(previewBase + (form[zone.key] as number)).toFixed(2)} {currency}/kWh
                    </span>
                  </div>
                ))
              ) : (
                <div className="flex justify-between font-medium">
                  <span className="text-gray-700 dark:text-gray-200">Buy price</span>
                  <span className="text-blue-600 dark:text-blue-400">
                    {previewBuy.toFixed(2)} {currency}/kWh
                  </span>
                </div>
              )}
              <div className="flex justify-between font-medium">
                <span className="text-gray-700 dark:text-gray-200">Sell price</span>
                <span className="text-green-600 dark:text-green-400">
                  {previewSell.toFixed(2)} {currency}/kWh
                </span>
              </div>
            </div>
          </>
        )}
        {isOctopus && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {numField('Tax Reduction', form.taxReduction,
              v => onChange({ ...form, taxReduction: v }),
              { unit: 'GBP/kWh credit on sold energy', min: 0, step: 0.001 })}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
