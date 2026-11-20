/** 「给照片补标坐标」对话框：内嵌高德地图点选 + 手动输入兜底。
 *
 * ## 为什么两种输入方式都要
 *
 * 地图点选是主路径（直观、不会错），但有两种情况它不可用：
 *   1. 高德 key 没配或脚本被墙 —— 这时地图显示占位卡；
 *   2. 用户手里有一串精确坐标（从别的工具复制的），点地图点到像素级反而更慢。
 * 所以坐标输入框常驻，地图是加速器而不是唯一入口。
 *
 * ## 为什么坐标要用受控输入而不是初始值
 *
 * 用户可能想"在地图上点一下、再把纬度微调 0.001"。两个输入必须始终同步，
 * 所以坐标是 state 的唯一真相源，地图只是它的一个编辑器。
 */

import { useEffect, useState } from 'react';
import { Loader2, MapPin } from 'lucide-react';

import { patchPhotoLocation, type PhotoListItem } from '../../services/api/photos';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { toast } from '../../components/ui/use-toast';
import { MiniMap } from './MiniMap';

export interface PhotoLocationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  photo: PhotoListItem;
  /** 高德 JS API key（由上层从环境变量读入后传下）。 */
  jsKey: string;
  securityCode?: string;
  /** 补标成功后回调（上层据此刷新照片库与地图）。 */
  onSaved: () => void | Promise<void>;
}

/** 经纬度输入框的步长。0.0001 度约 11 米，够手工微调又不至于难敲。 */
const STEP = 0.0001;

export function PhotoLocationDialog({
  open,
  onOpenChange,
  photo,
  jsKey,
  securityCode = '',
  onSaved,
}: PhotoLocationDialogProps) {
  const [lat, setLat] = useState('');
  const [lng, setLng] = useState('');
  const [saving, setSaving] = useState(false);

  // 打开时用照片当前坐标预填（已定位的照片是"改坐标"，不是"从零填"）
  useEffect(() => {
    if (!open) return;
    setLat(photo.lat !== null ? String(photo.lat) : '');
    setLng(photo.lng !== null ? String(photo.lng) : '');
  }, [open, photo.lat, photo.lng]);

  const latValue = Number.parseFloat(lat);
  const lngValue = Number.parseFloat(lng);
  // 两端都验：后端是真相，这里是即时反馈。NaN 与越界都拦在按钮可用性上
  const valid =
    Number.isFinite(latValue) &&
    Number.isFinite(lngValue) &&
    latValue >= -90 &&
    latValue <= 90 &&
    lngValue >= -180 &&
    lngValue <= 180;

  async function save(): Promise<void> {
    if (!valid || saving) return;
    setSaving(true);
    try {
      const updated = await patchPhotoLocation(photo.id, latValue, lngValue);
      toast({
        variant: 'success',
        title: '已补标坐标',
        description: `${updated.lat?.toFixed(5)}, ${updated.lng?.toFixed(5)} —— 来源已标为「用户补标」`,
      });
      onOpenChange(false);
      await onSaved();
    } catch (err) {
      // 失败不关对话框：用户改一下就能重试
      toast({
        variant: 'destructive',
        title: '补标失败',
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>补标坐标</DialogTitle>
          <DialogDescription>
            在地图上点一下，或直接填经纬度。补标后来源会标为「用户补标」，置信度比 EXIF 低一档。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <MiniMap
            jsKey={jsKey}
            securityCode={securityCode}
            initial={photo.lat !== null && photo.lng !== null ? { lat: photo.lat, lng: photo.lng } : null}
            onPick={(nextLat, nextLng) => {
              // 只保留 5 位小数（约 1 米）：点一下给出 15 位小数是假精度
              setLat(nextLat.toFixed(5));
              setLng(nextLng.toFixed(5));
            }}
          />

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="photo-lat">纬度（WGS84）</Label>
              <Input
                id="photo-lat"
                value={lat}
                inputMode="decimal"
                step={STEP}
                placeholder="例如 30.2422"
                onChange={(event) => setLat(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void save();
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="photo-lng">经度（WGS84）</Label>
              <Input
                id="photo-lng"
                value={lng}
                inputMode="decimal"
                step={STEP}
                placeholder="例如 120.1494"
                onChange={(event) => setLng(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void save();
                }}
              />
            </div>
          </div>

          {!valid && (lat !== '' || lng !== '') && (
            <p className="text-xs text-destructive">
              纬度需在 -90 ~ 90，经度需在 -180 ~ 180
            </p>
          )}

          <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
            <MapPin className="h-3.5 w-3.5 shrink-0" />
            <p>
              当前来源：
              <Badge variant="muted" className="mx-1">
                {photo.locationSource ?? '未知'}
              </Badge>
              补标后会变成 user，置信度 0.8（EXIF 是 0.9）—— 这个值会参与地理模糊半径的计算。
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!valid || saving}
            onClick={() => void save()}
            title={valid ? '' : '请先填合法的经纬度'}
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {saving ? '保存中…' : '保存坐标'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
