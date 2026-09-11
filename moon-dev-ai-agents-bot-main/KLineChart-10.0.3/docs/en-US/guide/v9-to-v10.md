# 🛠️ From 9.x to 10.x
This document helps you upgrade from klinecharts 9.x to the current 10.x version. If you are using 8.x or older, upgrade to 9.x first with [v8 to v9](https://v9.klinecharts.com/en-US/guide/v8-to-v9).

## Upgrade Highlights
+ 10.x reorganizes initialization options, data loading, axes, indicators, overlays, tooltips, and style configuration.
+ We recommend upgrading to the latest 9.x first, then handling the 10.x breaking changes in this document.
+ Axis configuration no longer lives in styles. Use `layout.yAxis` for defaults, `overrideYAxis` for runtime changes, and `createYAxis`, `removeYAxis`, and `getYAxes` for multiple y-axes.
+ Old data update APIs are replaced by `setDataLoader(loader)`.
+ Indicator placement and y-axis binding are configured with `indicator.paneId` and `indicator.yAxisId`.

## Initialization
### formatter
`customApi` is renamed to `formatter`, and `formatDate` now receives an object parameter.

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

Instance APIs should also be renamed:

```js
// 9.x
chart.setCustomApi(api)
chart.getCustomApi()

// 10.x
chart.setFormatter(formatter)
chart.getFormatter()
```

### layout
`options.layout` is now a default layout configuration object. It no longer uses the 9.x array-style pane layout, nor the beta-era `basicParams` / `panes` shape.

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

Use `setPaneOptions(options)` for runtime pane options:

```js
chart.setPaneOptions({
  id: 'pane_1',
  height: 160,
  dragEnabled: true,
  order: 20
})
```

Do not put axis options in `setPaneOptions`. Use `overrideYAxis(options)` or `overrideXAxis(options)` instead.

### Thousands Separator and Decimal Folding
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

## Data Loading
Old data writing and load-more APIs have been removed. Use `setDataLoader(loader)`.

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
    // call callback(data) when realtime data arrives
  },
  unsubscribeBar: () => {
    // unsubscribe realtime data
  }
})
```

Use `resetData()` when you need to clear and reload data.

## Symbol, Period, and Precision
`setPriceVolumePrecision(pricePrecision, volumePrecision)` has been removed. Use `setSymbol(symbol)`.

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

## Axes
### Default Y-axis
Style options such as `yAxis.position`, `yAxis.type`, and `yAxis.inside` have been removed. Put default values in `layout.yAxis`.

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

Update the default y-axis at runtime:

```js
chart.overrideYAxis({
  paneId: 'candle_pane',
  position: 'left',
  inside: true
})
```

### Multiple Y-axes
10.x supports multiple y-axes in one pane.

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

`removeYAxis` does not remove the main default y-axis, and it does not remove an axis that is still bound by indicators.

## Indicators
The current signature is `createIndicator(indicator, isStack?)`.

```js
// create in a specified pane
chart.createIndicator({
  name: 'MACD',
  paneId: 'macd_pane'
})

// stack indicator
chart.createIndicator('MA', true)

// bind to a specified y-axis
chart.createIndicator({
  name: 'VOL',
  paneId: 'volume_pane',
  yAxisId: 'volume_axis'
})
```

Use `getIndicators(filter)` for lookup:

```js
const indicators = chart.getIndicators({
  paneId: 'candle_pane',
  name: 'MA'
})
```

Custom indicators also need these updates:
+ `calc` returns an object keyed by timestamp instead of an array.
+ In `createTooltipDataSource`, `values` becomes `legends`, and `icons` becomes `features`.
+ Indicator `figures` support `text`; replace the old `rectText` figure with `text`.

## Overlays
Use `getOverlays(filter)` instead of `getOverlayById(id)`.

```js
const overlays = chart.getOverlays({
  id: 'overlay_id',
  groupId: 'group_1',
  paneId: 'candle_pane'
})
```

10.x adds continuous drawing mode for overlays and the built-in `brush` overlay. Replace `rectText` with `text` in custom overlays.

## Styles
Replace these options:
+ `candle.tooltip.icons` → `candle.tooltip.features`
+ `indicator.tooltip.icons` → `indicator.tooltip.features`
+ `candle.tooltip.custom` → `candle.tooltip.legend`
+ `indicator.tooltip.defaultValue` → `indicator.tooltip.legend`
+ `indicator.tooltip.showName`, `indicator.tooltip.showParams` → `indicator.tooltip.title`

These options have been removed:
+ `candle.tooltip.defaultValue`
+ `candle.tooltip.text`
+ `indicator.tooltip.text`
+ `overlay.rectText`
+ `yAxis.position`, `yAxis.type`, `yAxis.inside` in styles

## Events
`onTooltipIconClick` has been removed. Replace it based on the source:
+ `onCandleTooltipFeatureClick`
+ `onIndicatorTooltipFeatureClick`
+ `onCrosshairFeatureClick`

```js
chart.subscribeAction('onCandleTooltipFeatureClick', data => {
  // ...
})
```

## Figure Utilities
These utilities have been removed:
+ `utils.drawArc`
+ `utils.drawCircle`
+ `utils.drawLine`
+ `utils.drawPolygon`
+ `utils.drawRect`
+ `utils.drawText`
+ `utils.drawRectText`

Use `getFigureClass(name)` to obtain a figure class, then create and draw the figure yourself.

## Checklist
+ Search and replace `customApi`, `setCustomApi`, and `getCustomApi`.
+ Search and replace `applyNewData`, `applyMoreData`, `updateData`, `setLoadDataCallback`, and `loadMore`.
+ Check that `init` uses `layout.barSpaceLimit`, `layout.pane`, and `layout.yAxis`.
+ Check styles for `yAxis.*`, tooltip `icons/text/defaultValue/custom`, and `overlay.rectText`.
+ Check custom indicator `calc` and `createTooltipDataSource` return values.
+ Check `getIndicatorByPaneId`, `getOverlayById`, and `onTooltipIconClick`.
+ If you use multiple y-axes, make sure indicators bind to the target axis with `yAxisId`.
