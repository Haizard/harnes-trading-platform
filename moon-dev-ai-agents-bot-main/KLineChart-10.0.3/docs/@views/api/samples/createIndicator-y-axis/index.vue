<script setup>
import ChartPreview from '../../../../@components/ChartPreview.vue';
import { data } from './index.data'
</script>

<template>
  <ChartPreview
    title="createIndicator-y-axis"
    chartId="createIndicator-y-axis-chart"
    :code="data.js"/>
</template>