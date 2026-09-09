<script setup>
defineProps({
  variant: { type: String, default: 'ghost' }, // ghost | primary | danger
  disabled: { type: Boolean, default: false },
  title: { type: String, default: '' },
})
</script>

<template>
  <button class="app-btn" :class="`app-btn--${variant}`" :disabled="disabled" :title="title">
    <slot />
  </button>
</template>

<style scoped>
.app-btn {
  height: 28px; padding: 0 12px;
  border: 1px solid var(--border); border-radius: var(--radius-ctrl);
  background: var(--bg-elev); color: var(--text-1);
  font: 500 12px/1 var(--font-sans); letter-spacing: 0.02em;
  cursor: pointer; user-select: none; white-space: nowrap;
  transition: background var(--dur-fast) var(--ease-out),
              border-color var(--dur-fast) var(--ease-out),
              color var(--dur-fast) var(--ease-out);
}
.app-btn:hover:not(:disabled) { background: var(--bg-hover); border-color: var(--border-strong); }
.app-btn:active:not(:disabled) { background: var(--bg-active); }
.app-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.app-btn--primary {
  background: var(--accent-soft); border-color: var(--accent);
  color: var(--accent); font-weight: 600;
}
.app-btn--primary:hover:not(:disabled) { background: var(--accent); color: oklch(0.16 0.01 80); }
.app-btn--danger { color: var(--signal-red); border-color: var(--signal-red); }
.app-btn--danger:hover:not(:disabled) { background: var(--signal-red-soft); }
.app-btn--success {
  background: var(--signal-green-soft); border-color: var(--signal-green);
  color: var(--signal-green); font-weight: 600;
}
.app-btn--success:hover:not(:disabled) { background: var(--signal-green); color: oklch(0.16 0.01 80); }
</style>
