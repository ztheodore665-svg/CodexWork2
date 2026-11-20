// 纯 Node 环境（不声明 jsdom）：签名 / 验签都是字节运算。
import { describe, expect, it } from 'vitest';

import { ed25519 } from '@noble/curves/ed25519.js';

import {
  hexToBytes,
  publicKeyFromSeed,
  signWithSeed,
  bytesToHex,
} from '../core/identity';
import {
  JOURNEY_JSON_VERSION,
  demoPhotosForExport,
  journeyFileJson,
  signJourneyFile,
  stableStringify,
  verifyJourneyFile,
} from './exportJourney';
import { buildJourneyBundle } from '../core/journeyBuild';
import type { SystemPhoto } from '@travelmemory/photo-source';

/** 固定测试种子（仅测试用，绝不用于真实身份）。 */
const SEED = '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f';

function photo(id: string, minute: number): SystemPhoto {
  return {
    id,
    bytes: new ArrayBuffer(0),
    name: `${id}.jpg`,
    takenAt: new Date(Date.UTC(2026, 3, 18, 1, minute)).toISOString(),
    lat: 30.24 + minute * 0.001,
    lng: 120.14 + minute * 0.001,
    thumbnailDataUrl: 'data:image/png;base64,AA==',
  };
}

function sampleBundle() {
  return buildJourneyBundle({
    title: '测试旅程',
    photos: [photo('a', 0), photo('b', 10)],
    exportedAt: '2026-04-18T02:00:00.000Z',
  });
}

describe('signWithSeed —— 用种子对任意字节签名', () => {
  it('签名能被同身份的公钥验证', () => {
    const message = new TextEncoder().encode('hello travelmemory');
    const signature = signWithSeed(SEED, message);
    const pubHex = publicKeyFromSeed(SEED);

    // bare hex，128 字符 = 64 字节
    expect(signature).toMatch(/^[0-9a-f]{128}$/);
    expect(ed25519.verify(hexToBytes(signature), message, hexToBytes(pubHex))).toBe(true);
  });

  it('消息改一个字节 → 验签失败', () => {
    const message = new TextEncoder().encode('hello travelmemory');
    const signature = signWithSeed(SEED, message);
    const tampered = new Uint8Array(message);
    tampered[0] = tampered[0] ^ 0x01;

    const pubHex = publicKeyFromSeed(SEED);
    expect(ed25519.verify(hexToBytes(signature), tampered, hexToBytes(pubHex))).toBe(false);
  });

  it('带 `ed25519:` 前缀的种子也能签（主应用注入的就是这种口径）', () => {
    const message = new TextEncoder().encode('x');
    expect(signWithSeed(`ed25519:${SEED}`, message)).toBe(signWithSeed(SEED, message));
  });

  it('种子长度不对 → 抛错（绝不返回空签名冒充"签过了"）', () => {
    expect(() => signWithSeed('00', new Uint8Array([1]))).toThrow(/种子/);
  });
});

describe('signJourneyFile —— journey.json 的签名封装', () => {
  it('含 version / bundle / signerPubkey / signature，且字段类型正确', () => {
    const file = signJourneyFile(sampleBundle(), SEED);

    expect(file.version).toBe(JOURNEY_JSON_VERSION);
    expect(file.version).toBe('journey-json-1');
    expect(typeof file.bundle).toBe('object');
    expect(file.signerPubkey).toBe(`ed25519:${publicKeyFromSeed(SEED)}`);
    expect(file.signerPubkey).toMatch(/^ed25519:[0-9a-f]{64}$/);
    expect(file.signature).toMatch(/^[0-9a-f]{128}$/);
  });

  it('验签通过；改 bundle 里一个字节（地点名）→ 验签失败', () => {
    const file = signJourneyFile(sampleBundle(), SEED);
    expect(verifyJourneyFile(file)).toBe(true);

    // 深拷贝后改一个地点名（JSON 里一个字符之差就足以让字节变）
    const tampered = JSON.parse(JSON.stringify(file)) as typeof file;
    tampered.bundle.days[0].places[0].name = `${tampered.bundle.days[0].places[0].name}x`;
    expect(verifyJourneyFile(tampered)).toBe(false);
  });

  it('改 version → 验签失败（版本号也在签名范围内）', () => {
    const file = signJourneyFile(sampleBundle(), SEED);
    const tampered = { ...file, version: 'journey-json-2' as typeof file.version };
    expect(verifyJourneyFile(tampered)).toBe(false);
  });

  it('换签名人（换公钥）→ 验签失败', () => {
    const file = signJourneyFile(sampleBundle(), SEED);
    const tampered = {
      ...file,
      signerPubkey: `ed25519:${publicKeyFromSeed('ff'.repeat(32))}`,
    };
    expect(verifyJourneyFile(tampered)).toBe(false);
  });

  it('结构不对一律返回 false，不抛错', () => {
    expect(verifyJourneyFile(null)).toBe(false);
    expect(verifyJourneyFile({})).toBe(false);
    expect(verifyJourneyFile({ version: 'journey-json-1' })).toBe(false);
    expect(
      verifyJourneyFile({ version: 'journey-json-1', bundle: {}, signerPubkey: 'x', signature: 'y' }),
    ).toBe(false);
  });

  it('落盘文本是稳定 JSON：同一份 bundle 两次签出的文本逐字节相同', () => {
    const bundle = sampleBundle();
    const first = journeyFileJson(signJourneyFile(bundle, SEED));
    const second = journeyFileJson(signJourneyFile(bundle, SEED));
    expect(first).toBe(second);

    // 字段顺序 = 声明顺序（人眼可读），且能原样 parse 回来
    const parsed = JSON.parse(first) as Record<string, unknown>;
    expect(Object.keys(parsed)).toEqual(['version', 'bundle', 'signerPubkey', 'signature']);
  });

  it('文件里绝不出现种子', () => {
    const json = journeyFileJson(signJourneyFile(sampleBundle(), SEED));
    expect(json.includes(SEED)).toBe(false);
  });
});

describe('stableStringify —— 稳定序列化', () => {
  it('对象 key 排序、数组保序、undefined 被丢掉', () => {
    expect(stableStringify({ b: 1, a: [3, 1, 2] })).toBe('{"a":[3,1,2],"b":1}');
    expect(stableStringify({ a: undefined, b: 2 })).toBe('{"b":2}');
    // 嵌套同样排序
    expect(stableStringify({ z: { y: 1, x: 2 } })).toBe('{"z":{"x":2,"y":1}}');
  });
});

describe('demoPhotosForExport —— 演示线适配', () => {
  it('内核 Photo → SystemPhoto：时间转 ISO、地名进文件名、不带原图字节', () => {
    const [result] = demoPhotosForExport([
      {
        id: 'd1',
        thumb: 'data:image/png;base64,AA==',
        lat: 30.1,
        lng: 120.2,
        takenAt: Date.UTC(2026, 3, 18, 1, 0),
        placeName: '塔尔寺',
        locationSource: 'exif',
      },
    ]);

    expect(result.id).toBe('d1');
    expect(result.name).toBe('塔尔寺.jpg');
    expect(result.takenAt).toBe('2026-04-18T01:00:00.000Z');
    expect(result.lat).toBe(30.1);
    expect(result.lng).toBe(120.2);
    // 原图字节永远不进 journey.json；这里给的是空 buffer
    expect(result.bytes.byteLength).toBe(0);
    expect(bytesToHex(new Uint8Array(0))).toBe('');
  });
});
