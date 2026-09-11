---
outline: deep
---

# removeYAxis(filter)
`removeYAxis` remove y-axis.

## Reference {#reference}
<!-- @include: @/@views/api/references/instance/removeYAxis.md -->

### Parameters {#parameters}
- `filter` Filter conditions. At least one of `id` and `name` must be set.
  - `paneId` Pane id.
  - `id` Y-axis id.
  - `name` Axis name.

### Returns {#returns}
`removeYAxis` returns `boolean`.

## Usage {#usage}
<script setup>
import RemoveYAxis from '../../../@views/api/samples/removeYAxis/index.vue'
</script>

### Basic usage {#basic}
<RemoveYAxis/>
