import { init } from 'klinecharts'

const chart = init('createIndicator-y-axis-chart')
chart.createIndicator({ name: 'MA', paneId: 'candle_pane', yAxisId: 'ma_axis' })
chart.overrideYAxis({
  paneId: 'candle_pane',
  id: 'ma_axis',
  createTicks: ({ defaultTicks }) => {
    return defaultTicks.map(
      ({ coord, value, text }) => ({ coord, value, text: `$${text}` })
    )
  }
})

chart.setSymbol({ ticker: 'TestSymbol' })
chart.setPeriod({ span: 1, type: 'day' })
chart.setDataLoader({
  getBars: ({
    callback
  }) => {
    fetch('https://klinecharts.com/datas/kline.json')
      .then(res => res.json())
      .then(dataList => {
        callback(dataList)
      })
  }
})
