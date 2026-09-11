<script setup>
import ChartPreview from '../../../../@components/ChartPreview.vue';
import { data } from './index.data'
</script>

<template>
  <ChartPreview
    title="removeYAxis"
    chartId="removeYAxis-chart"
    :code="data.js"/>
</template>
