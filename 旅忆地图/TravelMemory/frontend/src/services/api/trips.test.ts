/** 行程客户端单测：`listTrips` 与 `createTrip`（阶段 6 收尾）。
 *
 * ## 这里测什么
 *
 * 客户端层只有三件容易出错、且错了不报错的事：
 *   1. **snake_case → camelCase 的归一化**（漏一个字段就是"页面上这一栏永远空白"）；
 *   2. **404 的语义翻译**（能力未启用 ≠ 服务挂了，错误信息必须能指导行动）；
 *   3. **注入点优先级**（`setTripsApi` 能被参数覆盖，也能被 `null` 复位）。
 *
 * 不测真实网络：与 `photos.test.ts` 同一套做法，`fetchImpl` 注入假实现。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { createTrip, listTrips, setTripsApi } from './trips';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

afterEach(() => {
  setTripsApi(null);
  vi.unstubAllGlobals();
});

describe('listTrips', () => {
  it('把后端的 snake_case 归一化成 camelCase', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({
        trips: [
          {
            id: 't1',
            title: '洛阳两日',
            started_at: '2026-04-12T01:00:00+00:00',
            ended_at: null,
            source: 'manual',
            item_count: 0,
            items: [],
          },
        ],
      }),
    );

    const trips = await listTrips({ baseUrl: '', fetchImpl });

    expect(trips).toHaveLength(1);
    expect(trips[0]).toMatchObject({
      id: 't1',
      title: '洛阳两日',
      startedAt: '2026-04-12T01:00:00+00:00',
      endedAt: null,
      source: 'manual',
      itemCount: 0,
    });
    // 归一化必须真的发生：这两个 key 在 raw 里叫 started_at / item_count
    expect(trips[0].startedAt).toBe('2026-04-12T01:00:00+00:00');
  });

  it('空列表是正常结果，不抛错', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ trips: [] }));
    await expect(listTrips({ baseUrl: '', fetchImpl })).resolves.toEqual([]);
  });
});

describe('createTrip', () => {
  it('POST 到 /api/v1/trips 并返回后端给的那一条', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          trip: {
            id: 't-new',
            title: '洛阳两日',
            started_at: null,
            ended_at: null,
            source: 'manual',
            item_count: 0,
            items: [],
          },
        },
        201,
      ),
    );

    const trip = await createTrip('  洛阳两日  ', { baseUrl: '', fetchImpl });

    // 前端原样把标题传上去（剥空白由后端做，避免两端各剥一半口径不一致）
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/trips');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ title: '  洛阳两日  ' });

    expect(trip.source).toBe('manual');
    expect(trip.id).toBe('t-new');
  });

  it('404 翻译成「能力未启用」而不是干巴巴的 HTTP 404', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ detail: '该能力未启用' }, 404),
    );

    await expect(createTrip('洛阳两日', { baseUrl: '', fetchImpl })).rejects.toThrow(
      /尚未启用/,
    );
  });

  it('422（标题为空）把后端原因带进错误信息', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ detail: '行程标题不能为空' }, 422),
    );

    await expect(createTrip('   ', { baseUrl: '', fetchImpl })).rejects.toThrow(
      /行程标题不能为空/,
    );
  });

  it('网络异常也抛成 Error（不静默返回空）', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(createTrip('洛阳两日', { baseUrl: '', fetchImpl })).rejects.toThrow(
      /网络请求异常/,
    );
  });

  it('模块级注入可被调用参数覆盖', async () => {
    const moduleLevel = vi.fn().mockResolvedValue(jsonResponse({ trip: {} }, 201));
    const perCall = vi.fn().mockResolvedValue(
      jsonResponse({ trip: { id: 'from-call', title: 'x', source: 'manual' } }, 201),
    );

    setTripsApi({ baseUrl: '', fetchImpl: moduleLevel });
    await createTrip('洛阳两日', { baseUrl: '', fetchImpl: perCall });

    expect(perCall).toHaveBeenCalledTimes(1);
    expect(moduleLevel).not.toHaveBeenCalled();
  });
});
