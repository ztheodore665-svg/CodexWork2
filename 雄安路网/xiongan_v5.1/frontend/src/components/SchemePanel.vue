<script setup>
import { computed, ref } from 'vue'
import PanelCard from './ui/PanelCard.vue'
import AppButton from './ui/AppButton.vue'
import { useSimStore } from '../stores/sim'

const sim = useSimStore()

const tab = ref('scheme_2')
const mode = ref('mappo')
const params = ref({ min_green: 5, max_green: 30, switch_clearance: 5 })
const todPlan = ref('')
const greenWave = ref(false)
const corridorDir = ref('EW')
const vehIds = ref('')
const restrictedEdge = ref('')
const lastMsg = ref('')

const PARAM_DEFS = [
  { key: 'min_green', label: '最短绿灯', min: 0, max: 20, unit: 's' },
  { key: 'max_green', label: '最长绿灯', min: 15, max: 90, unit: 's' },
  { key: 'switch_clearance', label: '变灯倒计时', min: 5, max: 20, unit: 's' },
]

const active = computed(() => sim.scheme)

async function act(schemeId, action, paramsObj = {}) {
  try {
    const r = await sim.schemeAction(schemeId, action, paramsObj)
    lastMsg.value = r?.message || `${action} 已执行`
    if (action === 'get_status') lastMsg.value = JSON.stringify(r, null, 0).slice(0, 200)
    // 动作可能切换了模式/参数：同步顶栏状态（scheme/scheme_mode）
    sim.refreshStatus().catch(() => {})
  } catch (e) {
    lastMsg.value = `执行失败: ${e.message}`
  }
}
</script>

<template>
  <PanelCard title="控制方案">
    <div class="tabs">
      <button v-for="t in [['scheme_1', '方案一'], ['scheme_2', '方案二'], ['scheme_3', '方案三']]"
        :key="t[0]" class="tab" :class="{ active: tab === t[0], running: sim.scheme === t[0] }"
        @click="tab = t[0]">{{ t[1] }}<i v-if="sim.scheme === t[0]" class="run-dot" /></button>
    </div>

    <!-- 方案一 · 固定配时 + 绿波 -->
    <div v-if="tab === 'scheme_1'" class="body">
      <p v-if="active !== 'scheme_1'" class="warn">当前未激活方案一（用顶栏方案选择启动）</p>
      <div class="row">
        <input v-model="todPlan" class="inp mono" placeholder="时段方案名（如 morning）" />
        <AppButton :disabled="active !== 'scheme_1'" @click="act('scheme_1', 'switch_tod_plan', { plan_name: todPlan })">切换时段</AppButton>
      </div>
      <div class="row">
        <AppButton :disabled="active !== 'scheme_1'" @click="act('scheme_1', 'recalculate')">重算配时</AppButton>
        <AppButton :disabled="active !== 'scheme_1'"
          @click="greenWave = !greenWave; act('scheme_1', 'enable_green_wave', { enabled: !greenWave })">
          {{ greenWave ? '关闭绿波' : '启用绿波' }}
        </AppButton>
      </div>
      <div class="row">
        <select v-model="corridorDir" class="inp">
          <option value="EW">东西走廊</option><option value="NS">南北走廊</option>
        </select>
        <AppButton :disabled="active !== 'scheme_1'" @click="act('scheme_1', 'add_corridor', { corridor_id: 'manual', direction: corridorDir })">加绿波走廊</AppButton>
      </div>
      <AppButton :disabled="active !== 'scheme_1'" @click="act('scheme_1', 'get_status')">查看状态</AppButton>
    </div>

    <!-- 方案二 · MAPPO/SCOOT -->
    <div v-else-if="tab === 'scheme_2'" class="body">
      <p v-if="active !== 'scheme_2'" class="warn">当前未激活方案二（用顶栏方案选择启动）</p>
      <div class="modes">
        <button v-for="m in [['mappo', 'MAPPO'], ['scoot', 'SCOOT'], ['auto', 'AUTO']]"
          :key="m[0]" class="mode" :class="{ active: mode === m[0] }" @click="mode = m[0]">{{ m[1] }}</button>
      </div>
      <div class="params">
        <div v-for="p in PARAM_DEFS" :key="p.key" class="param">
          <div class="param-head">
            <span class="mono">{{ p.key }}</span>
            <span class="mono value">{{ params[p.key] }}<span class="unit">{{ p.unit }}</span></span>
          </div>
          <input v-model.number="params[p.key]" type="range" :min="p.min" :max="p.max" step="1" class="slider" />
          <div class="param-range"><span>{{ p.min }}</span><span>{{ p.max }}</span></div>
        </div>
      </div>
      <div class="row">
        <AppButton :disabled="active !== 'scheme_2'" variant="primary"
          @click="act('scheme_2', mode === 'auto' ? 'switch_to_auto' : (mode === 'mappo' ? 'switch_to_mappo' : 'switch_to_scoot'))">
          切换{{ mode === 'auto' ? '自动' : mode.toUpperCase() }}
        </AppButton>
        <AppButton :disabled="active !== 'scheme_2'" @click="act('scheme_2', 'set_params', params)">应用参数</AppButton>
      </div>
      <p class="hint">参数范围与后端校验一致（越界会被拒绝）</p>
    </div>

    <!-- 方案三 · 车端引导 -->
    <div v-else class="body">
      <p v-if="active !== 'scheme_3'" class="warn">当前未激活方案三（用顶栏方案选择启动）</p>
      <div class="row">
        <input v-model="vehIds" class="inp mono" placeholder="车队车辆 id，逗号分隔" />
        <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'register_fleet', { vehicle_ids: vehIds.split(',').map(s => s.trim()).filter(Boolean) })">注册车队</AppButton>
      </div>
      <div class="row">
        <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'reroute_fleet', { vehicle_ids: vehIds.split(',').map(s => s.trim()).filter(Boolean) })">车队重路由</AppButton>
        <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'enable_auto_reroute', { enabled: true })">开启自动绕行</AppButton>
      </div>
      <div class="row">
        <input v-model="restrictedEdge" class="inp mono" placeholder="禁行区边 id" />
        <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'add_restricted_zone', { edge_ids: [restrictedEdge] })">设禁行</AppButton>
        <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'clear_restricted_zones')">清禁行</AppButton>
      </div>
      <AppButton :disabled="active !== 'scheme_3'" @click="act('scheme_3', 'get_status')">查看状态</AppButton>
    </div>

    <p v-if="lastMsg" class="last mono">{{ lastMsg }}</p>
  </PanelCard>
</template>

<style scoped>
.tabs { display: flex; gap: 6px; margin-bottom: var(--space-3); }
.tab {
  flex: 1; height: 26px; position: relative;
  border: 1px solid var(--border); border-radius: var(--radius-ctrl);
  background: var(--bg-elev); color: var(--text-2);
  font-size: 12px; cursor: pointer;
}
.tab.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.run-dot { position: absolute; top: 3px; right: 4px; width: 6px; height: 6px; border-radius: 50%; background: var(--signal-green); }
.body { display: flex; flex-direction: column; gap: var(--space-3); }
.warn { font-size: 11px; color: var(--accent); margin: 0; }
.row { display: flex; gap: 6px; }
.inp {
  flex: 1; height: 26px; padding: 0 8px; min-width: 0;
  background: var(--bg-elev); color: var(--text-1);
  border: 1px solid var(--border); border-radius: var(--radius-ctrl); font-size: 11px;
}
.modes { display: flex; gap: 6px; }
.mode {
  flex: 1; height: 26px;
  border: 1px solid var(--border); border-radius: var(--radius-ctrl);
  background: var(--bg-elev); color: var(--text-2);
  font: 600 11px/1 var(--font-mono); cursor: pointer;
}
.mode.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.params { display: flex; flex-direction: column; gap: var(--space-3); }
.param-head { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-2); }
.param-head .value { color: var(--text-1); font-size: 13px; }
.unit { color: var(--text-3); margin-left: 2px; font-size: 10px; }
.slider { width: 100%; accent-color: var(--accent); }
.param-range { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-3); }
.hint { font-size: 11px; color: var(--text-3); margin: 0; }
.last { font-size: 11px; color: var(--text-2); margin: var(--space-2) 0 0; word-break: break-all; }
</style>
