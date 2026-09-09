<script setup>
defineProps({ title: { type: String, default: '' } })
</script>

<template>
  <section class="panel panel-card">
    <header v-if="title" class="panel-title">
      <span>{{ title }}</span>
      <span class="slot-right"><slot name="extra" /></span>
    </header>
    <div class="panel-body"><slot /></div>
  </section>
</template>

<style scoped>
.panel-card { display: flex; flex-direction: column; min-height: 0; }
.panel-body { flex: 1; min-height: 0; overflow: auto; padding: var(--space-3) var(--space-4); }
.slot-right { display: inline-flex; align-items: center; gap: var(--space-2); }
</style>
