/** 好友通讯录：把一串 Ed25519 公钥映射到一个人的名字。
 *
 * ## 隐私红线（PROJECT.md §二）
 *
 * 这份通讯录**只存在本机 `localStorage`，绝不上传服务端**，也不进任何信物包：
 * 好友进包时只以"收件人公钥"的形式出现（`ShareGroup.wrappedKeys[i].recipientPubkey`），
 * 备注名从头到尾不离开这台设备。
 *
 * ## 为什么持久化在本地而不是服务端
 *
 * 服务端一旦保存"A 和 B 是好友"，就等于保存了一份社交关系图 —— 那是本项目
 * 明确不做的事（连 GeoJSON 都不存秘密方向量，何况人际关系）。代价是换设备要重新加一遍好友，
 * 这与"种子不导出、换设备 = 换身份"的已知局限一致。
 *
 * ## 存储可为什么要健壮
 *
 * `localStorage` 是**用户可写的**：手改 devtools、旧版本格式、别的库往同一个 key 写脏东西，
 * 都可能让读回来的东西不是我们写的形状。所以这里的两条原则：
 *   1. 整体损坏（JSON 解析失败 / 不是数组）→ 当空表处理，**不抛**（通讯录坏了不该让"生成信物"用不了）；
 *   2. 局部损坏（数组里混着缺字段的元素）→ **只丢坏元素，保留好元素**。
 *      整表丢弃意味着用户手抄的十几个公钥因为一条脏数据全没了，而且他无法察觉 ——
 *      那比丢一条坏数据糟糕得多。
 */

import { KEY_HEX_LEN, PUBKEY_PREFIX, normalizePubkey } from '../../services/crypto/keys';

const STORAGE_KEY = 'travelmemory.friends.v1';

/** 64 个十六进制字符。大小写都收 —— 手工抄写时混用大小写太常见了，
 *  为了这个拒绝用户是在惩罚他认真抄了一遍。内部统一落成小写，比对就不会错。 */
const HEX_64 = /^[0-9a-fA-F]{64}$/;

export interface Friend {
  /** 稳定 ID：取 `pubHex` 前 12 位。同一个公钥永远算出同一个 ID，天然幂等。 */
  id: string;
  name: string;
  /** 对外交换形式：`ed25519:<64hex>`。 */
  pubkey: string;
  /** bare hex，比对用。 */
  pubHex: string;
  addedAt: string;
}

/** 最小存储契约，与 `identity.ts` 保持一致（便于单测注入内存实现）。 */
type FriendStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

let storage: FriendStorage | null = null;

function defaultStorage(): FriendStorage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    localStorage.getItem(STORAGE_KEY);
    return localStorage;
  } catch {
    // 隐私模式：所有读写都可能在真正调用时才抛，一次探测比到处 try 便宜
    return null;
  }
}

/** 注入自定义存储（单测用；传 `null` 回到默认 localStorage）。 */
export function setFriendsStorage(next: FriendStorage | null): void {
  storage = next;
}

function store(): FriendStorage | null {
  return storage ?? defaultStorage();
}

/** 把任意 JSON 值修成一个 Friend；**修不出来就返回 null**（调用方据此丢弃）。
 *
 * `pubHex` 是唯一不可妥协的字段：它决定了"这包发给谁"，缺了它这条记录毫无意义。
 * 其余字段（备注名、添加时间）丢了都补得回来 —— 名字可以重起，时间只影响排序。 */
function coerceFriend(raw: unknown): Friend | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const pubHex = typeof record.pubHex === 'string' ? record.pubHex.trim().toLowerCase() : '';
  if (!HEX_64.test(pubHex)) return null;

  const pubkey =
    typeof record.pubkey === 'string' && record.pubkey.trim() !== ''
      ? `${PUBKEY_PREFIX}${normalizePubkey(record.pubkey).toLowerCase()}`
      : `${PUBKEY_PREFIX}${pubHex}`;

  return {
    id: typeof record.id === 'string' && record.id !== '' ? record.id : pubHex.slice(0, 12),
    name: typeof record.name === 'string' && record.name.trim() !== '' ? record.name : defaultName(pubHex),
    pubkey,
    pubHex,
    addedAt: typeof record.addedAt === 'string' ? record.addedAt : '',
  };
}

/** 备注名缺失时的兜底：`未命名好友-<pubHex 后 4 位>`。
 *
 * **为什么宁可生成默认名也不拒绝添加**：公钥才是唯一稳定的标识，名字随时能改。
 * 因为"备注名还没想好"就阻断用户把人加进来，是典型的劣质体验 ——
 * 他会选择用便签记着公钥，那份便签比 localStorage 更容易丢。 */
function defaultName(pubHex: string): string {
  return `未命名好友-${pubHex.slice(-4)}`;
}

function readRaw(): Friend[] {
  const target = store();
  if (!target) return [];
  let parsed: unknown;
  try {
    const raw = target.getItem(STORAGE_KEY);
    if (!raw) return [];
    parsed = JSON.parse(raw) as unknown;
  } catch {
    // 整体损坏 → 空表。这里刻意不 console.error：通讯录损坏对用户无 actionable，只会制造恐慌
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return sortByAddedAt(
    parsed.map(coerceFriend).filter((item): item is Friend => item !== null),
  );
}

function write(list: Friend[]): void {
  const target = store();
  if (!target) return;
  try {
    target.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // 配额满 / 隐私模式：静默降级成内存通讯录，功能不中断
  }
}

/** `addedAt` 升序。`localeCompare` 对 ISO 字符串等价于字典序，
 *  而 ISO 时间字符串的字典序 == 时间序（这也是它优于 Date.toLocaleString 的原因）。 */
function sortByAddedAt(list: Friend[]): Friend[] {
  return [...list].sort((a, b) => a.addedAt.localeCompare(b.addedAt));
}

/** 通讯录全表（按添加时间升序）。 */
export function listFriends(): Friend[] {
  return readRaw();
}

/**
 * 添加好友。**所有失败都抛人能看懂的 `Error`** —— UI 直接把文案喂给 toast。
 *
 * @param ownPubHex 本机公钥 bare hex，用来拦住"把自己加成好友"
 *   （自己默认就在收件人列表里，加进通讯录只会让两个列表打架）
 */
export function addFriend(input: { pubkey: string; name: string }, ownPubHex: string): Friend {
  const raw = typeof input?.pubkey === 'string' ? input.pubkey.trim() : '';
  if (raw === '') throw new Error('请输入对方的公钥');

  const bare = normalizePubkey(raw);
  if (bare.length !== KEY_HEX_LEN || !HEX_64.test(bare)) {
    throw new Error('公钥格式不正确：应为 64 位十六进制，可带 ed25519: 前缀');
  }
  const pubHex = bare.toLowerCase();

  const own = typeof ownPubHex === 'string' ? ownPubHex.trim().toLowerCase() : '';
  if (pubHex === own) throw new Error('不能添加自己：自己默认就在收件人列表里');

  const current = readRaw();
  if (current.some((friend) => friend.pubHex === pubHex)) {
    throw new Error('该好友已在列表中');
  }

  const name = typeof input?.name === 'string' ? input.name.trim() : '';
  const friend: Friend = {
    id: pubHex.slice(0, 12),
    name: name === '' ? defaultName(pubHex) : name,
    pubkey: `${PUBKEY_PREFIX}${pubHex}`,
    pubHex,
    addedAt: new Date().toISOString(),
  };
  write(sortByAddedAt([...current, friend]));
  return friend;
}

/** 删除好友。删掉返回 `true`，本来就没有返回 `false`。 */
export function removeFriend(id: string): boolean {
  const current = readRaw();
  const next = current.filter((friend) => friend.id !== id);
  if (next.length === current.length) return false;
  write(next);
  return true;
}

/** 清空通讯录。重置身份时可调用；存储不可用时静默无副作用。 */
export function clearFriends(): void {
  const target = store();
  if (!target) return;
  try {
    target.removeItem(STORAGE_KEY);
  } catch {
    // 同上
  }
}
