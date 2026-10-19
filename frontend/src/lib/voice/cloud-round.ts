/**
 * 云端流式 STT 轮次状态（begin/push/end 通道由调用方注入；一轮收音 = 开会话逐帧上行 → 收口出 final）。
 * 唤醒词与持续识别两条链路共用，互斥不并发。
 *
 * 从 wakeword-stt.ts 抽取。源文件里 createSttWakeWordListener 与
 * createSttContinuousListener 各自维护了一组语义完全相同的云端流式会话状态
 * （cloudStreamActive / cloudStreamEnded / cloudPartialSeen 三变量 +
 * beginCloudStreamRound / endCloudStreamOnce / 复位 三件套），这里合并为一份权威实现。
 * 音频逐帧上行（push）不在本类职责内——调用方在音频帧回调里自行判断 active 后推流。
 *
 * 与源码的细节差异兼容说明：
 *  - 门控（云端优先开关、通道是否存在）在调用方先判，再调 begin（唤醒词版源码在
 *    begin 内部判门控，复位在前、门控在后；门控不过时调用方改用 reset(空操作)
 *    仅复位标志、不补发 end，语义等价）；
 *  - begin 失败（openChannel 抛错）统一把 active 复位为 false——持续识别版源码如此，
 *    唤醒词版 catch 后不再使用该标志，置 false 语义等价；
 *  - 日志文案照抄各自版本，仅前缀（tag）不同。
 */

/** 构造参数：tag 为日志前缀（含方括号，如 '[SttWakeWord]' / '[SttContinuous]'） */
export interface CloudStreamRoundOptions {
  /** 日志前缀（含方括号），如 '[SttWakeWord]' / '[SttContinuous]' */
  tag: string;
  /** 调试日志开关（与创建方监听器的 debug 一致；成功/失败日志均为 debug 门控） */
  debug?: boolean;
}

/**
 * 一轮"云端流式收音"的状态机：
 * begin（开会话，此后音频逐帧上行）→ endOnce（收口出 stt_final，end 只发一次）→
 * reset（收尾复位，避免跨轮串扰）。
 */
export class CloudStreamRound {
  private readonly tag: string;
  private readonly debug: boolean;

  // 本轮已开启云端流式（音频逐帧上行中）。正常只应外部读取；翻转只发生在
  // begin / endOnce / reset 内部（保留公有可写：持续识别版源码存在收口后直接
  // 置 false 的写点，重接线时需要等价直写能力）。
  active = false;
  // 本轮已发过收口（end 只发一次）。同上，外部通常只读。
  ended = false;
  // 本轮收到过云端文本：此后输入框显示以云端为准，端侧/浏览器实时文本不再覆盖
  // （云端优先显示）。多个闭包（实时转写回调/端侧 partial 分支）要读它，必须公有可读写。
  partialSeen = false;

  constructor(options: CloudStreamRoundOptions) {
    this.tag = options.tag;
    this.debug = options.debug ?? false;
  }

  /**
   * 开启新一轮云端流式会话：先复位三标志，再调用 openChannel（外部 cloudStream.begin）。
   * 门控（云端优先开关是否开启、通道是否存在）由调用方先判——门控不过时不调本方法，
   * 仅需复位标志时改用 reset(空操作)。openChannel 抛错视为开启失败：active 复位为
   * false，本轮回退整段模式。
   */
  begin(openChannel: () => void): void {
    this.active = false;
    this.ended = false;
    this.partialSeen = false;
    try {
      openChannel();
      this.active = true;
      if (this.debug) console.log(`${this.tag} 云端流式会话已开启，逐帧上行`);
    } catch (e) {
      this.active = false;
      if (this.debug) console.warn(`${this.tag} 云端流式开启失败，本轮回退整段模式`, e);
    }
  }

  /**
   * 收口本轮流式会话（幂等：!active 或已收口则直接 return，end 只发一次）。
   * 通道是否存在由调用方保证（无通道时轮次 active 恒为 false，本方法自然空操作）。
   */
  endOnce(closeChannel: () => void): void {
    if (!this.active || this.ended) return;
    this.ended = true;
    try { closeChannel(); } catch (e) { /* 忽略 */ }
  }

  /**
   * 收尾复位：先补发收口，再把 active/ended/partialSeen 全部复位，避免跨轮串扰。
   * 停止/链路切换等"只复位标志、不补发 end"的场景（未收口的会话由后端空闲看门狗回收）
   * 传空操作 () => {} 即可——endOnce 对空操作等价于跳过补发，净效果与裸复位一致。
   */
  reset(closeChannel: () => void): void {
    // 防悬空流式会话（本轮没走到收口分支就结束时补发 end；后端另有空闲看门狗兜底）
    this.endOnce(closeChannel);
    this.active = false;
    this.ended = false;
    this.partialSeen = false;
  }
}
