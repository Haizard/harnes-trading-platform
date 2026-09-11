import { init } from 'klinecharts'

const chart = init('getYAxes-chart')

chart.createYAxis({
  id: 'left_axis',
  position: 'left',
  inside: false
})

const yAxes = chart.getYAxes({
  paneId: 'candle_pane'
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
