---
outline: deep
---

# createYAxis(yAxis)
`createYAxis` create y-axis.

## Reference {#reference}
<!-- @include: @/@views/api/references/instance/createYAxis.md -->

### Parameters {#parameters}
- `yAxis` Y-axis configuration.
  - `paneId` Pane id. Defaults to the candle pane.
  - `id` Y-axis id.
  - `name` Axis name.
  - `reverse` Whether to reverse.
  - `inside` Whether to place inside.
  - `needWidget` Whether to create an axis widget.
  - `position` Position, supports `left` and `right`.
  - `scrollZoomEnabled` Whether scrolling and zooming are enabled on the Y-axis. When enabled, users can drag to scroll the axis or use the mouse wheel to zoom.
  - `gap` Top and bottom margin configuration.
    - `top` Top margin.
    - `bottom` Bottom margin.
  - `createRange` Callback for creating the value range on the axis.
  - `createTicks` Callback for creating tick information.

### Returns {#returns}
`createYAxis` returns the created y-axis id, or `null` if creation fails.

## Usage {#usage}
<script setup>
import CreateYAxis from '../../../@views/api/samples/createYAxis/index.vue'
</script>

### Basic usage {#basic}
<CreateYAxis/>
