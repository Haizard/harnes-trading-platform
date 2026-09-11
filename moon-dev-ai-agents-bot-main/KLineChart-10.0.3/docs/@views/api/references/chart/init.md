```typescript
(
  ds: string | HTMLElement,
  options?: {
    layout?: {
      barSpaceLimit?: {
        min?: number
        max?: number
      }
      pane?: {
        height?: number
        minHeight?: number
        dragEnabled?: boolean
        order?: number
        state?: 'normal' | 'maximize' | 'minimize'
      }
      yAxis?: {
        reverse?: boolean
        inside?: boolean
        needWidget?: boolean
        position?: 'left' | 'right'
        scrollZoomEnabled?: boolean
        gap?: {
          top?: number
          bottom?: number
        }
      }
    }
    locale?: string
    styles?: string | DeepPartial<Styles>
    timezone?: string
    formatter?: {
      formatDate?: (params: {
        dateTimeFormat: Intl.DateTimeFormat
        timestamp: number
        template: string
        type: 'tooltip' | 'crosshair' | 'xAxis'
      }) => string
      formatBigNumber?: (value: string | number) => string
      formatExtendText?: (params: {
        type: 'last_price'
        data: KLineData
        index: number
      }) => string
    }
    thousandsSeparator?: {
      sign?: string
      format: (value: number | string) => string
    }
    decimalFold?: {
      threshold?: number
      format?: (value: number | string) => string
    }
    zoomAnchor?: 'cursor' | 'last_bar' | { main?: 'cursor' | 'last_bar', xAxis?: 'cursor' | 'last_bar' }
    hotkey?: {
      enabled?: boolean
      exclude?: string[]
    }
  }
) => Chart
```
