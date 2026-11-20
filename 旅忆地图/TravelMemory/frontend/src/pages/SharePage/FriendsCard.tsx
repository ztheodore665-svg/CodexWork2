/** 好友卡片：录入（备注名 ↔ 公钥）与列表管理。
 *
 * ## 为什么错误要走 toast 而不是表单下方的红字
 *
 * 这类错误是**瞬时操作反馈**（点一下"添加"，立刻知道成不成），不是"看清再点"的常驻说明；
 * 而表单下方红字会在用户下次输入时还杵在那儿，变成过期信息。
 * 但无论如何都不能静默 —— `addFriend` 抛的每一句文案都是给人看的。
 *
 * ## 为什么列表不显示"共 N 个好友"
 *
 * TrailShare 的文案硬规则：界面不得出现总数暗示（数量是最容易泄漏的一类元信息）。
 * 本项目沿用这条 —— 收件人选择区同理，只给"已勾选 N 人"这种**用户自己勾出来的**数，
 * 而不给通讯录的全局规模。
 */

import { useState } from 'react';
import { Trash2, UserPlus } from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { toast } from '../../components/ui/use-toast';
import { addFriend, removeFriend, type Friend } from './friends';

/** 列表里展示的公钥长度。这里截断是**展示层**的事（整串另有复制按钮），
 *  不像身份卡片那样必须完整 —— 列表要在一屏里扫出"谁是谁"。 */
const PUBKEY_PREVIEW_LEN = 24;

function formatAddedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '未知时间';
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`;
}

export interface FriendsCardProps {
  friends: Friend[];
  /** 本机公钥 bare hex：拦住"把自己加成好友"。 */
  ownPubHex: string;
  /** 通讯录变更后通知父组件重新读表（单一数据源仍在存储空间层）。 */
  onChanged: () => void;
}

export function FriendsCard({ friends, ownPubHex, onChanged }: FriendsCardProps) {
  const [pubkeyInput, setPubkeyInput] = useState('');
  const [nameInput, setNameInput] = useState('');

  const handleAdd = () => {
    try {
      const friend = addFriend({ pubkey: pubkeyInput, name: nameInput }, ownPubHex);
      // 只在成功时清空 —— 失败多半是公钥抄错一两位，清掉等于让用户重抄一整串
      setPubkeyInput('');
      setNameInput('');
      onChanged();
      toast({
        variant: 'success',
        title: '好友已添加',
        description: `${friend.name} · 现在可以在「生成信物」里把他勾成收件人。`,
      });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: '没能添加好友',
        description: (error as Error).message,
      });
    }
  };

  const handleRemove = (friend: Friend) => {
    removeFriend(friend.id);
    onChanged();
    toast({
      title: '已移除好友',
      description: `${friend.name} 已不在收件人候选里。`,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <UserPlus className="h-5 w-5 text-primary" />
          好友
          <Badge variant="muted">仅存本机</Badge>
        </CardTitle>
        <CardDescription>
          备注名只写在这台设备上，永远不会进信物包、也不会上传服务端。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="friend-pubkey">对方公钥</Label>
            <Input
              id="friend-pubkey"
              value={pubkeyInput}
              onChange={(event) => setPubkeyInput(event.target.value)}
              placeholder="ed25519:..."
              spellCheck={false}
              autoComplete="off"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="friend-name">备注名</Label>
            <Input
              id="friend-name"
              value={nameInput}
              onChange={(event) => setNameInput(event.target.value)}
              placeholder="留空则用默认名"
              autoComplete="off"
            />
          </div>
          <Button onClick={handleAdd}>
            <UserPlus className="h-4 w-4" />
            添加好友
          </Button>
        </div>

        {friends.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-6 text-center text-sm text-muted-foreground">
            还没有好友。和对方互相抄一遍公钥粘贴到这里，就能把信物定向发给他。
          </p>
        ) : (
          <ul className="space-y-1">
            {friends.map((friend) => (
              <li
                key={friend.id}
                className="flex flex-wrap items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/60"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium leading-snug">{friend.name}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">
                    {friend.pubkey.slice(0, PUBKEY_PREVIEW_LEN)}…
                  </div>
                </div>
                <span className="text-xs text-muted-foreground">{formatAddedAt(friend.addedAt)}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`移除好友 ${friend.name}`}
                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => handleRemove(friend)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
