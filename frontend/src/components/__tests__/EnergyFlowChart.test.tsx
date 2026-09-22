import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { ReactElement } from 'react'

// jsdom has no layout engine, so ResponsiveContainer measures its parent as
// 0x0 and recharts draws nothing. Pinning a size makes the series reach the
// DOM, which is what the visibility tests below observe.
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) => (
      <actual.ResponsiveContainer width={800} height={400}>{children}</actual.ResponsiveContainer>
    ),
  }
})

import { EnergyFlowChart, getSellPriceTooltipText, getHomeLoadSplit } from '../EnergyFlowChart'
import type { HourlyData } from '../../types'

const dailyViewData: HourlyData[] = [
  {
    period: 0,
    dataSource: 'actual',
    solarProduction: { value: 0, display: '0', unit: 'kWh', text: '0 kWh' },
    homeConsumption: { value: 1, display: '1', unit: 'kWh', text: '1 kWh' },
    buyPrice: { value: 0.21, display: '0.21', unit: 'EUR', text: '0.21 EUR' },
    sellPrice: { value: -0.03, display: '-0.03', unit: 'EUR', text: '-0.03 EUR' },
  },
]

describe('EnergyFlowChart sell price toggle', () => {
  it('renders the sell price switch reflecting showSellPrice', () => {
    render(
      <EnergyFlowChart
        dailyViewData={dailyViewData}
        currentHour={0}
        resolution="hourly"
        showSellPrice={false}
        onShowSellPriceChange={vi.fn()}
      />
    )

    expect(screen.getByRole('switch', { name: /show sell price/i })).toHaveAttribute('aria-checked', 'false')
  })

  it('calls onShowSellPriceChange when clicked', () => {
    const onShowSellPriceChange = vi.fn()
    render(
      <EnergyFlowChart
        dailyViewData={dailyViewData}
        currentHour={0}
        resolution="hourly"
        showSellPrice={false}
        onShowSellPriceChange={onShowSellPriceChange}
      />
    )

    fireEvent.click(screen.getByRole('switch', { name: /show sell price/i }))

    expect(onShowSellPriceChange).toHaveBeenCalledWith(true)
  })
})

describe('EnergyFlowChart series visibility', () => {
  const fv = (value: number) => ({ value, display: String(value), unit: 'kWh', text: `${value} kWh` })

  // Every series carries a non-zero value, so each one is genuinely drawn and
  // its disappearance means it was hidden rather than never rendered.
  const allSeriesData: HourlyData[] = [
    {
      period: 0,
      dataSource: 'actual',
      solarProduction: fv(2),
      homeConsumption: fv(1),
      batteryCharged: fv(0.5),
      batteryDischarged: fv(0.3),
      gridImported: fv(0.8),
      gridExported: fv(0.2),
      buyPrice: { value: 0.21, display: '0.21', unit: 'EUR', text: '0.21 EUR' },
      sellPrice: { value: 0.05, display: '0.05', unit: 'EUR', text: '0.05 EUR' },
    },
  ]

  // recharts labels each drawn series with its `name`, which is the same text
  // the legend shows -- so this asks "is this series on the chart?".
  const isDrawn = (container: HTMLElement, seriesName: string) =>
    container.querySelector(`[name="${seriesName}"]`) !== null

  const renderChart = (showSellPrice = false) =>
    render(
      <EnergyFlowChart
        dailyViewData={allSeriesData}
        currentHour={0}
        resolution="hourly"
        showSellPrice={showSellPrice}
        onShowSellPriceChange={vi.fn()}
      />
    )

  it('starts with every legend entry pressed', () => {
    renderChart()

    for (const name of [/solar production/i, /battery charge/i, /grid import/i, /home load/i, /buy price/i]) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    }
  })

  it('removes a series from the chart when its legend entry is clicked', () => {
    const { container } = renderChart()
    expect(isDrawn(container, 'Solar Production')).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: /solar production/i }))

    expect(isDrawn(container, 'Solar Production')).toBe(false)
    expect(screen.getByRole('button', { name: /solar production/i })).toHaveAttribute('aria-pressed', 'false')
  })

  it('brings a hidden series back on a second click', () => {
    const { container } = renderChart()

    fireEvent.click(screen.getByRole('button', { name: /solar production/i }))
    fireEvent.click(screen.getByRole('button', { name: /solar production/i }))

    expect(isDrawn(container, 'Solar Production')).toBe(true)
  })

  it('leaves the other series drawn when one is hidden', () => {
    const { container } = renderChart()

    fireEvent.click(screen.getByRole('button', { name: /solar production/i }))

    expect(isDrawn(container, 'Grid Import')).toBe(true)
    expect(isDrawn(container, 'Home Load')).toBe(true)
    expect(isDrawn(container, 'Buy Price')).toBe(true)
  })

  it('hides both directions at once for the grouped Battery entry', () => {
    const { container } = renderChart()

    fireEvent.click(screen.getByRole('button', { name: /battery charge/i }))

    expect(isDrawn(container, 'Battery Discharge')).toBe(false)
    expect(isDrawn(container, 'Battery Charge')).toBe(false)
  })

  it('hides both directions at once for the grouped Grid entry', () => {
    const { container } = renderChart()

    fireEvent.click(screen.getByRole('button', { name: /grid import/i }))

    expect(isDrawn(container, 'Grid Import')).toBe(false)
    expect(isDrawn(container, 'Grid Export')).toBe(false)
  })

  it('hides each price line independently', () => {
    const { container } = renderChart(true)

    fireEvent.click(screen.getByRole('button', { name: /buy price/i }))

    expect(isDrawn(container, 'Buy Price')).toBe(false)
    expect(isDrawn(container, 'Sell Price')).toBe(true)
  })

  it('offers a sell price entry only while the sell price line is included', () => {
    const { unmount } = renderChart(false)
    expect(screen.queryByRole('button', { name: /sell price/i })).toBeNull()
    unmount()

    renderChart(true)
    expect(screen.getByRole('button', { name: /sell price/i })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('EnergyFlowChart forecast outline visibility', () => {
  const fv = (value: number) => ({ value, display: String(value), unit: 'kWh', text: `${value} kWh` })

  // An elapsed period that declared a plan -- the case that draws the dotted
  // frozen-forecast outline over the measured consumption stack.
  const plannedData: HourlyData[] = [
    {
      period: 0,
      dataSource: 'actual',
      solarProduction: fv(0),
      homeConsumption: fv(2.5),
      predictedResidualLoad: fv(0.25),
      plannedManagedLoad: fv(1.0),
      buyPrice: { value: 0.21, display: '0.21', unit: 'EUR', text: '0.21 EUR' },
    },
  ]

  const outlines = (container: HTMLElement) =>
    container.querySelectorAll('rect[stroke-dasharray="2 2.5"]').length

  const renderPlanned = () =>
    render(
      <EnergyFlowChart
        dailyViewData={plannedData}
        currentHour={0}
        resolution="hourly"
        showSellPrice={false}
        onShowSellPriceChange={vi.fn()}
      />
    )

  it('drops the outline when Home Load is hidden, since it cannot restack', () => {
    const { container } = renderPlanned()
    expect(outlines(container)).toBe(1)

    fireEvent.click(screen.getByRole('button', { name: /home load/i }))

    expect(outlines(container)).toBe(0)
  })

  it('drops the outline when Planned Load is hidden', () => {
    const { container } = renderPlanned()

    fireEvent.click(screen.getByRole('button', { name: /planned load/i }))

    expect(outlines(container)).toBe(0)
  })
})

describe('getHomeLoadSplit', () => {
  const fv = (value: number) => ({ value, display: String(value), unit: 'kWh', text: `${value} kWh` })

  it('splits a future period into residual + planned that stack to the combined curve', () => {
    const split = getHomeLoadSplit(
      { dataSource: 'predicted', predictedResidualLoad: fv(0.25), plannedManagedLoad: fv(1.0) },
      1.25
    )
    // Consumption is drawn below the zero axis, so both are negative.
    expect(split.residual).toBe(-0.25)
    expect(split.planned).toBe(-1.0)
    expect(split.residual + split.planned).toBe(-1.25)
    expect(split.forecastTotal).toBeNull()  // no outline for the future -- it's already the solid stack
  })

  it('keeps an elapsed period at its measured total and exposes the frozen forecast shape', () => {
    // Measured 2.5 kWh, but the plan for that period was residual 0.25 + planned 1.0.
    const split = getHomeLoadSplit(
      { dataSource: 'actual', predictedResidualLoad: fv(0.25), plannedManagedLoad: fv(1.0) },
      2.5
    )
    expect(split.residual).toBe(-2.5)   // stack shows what was on the meter
    expect(split.planned).toBe(0)
    expect(split.forecastResidual).toBe(-0.25)  // outline's seam = where residual ended
    expect(split.forecastTotal).toBe(-1.25)     // outline's outer edge = what was planned
  })

  it('keeps the forecast outline null for an elapsed period with no plan declared', () => {
    // Ordinary hour, no overlay -- nothing to compare, so no outline.
    const split = getHomeLoadSplit(
      { dataSource: 'actual', predictedResidualLoad: fv(0.375), plannedManagedLoad: fv(0) },
      0.375
    )
    expect(split.forecastResidual).toBeNull()
    expect(split.forecastTotal).toBeNull()
  })

  it('falls back to all-residual when the breakdown is absent', () => {
    const split = getHomeLoadSplit(undefined, 1.25)
    expect(split.residual).toBe(-1.25)
    expect(split.planned).toBe(0)
    expect(split.forecastResidual).toBeNull()
    expect(split.forecastTotal).toBeNull()
  })

  it('keeps a negative planned block (away-from-home subtract) intact for the future', () => {
    const split = getHomeLoadSplit(
      { dataSource: 'predicted', predictedResidualLoad: fv(1.0), plannedManagedLoad: fv(-0.4) },
      0.6
    )
    expect(split.residual).toBe(-1.0)
    expect(split.planned).toBe(0.4)
  })
})

describe('getSellPriceTooltipText', () => {
  const sellPriceFormatted = { value: -0.03, display: '-0.03', unit: 'EUR', text: '-0.03 EUR' }

  it('returns null when there is no sell price data', () => {
    expect(getSellPriceTooltipText({})).toBeNull()
  })

  it('returns the formatted sell price text whenever it is present, regardless of the line toggle', () => {
    expect(getSellPriceTooltipText({ sellPriceFormatted })).toBe('-0.03 EUR')
  })
})
