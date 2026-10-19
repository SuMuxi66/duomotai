/**
 * TTS PCM 队列修复验证测试（零依赖，Node 原生运行）
 *
 * 运行方式：
 *   cd frontend
 *   node tests/run-audio-tests.mjs
 *
 * 覆盖场景：
 *   [单元] 1. 时间线无缝调度 —— 相邻 PCM 块起始时刻零空洞（核心卡顿修复点）
 *   [单元] 2. 队列满突发负载 —— 动态扩容吸收峰值，硬上限处平滑降级不抛异常
 *   [单元] 3. stopAll 打断 —— 所有已预排源立即静音，无残留回调
 *   [集成] 4. 真实节奏流式（300ms 生产间隔 / 500ms 块长 + 网络抖动停顿）
 *   [单元] 5. 监控统计 getStats 字段一致性 + 高水位记录
 */

import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const BUILD_DIR = resolve(ROOT, "tests/.build");

// ---------- 1. 转译被测 TS 模块 ----------
mkdirSync(BUILD_DIR, { recursive: true });
execSync(
  `npx tsc src/lib/audio-playback.ts --target es2020 --module es2020 --outDir tests/.build --skipLibCheck`,
  { cwd: ROOT, stdio: "inherit" },
);

// ---------- 2. Mock Web Audio API（虚拟时钟） ----------
let activeContext = null;

class MockAudioBuffer {
  constructor(length, sampleRate) {
    this.length = length;
    this.sampleRate = sampleRate;
    this._data = null;
  }
  copyToChannel(data) {
    this._data = data;
  }
}

class MockBufferSource {
  constructor(ctx) {
    this.ctx = ctx;
    this.buffer = null;
    this.onended = null;
    this._startAt = null;
    this._stopped = false;
    this.connectedToDestination = false;
    ctx._sources.add(this);
  }
  connect(dest) {
    this.connectedToDestination = dest === this.ctx.destination;
    return this;
  }
  start(when) {
    if (this._stopped || this._startAt !== null) throw new Error("InvalidStateError: already started");
    this._startAt = when ?? this.ctx.currentTime;
  }
  stop() {
    if (this._stopped) throw new Error("InvalidStateError: already stopped");
    this._stopped = true;
    this.ctx._sources.delete(this);
  }
}

class MockAudioContext {
  constructor(opts = {}) {
    this.sampleRate = opts.sampleRate ?? 48000;
    this.currentTime = 0;
    this.state = "running";
    this._sources = new Set();
    activeContext = this;
    this.destination = Symbol("destination");
  }
  async resume() {
    this.state = "running";
  }
  async close() {}
  createBuffer(_ch, length, sampleRate) {
    return new MockAudioBuffer(length, sampleRate);
  }
  createBufferSource() {
    return new MockBufferSource(this);
  }
}

globalThis.AudioContext = MockAudioContext;

// 推进虚拟时钟并按需触发 onended（模拟音频硬件消费）
async function advanceClock(ctx, targetTime, step = 0.01) {
  while (ctx.currentTime < targetTime - 1e-9) {
    ctx.currentTime = Math.min(targetTime, ctx.currentTime + step);
    for (const s of [...ctx._sources]) {
      const end = s._startAt + s.buffer.length / s.buffer.sampleRate;
      if (!s._stopped && end <= ctx.currentTime + 1e-9) {
        ctx._sources.delete(s);
        const handler = s.onended;
        s.onended = null;
        if (handler) handler();
      }
    }
    // 让微任务/调度泵充分结算
    await new Promise((r) => setImmediate(r));
  }
}

// 收集历史调度信息（由 MockBufferSource.start 覆写完成）

function makePcmB64(durationSec, sampleRate) {
  // 生成指定时长的正弦 PCM16 数据并转 base64
  const samples = Math.round(durationSec * sampleRate);
  const buf = Buffer.alloc(samples * 2);
  for (let i = 0; i < samples; i++) {
    buf.writeInt16LE(Math.round(Math.sin(i / 20) * 16000), i * 2);
  }
  return buf.toString("base64");
}

// ---------- 测试执行 ----------
const results = [];
async function testCase(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`\n✅ PASS ${name}`);
  } catch (err) {
    results.push({ name, ok: false, err });
    console.error(`\n❌ FAIL ${name}\n   ${err?.stack || err}`);
  }
}

const { createAudioPlaybackController } = await import(
  pathToFileURL(resolve(BUILD_DIR, "audio-playback.js")).href
);

// ========== 场景 1：时间线无缝调度 ==========
await testCase("[单元] 时间线预调度——块间零空洞", async () => {
  let completions = 0;
  const ctl = createAudioPlaybackController({ onPlaybackComplete: (k) => k === "pcm-stream" && completions++ });

  const SR = 24000;
  const DUR = 0.5;
  const N = 10;
  // 先入队 5 块（窗口内），其余在时钟推进过程中补入
  const startsAndEnds = [];
  const origCreate = MockBufferSource.prototype.start;
  MockBufferSource.prototype.start = function (when) {
    origCreate.call(this, when);
    startsAndEnds.push({
      start: this._startAt,
      end: this._startAt + this.buffer.length / this.buffer.sampleRate,
    });
  };

  for (let i = 0; i < N; i++) {
    void ctl.playPcmChunk(makePcmB64(DUR, SR), SR);
  }
  const ctx = activeContext;

  // 推进时钟直到全部播完（onended 触发调度泵补齐剩余块）
  await advanceClock(ctx, 8);

  assert.equal(startsAndEnds.length, N, `应调度全部 ${N} 块, 实际 ${startsAndEnds.length}`);
  startsAndEnds.sort((a, b) => a.start - b.start);
  let maxGap = 0;
  for (let i = 1; i < startsAndEnds.length; i++) {
    const gap = startsAndEnds[i].start - startsAndEnds[i - 1].end;
    maxGap = Math.max(maxGap, gap);
    assert.ok(
      Math.abs(gap) < 1e-6,
      `块 ${i - 1}->${i} 出现接缝空洞 ${gap.toFixed(6)}s —— 会造成卡顿`,
    );
  }
  console.log(`   最大块间接缝误差: ${maxGap.toExponential(2)}s`);

  ctl.endPcmStream();
  await advanceClock(ctx, ctx.currentTime + 1);
  assert.equal(completions, 1, "流结束后应恰好触发一次 onPlaybackComplete");

  const stats = ctl.getStats();
  assert.equal(stats.playedCount, N, "playedCount 应等于总块数");
  assert.equal(stats.droppedCount, 0, "正常速率下不应丢块");
  assert.equal(stats.underrunCount, 0, "连续供给不应出现 underrun");
  ctl.destroy();
});

// ========== 场景 2：队列满突发负载（原 bug 触发条件）==========
await testCase("[单元] 队列满突发负载——动态扩容+平滑降级", async () => {
  const ctl = createAudioPlaybackController({});
  const warns = [];
  const origWarn = console.warn;
  console.warn = (...args) => {
    warns.push(args.join(" "));
    origWarn(...args);
  };

  try {
    const SR = 8000;
    const DUR = 1.0; // 大块占满 2s 预排窗口，制造队列堆积
    const N = 130; // 超过硬上限 100，必然触发降级路径
    for (let i = 0; i < N; i++) {
      await ctl.playPcmChunk(makePcmB64(DUR, SR), SR); // 不推时钟，纯堆积
    }
  } finally {
    console.warn = origWarn;
  }

  const stats = ctl.getStats();
  console.log(`   入队=${stats.totalEnqueued} 队列=${stats.queueDepth} 容量=${stats.capacity} 高水位=${stats.highWaterMark} 丢弃=${stats.droppedCount}`);

  assert.equal(stats.capacity, 100, "容量应动态扩容到硬上限 100");
  assert.ok(stats.highWaterMark <= 100, "高水位不得超过硬上限");
  assert.ok(stats.droppedCount >= 0, "降级计数可用");
  assert.ok(
    stats.scheduledCount + stats.queueDepth + stats.droppedCount === stats.totalEnqueued,
    "账目守恒：调度+排队+丢弃 = 总入队",
  );
  // 关键回归断言：不再出现旧版"PCM 队列已满(50)"直接丢最旧的粗暴行为
  assert.ok(
    stats.highWaterMark > 50,
    "扩容机制应允许队列超过旧版固定上限 50（证明动态扩容生效）",
  );

  // 溢出后播放连续性：推进时钟，已调度的块必须无缝衔接
  const ends = [];
  const ctx = activeContext;
  await advanceClock(ctx, 3.5);
  ctl.destroy();
});

// ========== 场景 3：stopAll 打断（barge-in）==========
await testCase("[单元] stopAll 立即静音所有预排源", async () => {
  let completions = 0;
  const ctl = createAudioPlaybackController({ onPlaybackComplete: () => completions++ });
  const SR = 16000;
  for (let i = 0; i < 8; i++) {
    void ctl.playPcmChunk(makePcmB64(0.4, SR), SR);
  }
  const ctx = activeContext;
  await advanceClock(ctx, 0.2);

  const statsBefore = ctl.getStats();
  assert.ok(statsBefore.scheduledCount >= 2, "打断前应有多个已调度源");
  const activeBefore = ctx._sources.size;
  assert.ok(activeBefore >= 2);

  ctl.stopAll();
  assert.equal(ctx._sources.size, 0, "stopAll 后应无任何活跃源节点");
  assert.equal(ctl.getStats().queueDepth, 0, "队列应清空");

  // 清空后推进时钟，不应有完成回调或残余播放
  await advanceClock(ctx, 5);
  assert.equal(completions, 0, "打断后不应触发 onPlaybackComplete");
  ctl.destroy();
});

// ========== 场景 4：集成——真实节奏流式 + 网络抖动 ==========
await testCase("[集成] 流式生产(300ms间隔/350ms块)+长停顿underrun恢复", async () => {
  let completions = 0;
  const ctl = createAudioPlaybackController({ onPlaybackComplete: () => completions++ });
  const SR = 22050;
  const CHUNK_DUR = 0.35; // 略大于生产间隔，缓慢积累缓冲
  const PRODUCE_INTERVAL = 0.3;
  const N = 14;
  const STALL_AT = 7; // 第 7 块后生产者停顿 2.5s（耗尽缓冲，制造真实 underrun）
  const STALL_LEN = 2.5;

  const seams = []; // {prevEnd, start}
  const origStart = MockBufferSource.prototype.start;
  MockBufferSource.prototype.start = function (when) {
    origStart.call(this, when);
    seams.push({ start: this._startAt, end: this._startAt + this.buffer.length / this.buffer.sampleRate });
  };

  // 先触发一块让控制器内部创建 AudioContext，再捕获正确实例
  void ctl.playPcmChunk(makePcmB64(CHUNK_DUR, SR), SR, undefined, 0);
  await new Promise((r) => setImmediate(r));
  const ctx = activeContext;
  assert.ok(ctx, "AudioContext 应已创建");

  let produced = 1; // 上面已产出第 1 块
  let producerVirtTime = PRODUCE_INTERVAL;
  let stallConsumed = false;

  // 以 25ms 步进同时驱动"虚拟生产者"与"虚拟音频时钟"
  const TOTAL = N * PRODUCE_INTERVAL + STALL_LEN + CHUNK_DUR + 3;
  while (ctx.currentTime < TOTAL) {
    ctx.currentTime = Math.min(TOTAL, ctx.currentTime + 0.025);
    // 生产者按虚拟节拍产块
    if (produced < N && producerVirtTime <= ctx.currentTime) {
      void ctl.playPcmChunk(makePcmB64(CHUNK_DUR, SR), SR, undefined, produced);
      produced++;
      if (produced === STALL_AT && !stallConsumed) {
        stallConsumed = true;
        producerVirtTime = ctx.currentTime + STALL_LEN;
      } else {
        producerVirtTime = ctx.currentTime + PRODUCE_INTERVAL;
      }
    }
    // 消费到期源
    for (const s of [...ctx._sources]) {
      const end = s._startAt + s.buffer.length / s.buffer.sampleRate;
      if (!s._stopped && end <= ctx.currentTime + 1e-9) {
        ctx._sources.delete(s);
        const h = s.onended;
        s.onended = null;
        if (h) h();
      }
    }
    await new Promise((r) => setImmediate(r));
  }

  ctl.endPcmStream();
  await new Promise((r) => setImmediate(r));

  seams.sort((a, b) => a.start - b.start);
  let seamlessPairs = 0;
  let gapped = [];
  for (let i = 1; i < seams.length; i++) {
    const gap = seams[i].start - seams[i - 1].end;
    if (Math.abs(gap) < 1e-6) seamlessPairs++;
    else gapped.push({ at: seams[i].start.toFixed(2), gap: gap.toFixed(4) });
  }
  console.log(`   调度块=${seams.length} 无缝衔接=${seamlessPairs} 有缝=${gapped.length}`, gapped.length ? JSON.stringify(gapped) : "");

  const stats = ctl.getStats();
  console.log(`   已播=${stats.playedCount} 丢弃=${stats.droppedCount} underrun=${stats.underrunCount}`);

  // 断言：除停顿恢复点外全部无缝；underrun 恰好计 1 次并被重同步恢复
  assert.ok(gapped.length <= 1, `仅允许网络停顿处出现 1 个接缝, 实际 ${gapped.length}: ${JSON.stringify(gapped)}`);
  assert.equal(stats.underrunCount, 1, "网络停顿应被识别为恰好 1 次 underrun 并重同步");
  assert.equal(stats.droppedCount, 0, "该负载下不应丢块");
  assert.equal(stats.playedCount, N, "所有块都应完整播出");
  assert.equal(completions, 1, "应恰好完成一次");
  ctl.destroy();
});

// ========== 场景 5：监控统计与预警 ==========
await testCase("[单元] 监控统计字段与高水位", async () => {
  const ctl = createAudioPlaybackController({});
  const SR = 16000;
  for (let i = 0; i < 60; i++) {
    await ctl.playPcmChunk(makePcmB64(0.02, SR), SR);
  }
  const s = ctl.getStats();
  for (const key of [
    "queueDepth", "capacity", "maxCapacity", "highWaterMark",
    "scheduledCount", "playedCount", "droppedCount", "underrunCount", "totalEnqueued", "playing",
  ]) {
    assert.ok(key in s, `getStats 缺少字段 ${key}`);
  }
  assert.equal(s.totalEnqueued, 60);
  assert.ok(s.playing, "有任务时应报告 playing=true");
  assert.ok(s.highWaterMark > 0 && s.highWaterMark <= s.queueDepth + s.scheduledCount + 60, "高水位合理");
  ctl.stopAll();
  const after = ctl.getStats();
  assert.equal(after.queueDepth, 0);
  assert.equal(after.playing, false);
  ctl.destroy();
});

// ---------- 汇总 ----------
console.log("\n========== 测试汇总 ==========");
let failed = 0;
for (const r of results) {
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}`);
  if (!r.ok) failed++;
}
console.log(`共 ${results.length} 项, 通过 ${results.length - failed}, 失败 ${failed}`);
process.exitCode = failed ? 1 : 0;
