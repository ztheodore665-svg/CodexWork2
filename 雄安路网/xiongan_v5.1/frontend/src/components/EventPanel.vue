<script setup>
import { computed, ref, watch } from 'vue'
import PanelCard from './ui/PanelCard.vue'
import AppButton from './ui/AppButton.vue'
import { useEventStore } from '../stores/events'
import { useSimStore } from '../stores/sim'

const events = useEventStore()
const sim = useSimStore()

const edge = ref('')
const toEdge = ref('')
const addCount = ref(20)
const vehType = ref('DEFAULT_VEHTYPE')
const addMsg = ref('')
const flowMode = ref('fixed')   // fixed | all
const flowCount = ref(30)
const flowMsg = ref('')

const VEH_TYPES = [
  { value: 'DEFAULT_VEHTYPE', label: '小汽车' },
  { value: 'bus', label: '公交车' },
  { value: 'truck', label: '货车' },
  { value: 'bicycle', label: '自行车' },
  { value: 'fleet', label: '车队车辆' },
]

// 默认边取当前路网第一条真实边；路网加载后自动选中
const defaultEdge = computed(() => sim.currentEdges[0] || 'E21_1')
watch(() => sim.currentEdges, (es) => {
  if (es.length && !edge.value) edge.value = es[0]
}, { immediate: true })

const TYPES = [
  { key: 'accident', label: '事故限速' },
  { key: 'construction', label: '施工占道' },
  { key: 'large_event', label: '突发车流' },
]

async function addVehicles() {
  const eid = edge.value || defaultEdge.value
  try {
    const r = await events.inject('large_event', {
      edge_ids: [eid], vehicles: addCount.value, veh_type: vehType.value,
      to_edge: toEdge.value || undefined })
    addMsg.value = `已加入 ${r?.added ?? addCount.value} 辆车到 ${eid}`
  } catch (e) {
    addMsg.value = `失败: ${e.message}`
  }
}

async function genFlow() {
  const params = flowMode.value === 'all'
    ? { mode: 'all_entries', vehicles: flowCount.value }
    : { edge_ids: [edge.value || defaultEdge.value], vehicles: flowCount.value,
        to_edge: toEdge.value || undefined }
  try {
    const r = await events.inject('large_event', params)
    flowMsg.value = flowMode.value === 'all'
      ? `全入口生成 ${r?.added ?? flowCount.value} 辆（随机路线）`
      : `定点生成 ${r?.added ?? flowCount.value} 辆（随机路线${toEdge.value ? '，终点 ' + toEdge.value : ''}）`
  } catch (e) {
    flowMsg.value = `失败: ${e.message}`
  }
}
</script>

<template>
  <PanelCard title="扰动与车辆">
    <div class="block">
      <div class="lbl">扰动注入</div>
      <div class="row">
        <select v-model="edge" class="inp mono" title="目标道路">
          <option v-for="eid in sim.currentEdges" :key="eid" :value="eid">{{ eid }}</option>
        </select>
      </div>
      <div class="types">
        <AppButton v-for="t in TYPES" :key="t.key"
          @click="events.inject(t.key, t.key === 'large_event' ? { edge_ids: [(edge || defaultEdge)], vehicles: addCount } : { edge_ids: [(edge || defaultEdge)] }).catch(() => {})"
        >{{ t.label }}</AppButton>
      </div>
    </div>

    <div class="block">
      <div class="lbl">生成车流（随机路线、正常速度）</div>
      <div class="row">
        <select v-model="flowMode" class="inp" style="flex:0 0 74px">
          <option value="fixed">定点</option><option value="all">全入口</option>
        </select>
        <input v-model.number="flowCount" type="number" min="1" max="300" class="inp num" style="width:60px" />
        <AppButton variant="primary" @click="genFlow">生成</AppButton>
      </div>
      <div class="row">
        <select v-model="toEdge" class="inp mono" title="终点（可选）">
          <option value="">终点（随机）</option>
          <option v-for="eid in sim.currentEdges" :key="'t' + eid" :value="eid">{{ eid }}</option>
        </select>
      </div>
      <p v-if="flowMsg" class="msg mono">{{ flowMsg }}</p>
    </div>

    <div class="block">
      <div class="lbl">人工加入车辆（可指定终点）</div>
      <div class="row">
        <input v-model.number="addCount" type="number" min="1" max="200" class="inp num" style="width:56px" />
        <select v-model="vehType" class="inp" title="车辆类型">
          <option v-for="t in VEH_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
        </select>
        <AppButton @click="addVehicles">加入车辆</AppButton>
      </div>
      <p v-if="addMsg" class="msg mono">{{ addMsg }}</p>
    </div>

    <div class="log">
      <div v-if="!events.list.length" class="empty mono">暂无事件</div>
      <div v-for="(e, i) in events.list.slice(-5).reverse()" :key="i" class="item">
        <span class="dot" />
        <span class="mono">{{ e.event_type }}</span>
        <span class="step mono">步 {{ e.step ?? '—' }}</span>
      </div>
    </div>
  </PanelCard>
</template>

<style scoped>
.block { margin-bottom: var(--space-3); }
.lbl { font-size: 11px; color: var(--text-2); margin-bottom: 6px; }
.row { display: flex; gap: 6px; margin-bottom: 6px; }
.inp {
  flex: 1; height: 26px; padding: 0 8px; min-width: 0;
  background: var(--bg-elev); color: var(--text-1);
  border: 1px solid var(--border); border-radius: var(--radius-ctrl); font-size: 11px;
}
.types { display: flex; gap: 6px; }
.types .app-btn { flex: 1; font-size: 11px; padding: 0 6px; }
.msg { font-size: 11px; color: var(--signal-green); margin: 4px 0 0; }
.log { display: flex; flex-direction: column; gap: 4px; border-top: 1px solid var(--border); padding-top: var(--space-2); }
.item { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-2); }
.item .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--signal-red); }
.item .step { margin-left: auto; color: var(--text-3); }
.empty { color: var(--text-3); font-size: 11px; text-align: center; padding: 8px 0; }
</style>
