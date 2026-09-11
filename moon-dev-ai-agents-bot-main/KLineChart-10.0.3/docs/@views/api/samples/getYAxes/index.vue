<script setup>
import ChartPreview from '../../../../@components/ChartPreview.vue';
import { data } from './index.data'
</script>

<template>
  <ChartPreview
    title="getYAxes"
    chartId="getYAxes-chart"
    :code="data.js"/>
</template>
