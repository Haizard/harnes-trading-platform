---
outline: deep
---

# getYAxes(filter)
`getYAxes` 获取 y 轴信息。

## 参考 {#reference}
<!-- @include: @/@views/api/references/instance/getYAxes.md -->

### 参数 {#parameters}
- `filter` 过滤参数。
  - `paneId` 窗口 id。
  - `id` y 轴 id。
  - `name` 坐标轴名称。

### 返回值 {#returns}
`getYAxes` 返回 `YAxis[]`。

## 用法 {#usage}
<script setup>
import GetYAxes from '../../@views/api/samples/getYAxes/index.vue'
</script>

### 基本使用 {#basic}
<GetYAxes/>
