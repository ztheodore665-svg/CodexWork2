/** 信物分享页：生成 → 下载 → 导入 → 验签 → 上地图。
 *
 * ## 这一页把整条链路接起来
 *
 * ```
 * [生成]   demoTrip() ──prepareShare(obfuscateLocation)──▶ pack ──▶ .trailshare 下载
 *              │                        ↑ 后端 POST /api/v1/privacy/obfuscate
 *              │                        └─ 失败 → 该地点不写坐标（绝不退化成原坐标）
 * [导入]   .trailshare ──unpackForViewer(我的种子)──▶ 验签/承诺报告 + 时间线
 *              └─ radius > 0 的地点 →「在地图上查看」→ MapPage 画半透明圈
 * ```
 *
 * ## 文件怎么分的
 *
 * 这一页的状态编排留在本文件（identity / friends / 收件人勾选 / 生成 / 导入），
 * 展示拆成四个子组件同目录下的兄弟文件：
 * `IdentityCard`（公钥与不可逆重置）、`FriendsCard`（通讯录）、
 * `RecipientCard`（收件人多选，W7-1 起顺带展示语义标签）、`ImportCard`（文件与解出的内容）、
 * `VerifyCard`（W7-3：只回答"能不能信"的三条可验证断言，与 ImportCard 共用同一份解包结论）。
 * 拆分的理由不是"文件太短"，而是它们各自有一份**独立的关注点与失效场景**，
 * 混在一个 400 行的渲染函数里改一处必看错一处。
 *
 * ## 刻意不做（留给后续阶段 / 人类）
 *
 * - 旅程编辑器、可见性勾选树（TrailShare 规范 §8 的完整 UI）；
 * - 接受/拒绝落本地记忆与溯源边（`store/` 层未移植）；
 * - 照片压缩与相册选择（W6-3 的桥已就位，UI 未接）。
 *
 * 这些都不影响密码学与协议链路的可验证性 —— 那部分在 `services/crypto|share/`
 * 有 100+ 条单测守着。
 */

import { useCallback, useMemo, useState } from 'react';
import { ShieldCheck } from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { toast } from '../../components/ui/use-toast';
import { obfuscateLocation } from '../../services/api/privacy';
import type { TripView } from '../../services/api/trips';
import { resolveNativeBridge } from '../../services/native';
import { emptyVisibility } from '../../services/share/filter';
import { prepareShare } from '../../services/share/prepare';
import { SHARE_FILE_EXTENSION } from '../../services/share/schema';
import { unpackForViewer } from '../../services/share/unpack';
import type { Tag, Trip, UnpackedPlace, UnpackViewerResult } from '../../types/share';
import { demoTrip, demoVisibility } from './demoTrip';
import {
  countMissingSemanticTags,
  describeMissingSemanticTags,
  hydrateTripPhotos,
  tripViewToTrip,
} from './tripSource';
import { FriendsCard } from './FriendsCard';
import { IdentityCard } from './IdentityCard';
import { ImportCard } from './ImportCard';
import { clearIdentity, loadOrCreateIdentity } from './identity';
import { listFriends, type Friend } from './friends';
import { RecipientCard } from './RecipientCard';
import {
  ALL_SEMANTIC_TAGS,
  FALLBACK_SEMANTIC_TAG,
  normalizeSemanticTag,
} from './semanticTags';
import { VerifyCard } from './VerifyCard';

/** 把示例旅程每个地点的 `tag` 过一遍语义标签校验（W7-1 前端部分）。
 *
 * **本次不改动分享协议**：`Place.tag` 本来就在协议里（`EncryptedPlace.tag` 是明文、
 * 且参与承诺），这里只是确保它一定是六类之一 —— 后端语义服务给出的标签有时会是
 * 新词或 `None`，直接打进包里会让收件人侧的分类渲染与承诺比对出现意外值。
 * 分类算法本身在后端 `semantic_service.py`，前端不复算，只做校验与保底。
 */
function withNormalizedTags(trip: Trip): Trip {
  return {
    ...trip,
    days: trip.days.map((day) => ({
      ...day,
      places: day.places.map((place) => ({
        ...place,
        tag: normalizeSemanticTag(place.tag) ?? FALLBACK_SEMANTIC_TAG,
      })),
    })),
  };
}

/** 行程来源选择器的"内置示例"这一项的值。 */
export const DEMO_TRIP_OPTION = '__demo__';

export interface SharePageProps {
  /** 「在地图上查看」：把解出的地点交给地图页渲染模糊圈。 */
  onViewOnMap?: (places: UnpackedPlace[]) => void;
  /** 后端行程列表（导入照片后由后端聚类出来）。不传则只有内置示例可选。 */
  trips?: readonly TripView[];
}

/** 行程来源选择器：默认优先用新建（导入出来的）行程，没有才回落到内置示例。 */
function TripSourceSelect({
  trips,
  value,
  onChange,
}: {
  trips: readonly TripView[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor="trip-source" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        旅程来源
      </Label>
      <select
        id="trip-source"
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value={DEMO_TRIP_OPTION}>内置示例旅程（洛阳两日）</option>
        {trips.map((item) => (
          <option key={item.id} value={item.id}>
            {item.title || '未命名旅程'}（{item.itemCount} 张 · {item.source === 'manual' ? '手工' : '自动聚类'}）
          </option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">
        {value === DEMO_TRIP_OPTION
          ? '示例数据是写死的，用来验证打包链路。'
          : '来自后端行程：照片字节取本机缩略图缓存，坐标仍会先做地理模糊。'}
      </p>
    </div>
  );
}

/** 把包落成文件下载。`Blob` + 临时 `<a>` 是最省事也最稳的一条路；
 *  注意 Firefox 要求 anchor 挂到文档上才会触发下载，`revokeObjectURL` 必须等 click 之后。 */
function downloadShareFile(pkg: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function SharePage({ onViewOnMap, trips = [] }: SharePageProps) {
  // 惰性初始化：localStorage 同步读，useState 初始函数只跑一次（StrictMode 双跑也安全）
  const [identity, setIdentity] = useState<ReturnType<typeof loadOrCreateIdentity>>(() => loadOrCreateIdentity());
  const [friends, setFriends] = useState<Friend[]>(() => listFriends());

  // null = 还没手动选过，跟着"有真实行程就用真实行程"的默认值走
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);
  const effectiveTripId = selectedTripId ?? (trips.length > 0 ? trips[0].id : DEMO_TRIP_OPTION);
  const selectedTrip = useMemo(
    () => trips.find((item) => item.id === effectiveTripId) ?? null,
    [trips, effectiveTripId],
  );
  const usingDemo = selectedTrip === null;

  // 自己默认在收件人里（不信 "写死"，用户可以取消）
  const [selfSelected, setSelfSelected] = useState(true);
  const [selectedFriendIds, setSelectedFriendIds] = useState<string[]>([]);

  const [generating, setGenerating] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [view, setView] = useState<UnpackViewerResult | null>(null);

  /** 勾选出来的收件人公钥（`bare hex` 或 `ed25519:` 前缀都行，`pack` 两种都认）。 */
  const recipientPubkeys = useMemo(() => {
    const friendKeys = selectedFriendIds
      .map((id) => friends.find((friend) => friend.id === id)?.pubkey)
      .filter((key): key is string => typeof key === 'string');
    return selfSelected ? [identity.pubkey, ...friendKeys] : friendKeys;
  }, [friends, identity.pubkey, selectedFriendIds, selfSelected]);

  /** 当前选中的旅程 + 语义标签校验（W7-1）。生成与展示共用同一份，避免"显示的标签"和
   *  "打包进去的标签"不一致。
   *
   *  真实行程由 `tripViewToTrip` 折出来：标签已经在里面退回过 `leisure`，
   *  这里再过一遍 `withNormalizedTags` 是为了让两个来源的保底规则完全一致。 */
  const normalizedTrip = useMemo(
    () => withNormalizedTags(selectedTrip ? tripViewToTrip(selectedTrip) : demoTrip()),
    [selectedTrip],
  );

  /** 可见性：示例用自己的那份（tripId 写死 `t_demo0001`），真实行程用空可见性
   *  （tripId 必须跟着行程 id 变，否则将来按 tripId 查记录会查到别人头上）。 */
  const visibility = useMemo(
    () => (usingDemo ? demoVisibility() : emptyVisibility(normalizedTrip.id, 'g1')),
    [usingDemo, normalizedTrip.id],
  );

  /** 这份旅程用到的语义标签，按 `ALL_SEMANTIC_TAGS` 顺序去重后展示。 */
  const semanticTags = useMemo<Tag[]>(() => {
    const seen = new Set<Tag>();
    for (const day of normalizedTrip.days) {
      for (const place of day.places) seen.add(place.tag);
    }
    return ALL_SEMANTIC_TAGS.filter((tag) => seen.has(tag));
  }, [normalizedTrip]);

  /** 语义标签**缺失**的地点数（后端 `semanticTag === null` 的那些）。
   *
   * 导入那一刻照片还没有描述文本，后端诚实返回 null，`itemToPlace` 会静默
   * 退回 `leisure` —— 打包照样成功，所以这件事必须在**打包前**说出来，
   * 否则收件人会把一个占位符当成发送方的分类结论。 */
  const missingTagNotice = useMemo(
    () => (selectedTrip ? describeMissingSemanticTags(countMissingSemanticTags(selectedTrip)) : null),
    [selectedTrip],
  );

  /** 通讯录变了就重读，并顺手把已经不存在的好友从勾选里摘掉
   *  （留着一个幽灵 id 会让"勾选人数"和实际收件人对不上）。 */
  const refreshFriends = useCallback(() => {
    const next = listFriends();
    setFriends(next);
    setSelectedFriendIds((prev) => prev.filter((id) => next.some((friend) => friend.id === id)));
  }, []);

  const toggleFriend = useCallback((id: string, next: boolean) => {
    setSelectedFriendIds((prev) =>
      next ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((item) => item !== id),
    );
  }, []);

  const generate = useCallback(async () => {
    setGenerating(true);
    setWarnings([]);
    try {
      // 真实行程的照片字节在 `tripViewToTrip` 里是空的占位，打包前用本机缩略图缓存填上。
      // 取不到字节的地点会被丢掉（pack 拒绝 0 字节照片），数量进 warnings 让用户看见。
      let trip = normalizedTrip;
      let droppedPlaces = 0;
      if (!usingDemo) {
        const hydrated = await hydrateTripPhotos(normalizedTrip, resolveNativeBridge().thumbs);
        trip = hydrated.trip;
        droppedPlaces = hydrated.droppedPlaces;
        if (trip.days.length === 0) {
          throw new Error('本机没有这些照片的缩略图，请在「地图相册」重新导入照片后再生成');
        }
      }

      const result = await prepareShare(
        {
          groups: [
            {
              // 语义标签已经过 normalizeSemanticTag 校验（W7-1 前端部分）
              trip,
              visibility,
              recipientPubkeys,
            },
          ],
          senderIdentity: identity,
          displayName: '我',
          chainEnabled: false,
        },
        { obfuscate: obfuscateLocation },
      );

      downloadShareFile(result.pkg, `${result.pkg.shareId}${SHARE_FILE_EXTENSION}`);
      // 标签缺回报也要进回执：包已经发出去了，用户需要一份"这包里有什么是占位的"记录
      const postWarnings = [...result.warnings];
      if (droppedPlaces > 0) {
        postWarnings.push(`有 ${droppedPlaces} 个地点缺少本机缩略图，未打包进去`);
      }
      const tagNotice = missingTagNotice;
      if (tagNotice) postWarnings.push(tagNotice);
      setWarnings(postWarnings);
      toast({
        variant: 'success',
        title: '信物已生成',
        description: `${result.pkg.shareId}，收件人 ${recipientPubkeys.length} 人`,
      });
    } catch (exc) {
      // 失败不静默：打包失败通常意味着"这个文件没发出去"，用户必须立刻知道
      toast({
        variant: 'destructive',
        title: '生成失败',
        description: (exc as Error).message,
      });
    } finally {
      setGenerating(false);
    }
  }, [identity, normalizedTrip, usingDemo, recipientPubkeys, visibility, missingTagNotice]);

  const importFile = useCallback(
    async (file: File) => {
      setImporting(true);
      setImportError(null);
      setView(null);
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as unknown;
        setView(await unpackForViewer(parsed, { privHex: identity.privHex }));
      } catch (exc) {
        setImportError((exc as Error).message);
      } finally {
        setImporting(false);
      }
    },
    [identity.privHex],
  );

  /** 重置身份：种子没了就再也回不去，所以确认由 IdentityCard 的 Dialog 拦一道。 */
  const resetIdentity = useCallback(() => {
    clearIdentity();
    setIdentity(loadOrCreateIdentity());
    // 旧身份解过的东西对新身份没有意义，连带着清掉，避免"看着还在但已经读不了"的假象
    setView(null);
    setImportError(null);
    setWarnings([]);
    toast({
      title: '身份已重置',
      description: '新的密钥对已在本机生成，记得把新公钥重新发给好友。',
    });
  }, []);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4">
      <header className="space-y-2">
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldCheck className="h-6 w-6 text-emerald-600" />
          信物分享
          <Badge variant="secure">端到端加密</Badge>
        </h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          定向加密信物（TrailShare 协议 ts-2026.4）：只有指定收件人能解开；私钥只在本机，绝不上传。
        </p>
      </header>

      <IdentityCard identity={identity} onReset={resetIdentity} />

      <FriendsCard
        friends={friends}
        ownPubHex={identity.pubHex}
        onChanged={refreshFriends}
      />

      {/* id 是给外层 ShareDialog 的锚点滚动用的（「生成信物」按钮直接跳到这）。
          scroll-mt-4：滚动容器有内边距，不补这一截标题会被切掉一截。 */}
      <div id="share-generate" className="scroll-mt-4">
        <RecipientCard
          tripLabel={usingDemo ? '内置示例旅程' : '我导入的照片'}
          sourceNote={
            usingDemo
              ? '用内置的洛阳两日示例打包：地点坐标会先做地理模糊，只有被勾选的收件人能解开。'
              : `打包后端行程「${normalizedTrip.title}」：照片字节取本机缩略图缓存，坐标先做地理模糊。`
          }
          tripSource={
            <TripSourceSelect
              trips={trips}
              value={effectiveTripId}
              onChange={setSelectedTripId}
            />
          }
          selfSelected={selfSelected}
          onToggleSelf={setSelfSelected}
          selfLabel={`指纹 ${identity.pubHex.slice(0, 8)}`}
          friends={friends}
          selectedFriendIds={selectedFriendIds}
          onToggleFriend={toggleFriend}
          recipientCount={recipientPubkeys.length}
          generating={generating}
          onGenerate={() => void generate()}
          warnings={warnings}
          semanticTags={semanticTags}
          missingTagNotice={missingTagNotice}
        />
      </div>

      <ImportCard
        importing={importing}
        error={importError}
        view={view}
        onFile={(file) => void importFile(file)}
        onViewOnMap={onViewOnMap}
      />

      {/* W7-3：可验证断言。与导入区共用同一份解包结论，所以两者不会给出矛盾的判断。 */}
      <div id="share-verify" className="scroll-mt-4">
        <VerifyCard
          cred={{ privHex: identity.privHex }}
          externalResult={view}
        />
      </div>
    </div>
  );
}
