import { init } from 'klinecharts'

const chart = init('removeYAxis-chart')

const yAxisId = chart.createYAxis({
  id: 'left_axis',
  position: 'left',
  inside: false
})

chart.removeYAxis({
  id: yAxisId
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
