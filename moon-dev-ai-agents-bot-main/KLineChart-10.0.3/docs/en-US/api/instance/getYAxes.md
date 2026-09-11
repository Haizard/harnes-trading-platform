---
outline: deep
---

# getYAxes(filter)
`getYAxes` get y-axis information.

## Reference {#reference}
<!-- @include: @/@views/api/references/instance/getYAxes.md -->

### Parameters {#parameters}
- `filter` Filter conditions.
  - `paneId` Pane id.
  - `id` Y-axis id.
  - `name` Axis name.

### Returns {#returns}
`getYAxes` returns `YAxis[]`.

## Usage {#usage}
<script setup>
import GetYAxes from '../../../@views/api/samples/getYAxes/index.vue'
</script>

### Basic usage {#basic}
<GetYAxes/>
