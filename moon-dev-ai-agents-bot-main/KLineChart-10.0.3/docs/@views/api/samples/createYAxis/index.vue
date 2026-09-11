<script setup>
import ChartPreview from '../../../../@components/ChartPreview.vue';
import { data } from './index.data'
</script>

<template>
  <ChartPreview
    title="createYAxis"
    chartId="createYAxis-chart"
    :code="data.js"/>
</template>
