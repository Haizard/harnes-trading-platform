# 🛠️ 从 V9 到 V10
本文档帮助你从 klinecharts 9.x 升级到当前 10.x 版本。如果你正在使用 8.x 或更老版本，请先参考 [v8 到 v9](https://v9.klinecharts.com/guide/v8-to-v9) 升级到 9.x。

## 升级重点
+ 10.x 重新整理了初始化配置、数据加载、坐标轴、指标、覆盖物、tooltip 和样式配置。
+ 推荐先升级到 9.x 最新版本，再一次性处理本文列出的 10.x 破坏性变更。
+ 坐标轴配置不再放在样式里。默认 y 轴放在 `init` 的 `layout.yAxis`，运行时修改使用 `overrideYAxis`，多 y 轴使用 `createYAxis`、`removeYAxis` 和 `getYAxes`。
+ 数据更新相关旧 API 被统一收敛为 `setDataLoader(loader)`。
+ 指标所在窗口和绑定 y 轴直接写在 `indicator.paneId` 和 `indicator.yAxisId` 上。

## 初始化配置
### formatter
9.x 中的 `customApi` 改为 `formatter`，`formatDate` 参数改为对象。

```js
// 9.x
init('chart', {
  customApi: {
    formatDate: (dateTimeFormat, timestamp, format, type) => ''
  }
})

// 10.x
init('chart', {
  formatter: {
    formatDate: ({ dateTimeFormat, timestamp, template, type }) => ''
  }
})
```

实例 API 也需要同步替换：

```js
// 9.x
chart.setCustomApi(api)
chart.getCustomApi()

// 10.x
chart.setFormatter(formatter)
chart.getFormatter()
```

### layout
`options.layout` 调整为默认布局配置对象，不再使用 v9 的数组式窗口布局，也不再使用 beta 期间的 `basicParams`、`panes` 写法。

```js
init('chart', {
  layout: {
    barSpaceLimit: {
      min: 2,
      max: 40
    },
    pane: {
      height: 120,
      minHeight: 40,
      dragEnabled: true,
      order: 10,
      state: 'normal'
    },
    yAxis: {
      position: 'right',
      inside: false,
      scrollZoomEnabled: true
    }
  }
})
```

窗口运行时属性使用 `setPaneOptions(options)`：

```js
chart.setPaneOptions({
  id: 'pane_1',
  height: 160,
  dragEnabled: true,
  order: 20
})
```

坐标轴运行时属性不要再放到 `setPaneOptions`，请使用 `overrideYAxis(options)` 或 `overrideXAxis(options)`。

### 千分符和小数折叠
```js
// 9.x
init('chart', {
  decimalFoldThreshold: 10000
})

// 10.x
init('chart', {
  thousandsSeparator: {
    sign: ','
  },
  decimalFold: {
    threshold: 10000
  }
})
```

## 数据接入
旧的数据写入、加载更多和回调 API 已移除，请改用 `setDataLoader(loader)`。

```js
// 9.x
chart.applyNewData(dataList)
chart.updateData(data)
chart.loadMore(callback)

// 10.x
chart.setDataLoader({
  getBars: ({ callback }) => {
    fetch('/datas/kline.json')
      .then(res => res.json())
      .then(dataList => callback(dataList))
  },
  subscribeBar: ({ callback }) => {
    // 收到实时数据后调用 callback(data)
  },
  unsubscribeBar: () => {
    // 取消订阅实时数据
  }
})
```

如需清空并重新拉取数据，请使用 `resetData()`。

## 标的、周期和精度
`setPriceVolumePrecision(pricePrecision, volumePrecision)` 已移除，请通过 `setSymbol(symbol)` 管理标的和精度。

```js
chart.setSymbol({
  ticker: 'BTCUSDT',
  pricePrecision: 2,
  volumePrecision: 4
})

chart.setPeriod({
  span: 1,
  type: 'day'
})
```

## 坐标轴
### 默认 y 轴
旧样式配置中的 `yAxis.position`、`yAxis.type` 和 `yAxis.inside` 已移除。初始化默认值请放到 `layout.yAxis`。

```js
init('chart', {
  layout: {
    yAxis: {
      position: 'right',
      inside: false,
      reverse: false
    }
  }
})
```

运行时修改默认 y 轴：

```js
chart.overrideYAxis({
  paneId: 'candle_pane',
  position: 'left',
  inside: true
})
```

### 多 y 轴
10.x 支持在同一窗口创建多个 y 轴。

```js
const yAxisId = chart.createYAxis({
  paneId: 'candle_pane',
  id: 'left_axis',
  position: 'left'
})

chart.createIndicator({
  name: 'MA',
  paneId: 'candle_pane',
  yAxisId
})

const axes = chart.getYAxes({
  paneId: 'candle_pane'
})

chart.removeYAxis({
  id: 'left_axis'
})
```

`removeYAxis` 不会移除主图默认 y 轴，也不会移除仍被指标绑定的 y 轴。

## 指标
`createIndicator` 当前签名是 `createIndicator(indicator, isStack?)`。

```js
// 创建到指定窗口
chart.createIndicator({
  name: 'MACD',
  paneId: 'macd_pane'
})

// 叠加指标
chart.createIndicator('MA', true)

// 绑定指定 y 轴
chart.createIndicator({
  name: 'VOL',
  paneId: 'volume_pane',
  yAxisId: 'volume_axis'
})
```

查询指标请使用 `getIndicators(filter)`：

```js
const indicators = chart.getIndicators({
  paneId: 'candle_pane',
  name: 'MA'
})
```

自定义指标也需要调整：
+ `calc` 返回值由数组变更为以时间戳为 key 的对象。
+ `createTooltipDataSource` 返回值中 `values` 改为 `legends`，`icons` 改为 `features`。
+ 指标 `figures` 支持 `text` 类型，原 `rectText` 图形请替换为 `text`。

## 覆盖物
查询覆盖物请使用 `getOverlays(filter)`，不要再使用 `getOverlayById(id)`。

```js
const overlays = chart.getOverlays({
  id: 'overlay_id',
  groupId: 'group_1',
  paneId: 'candle_pane'
})
```

10.x 覆盖物新增连续绘制模式，内置覆盖物新增 `brush`。如果自定义覆盖物里使用了 `rectText`，请改成 `text`。

## 样式配置
以下配置需要替换：
+ `candle.tooltip.icons` → `candle.tooltip.features`
+ `indicator.tooltip.icons` → `indicator.tooltip.features`
+ `candle.tooltip.custom` → `candle.tooltip.legend`
+ `indicator.tooltip.defaultValue` → `indicator.tooltip.legend`
+ `indicator.tooltip.showName`、`indicator.tooltip.showParams` → `indicator.tooltip.title`

以下配置已移除：
+ `candle.tooltip.defaultValue`
+ `candle.tooltip.text`
+ `indicator.tooltip.text`
+ `overlay.rectText`
+ 样式中的 `yAxis.position`、`yAxis.type`、`yAxis.inside`

## 事件
`onTooltipIconClick` 已移除，请根据来源替换为：
+ `onCandleTooltipFeatureClick`
+ `onIndicatorTooltipFeatureClick`
+ `onCrosshairFeatureClick`

```js
chart.subscribeAction('onCandleTooltipFeatureClick', data => {
  // ...
})
```

## 图形工具
以下工具方法已移除：
+ `utils.drawArc`
+ `utils.drawCircle`
+ `utils.drawLine`
+ `utils.drawPolygon`
+ `utils.drawRect`
+ `utils.drawText`
+ `utils.drawRectText`

请使用 `getFigureClass(name)` 获取图形类后自行创建和绘制。

## 快速检查清单
+ 搜索并替换 `customApi`、`setCustomApi`、`getCustomApi`。
+ 搜索并替换 `applyNewData`、`applyMoreData`、`updateData`、`setLoadDataCallback`、`loadMore`。
+ 检查 `init` 中的 `layout` 是否已经改成 `barSpaceLimit`、`pane`、`yAxis`。
+ 检查样式里的 `yAxis.*`、tooltip `icons/text/defaultValue/custom` 和 `overlay.rectText`。
+ 检查自定义指标的 `calc` 和 `createTooltipDataSource` 返回值。
+ 检查 `getIndicatorByPaneId`、`getOverlayById`、`onTooltipIconClick`。
+ 如使用多 y 轴，确认指标已通过 `yAxisId` 绑定到目标 y 轴。
