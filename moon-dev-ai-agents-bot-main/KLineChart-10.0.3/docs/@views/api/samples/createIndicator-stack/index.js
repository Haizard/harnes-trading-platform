import { init } from 'klinecharts'

const chart = init('createIndicator-stack-chart')
chart.createIndicator({ name: 'MA', paneId: 'candle_pane' })
chart.createIndicator({ name: 'EMA', paneId: 'candle_pane' }, true)

const paneId = 'boll_pane'
chart.createIndicator({ name: 'BOLL', paneId })
chart.createIndicator({ name: 'EMA', paneId }, true)

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
