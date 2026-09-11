---
outline: deep
---

# removeYAxis(filter)
`removeYAxis` 移除 y 轴。

## 参考 {#reference}
<!-- @include: @/@views/api/references/instance/removeYAxis.md -->

### 参数 {#parameters}
- `filter` 过滤参数，`id` 和 `name` 至少需要设置一个。
  - `paneId` 窗口 id。
  - `id` y 轴 id。
  - `name` 坐标轴名称。

### 返回值 {#returns}
`removeYAxis` 返回 `boolean`。

## 用法 {#usage}
<script setup>
import RemoveYAxis from '../../@views/api/samples/removeYAxis/index.vue'
</script>

### 基本使用 {#basic}
<RemoveYAxis/>
