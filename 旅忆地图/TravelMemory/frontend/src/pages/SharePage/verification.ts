/** 解包结果 → 三态验证指标（阶段 7 W7-3 的纯函数层）。
 *
 * ## 为什么单独抽一个纯函数
 *
 * `unpackForViewer()` 返回的是**协议视角**的报告（`signatureOk` / `commitmentOk` / `chainOk`，
 * 后两个是 `boolean | null`），直接塞进 JSX 就会出现散落在渲染代码里的三元表达式，
 * 而这三处判断恰恰是最容易写错语义的地方（见下）。抽成纯函数后：
 * 三态映射可以在 Node 环境下单测，UI 只负责把 `CheckItem[]` 画出来。
 *
 * ## 为什么 `null` 是 unknown 而不是 fail
 *
 * `commitmentOk === null` 表示"**缺字段，没法比对**"，`chainOk === null` 表示
 * "链关闭 / prevHash 没命中本地账本"。这两种情形的共同点是：
 * **本机没有拿到能证伪的证据**。
 *
 * 密码学 UI 的红线是"不能把'没验证'说成'验证失败'"——
 * 一个跨设备收到的信物（prevHash 指向对方机器上的链头）在收件人这边必然判不出来，
 * 这是协议的预期行为，不是文件被动过手脚。把它涂红会让用户以为信物不可信，
 * 进而怀疑整套端到端加密；把它涂成"未比对"的黄灰才是诚实的。
 * 反过来，`false` 是**明确比对过且不一致**，那才是必须报警的红色。
 *
 * 一句话：红 = 证伪，黄灰 = 未证实，绿 = 证实。三种颜色互不替代。
 *
 * ## 只在解包失败时三态全红
 *
 * `UnpackFailure` 意味着连"这包是谁发的"都没建立起来，此时把三条都涂成
 * "未比对"会让人误以为"部分可信"。既然整个包都不可用，三条一律 fail，
 * 但 `summary` 要用协议层给的 `reason`（那句话是统一口径，不泄露字段级细节）。
 */

import type {
  SafeReport,
  UnpackFailure,
  UnpackFailureKind,
  UnpackView,
  UnpackViewerResult,
} from '../../types/share';

export type CheckState = 'pass' | 'fail' | 'unknown';

export interface CheckItem {
  key: 'signature' | 'commitment' | 'chain';
  /** 「来源真实」/「内容未篡改」/「链锚定状态」。 */
  title: string;
  /** pass=绿 / fail=红 / unknown=黄灰。 */
  state: CheckState;
  /** 人话说明。 */
  detail: string;
  /** 技术细节（`ed25519 · 验签通过` 这种）。 */
  technical: string;
}

export interface VerificationView {
  /** 没有任何 fail 且确实有验证对象。unknown 不算失败。 */
  ok: boolean;
  /** 固定三条：signature / commitment / chain。 */
  items: CheckItem[];
  shareId: string | null;
  fromName: string | null;
  /** 一句话结论。 */
  summary: string;
}

/** `UnpackFailure.kind` → 人话。协议层的 `reason` 已经统一口径，这里只补"是哪一类问题"。 */
const KIND_LABELS: Record<UnpackFailureKind, string> = {
  'old-version': '信物版本过旧',
  'not-for-you': '这个信物不是发给你的',
  invalid: '信物已损坏或不是 TrailShare 信物',
};

const TITLES: Record<CheckItem['key'], string> = {
  signature: '来源真实',
  commitment: '内容未篡改',
  chain: '链锚定状态',
};

function stateLabel(state: CheckState): string {
  return state === 'pass' ? '通过' : state === 'fail' ? '失败' : '未比对';
}

/** 三条检查项的短标签（UI 的 Badge 文案）。导出给渲染层与测试共用，避免两处各写一份。 */
export const CHECK_STATE_LABELS: Record<CheckState, string> = {
  pass: stateLabel('pass'),
  fail: stateLabel('fail'),
  unknown: stateLabel('unknown'),
};

function buildSignature(report: SafeReport): CheckItem {
  const state: CheckState = report.signatureOk ? 'pass' : 'fail';
  return {
    key: 'signature',
    title: TITLES.signature,
    state,
    detail: state === 'pass'
      ? 'Ed25519 签名通过：确实是这位发送方发出的，中途没有被换人。'
      : '签名校验未通过：无法证明这包来自文件里声明的发送方。',
    technical: state === 'pass' ? 'ed25519 · 验签通过' : 'ed25519 · 验签未通过',
  };
}

function buildCommitment(report: SafeReport): CheckItem {
  const state: CheckState =
    report.commitmentOk === null ? 'unknown' : report.commitmentOk ? 'pass' : 'fail';
  return {
    key: 'commitment',
    title: TITLES.commitment,
    state,
    detail:
      state === 'pass'
        ? '承诺值与内容逐项吻合：地点分类与强度没有被改动过。'
        : state === 'fail'
          ? '承诺值与内容不一致：包里的分类或强度被动过，请不要信任。'
          : '包里没有可用于比对的承诺字段，本机无法判定内容是否被改动。',
    technical:
      state === 'pass'
        ? 'sha256 · 承诺值与内容一致'
        : state === 'fail'
          ? 'sha256 · 承诺值与内容不一致'
          : 'sha256 · 缺少承诺字段，无法比对',
  };
}

function buildChain(report: SafeReport): CheckItem {
  const state: CheckState = report.chainOk === null ? 'unknown' : report.chainOk ? 'pass' : 'fail';
  // 三种状态都带上 chainNote：链的判定理由全在那句话里（"跨设备接收属预期现象"等）。
  const note = report.chainNote;
  return {
    key: 'chain',
    title: TITLES.chain,
    state,
    detail:
      state === 'pass'
        ? `本地哈希链连续：${note}`
        : state === 'fail'
          ? `本地哈希链不连续：${note}`
          : `${note}。跨设备接收时这是预期现象，不代表信物有问题。`,
    technical:
      state === 'pass'
        ? `本地哈希链 · ${note}`
        : state === 'fail'
          ? `本地哈希链 · ${note}`
          : `本地哈希链 · ${note}`,
  };
}

function summarize(items: CheckItem[], failureReason: string | null): string {
  if (failureReason !== null) return failureReason;
  if (items.some((item) => item.state === 'fail')) {
    return '存在未通过的验证项：这个信物的内容不可信，请不要接受。';
  }
  if (items.some((item) => item.state === 'unknown')) {
    return '已通过的项全部成立，另有项目在本机无法判定（不影响已通过项的结论）。';
  }
  return '来源真实、内容未篡改、链路连续：这个信物可以放心接受。';
}

/** 解包结果 → 三态视图。`null`（还没选文件）→ 三条 unknown，`ok=false`。 */
export function buildVerificationView(
  result: UnpackView | UnpackFailure | null,
): VerificationView {
  if (result === null) {
    return {
      ok: false,
      items: [
        {
          key: 'signature',
          title: TITLES.signature,
          state: 'unknown',
          detail: '还没有可验证的信物。',
          technical: '等待输入',
        },
        {
          key: 'commitment',
          title: TITLES.commitment,
          state: 'unknown',
          detail: '还没有可验证的信物。',
          technical: '等待输入',
        },
        {
          key: 'chain',
          title: TITLES.chain,
          state: 'unknown',
          detail: '还没有可验证的信物。',
          technical: '等待输入',
        },
      ],
      shareId: null,
      fromName: null,
      summary: '选择一个 .trailshare 文件，验证会立刻在你本机完成。',
    };
  }

  if (!result.ok) {
    const reason = `${KIND_LABELS[result.kind]}：${result.reason}`;
    const fail = (key: CheckItem['key']): CheckItem => ({
      key,
      title: TITLES[key],
      state: 'fail',
      detail: reason,
      technical: `解包失败 · ${result.kind}`,
    });
    return {
      ok: false,
      items: [fail('signature'), fail('commitment'), fail('chain')],
      shareId: null,
      fromName: null,
      summary: reason,
    };
  }

  const items: CheckItem[] = [
    buildSignature(result.report),
    buildCommitment(result.report),
    buildChain(result.report),
  ];
  return {
    ok: !items.some((item) => item.state === 'fail'),
    items,
    shareId: result.shareId,
    fromName: result.from.displayName,
    summary: summarize(items, null),
  };
}

/** 结果类型收窄用：UI 拿到的常常是联合类型 `UnpackViewerResult | null`。 */
export type VerificationInput = UnpackViewerResult | null;
