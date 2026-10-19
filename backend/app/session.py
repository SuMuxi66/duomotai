from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import asyncio
import time
import hashlib
from collections import deque
from .context_engine import ContextEngine

@dataclass
class SessionState:
    # 会话 ID（用于按会话隔离并恢复 AI 记忆）
    session_id: str = "default"

    # 视觉观察记录缓存（结构化存储，包含时间戳和文本）
    vision_observations: List[Dict[str, Any]] = field(default_factory=list)
    max_observations: int = 10  # 最大缓存数量
    last_vision_analysis: float = 0  # 上次分析时间戳
    
    # 重复图像检测
    last_frame_hash: Optional[str] = None  # 上一帧的哈希
    last_analysis_time: float = 0  # 上次分析时间
    analysis_cooldown: float = 2.0  # 最小分析间隔（秒）
    frame_hash_cache: deque = field(default_factory=lambda: deque(maxlen=10))  # 最近10帧的哈希缓存
    analysis_result_cache: Dict[str, Tuple[str, float]] = field(default_factory=dict)  # 分析结果缓存（哈希 -> (结果, 时间戳)）
    result_cache_ttl: float = 30.0  # 结果缓存TTL（秒）

    # 对话历史统一由 context_engine 管理（按 session_id 分桶 + 分层压缩）

    # 音频缓冲（按 seq 拼接）- 用于原有 audio_chunk 模式
    audio_chunks: Dict[int, bytes] = field(default_factory=dict)
    audio_sample_rate: int = 16000
    stt_partial_busy: bool = False  # 中间结果异步转录占用标志，防并发

    # 流式音频缓冲 - 用于 audio_stream 模式
    stream_audio_chunks: Dict[int, bytes] = field(default_factory=dict)
    stream_audio_seq: int = 0  # 当前流式音频序列号

    # 云端流式 STT 会话（Fun-ASR create_stream：stream=true 帧实时喂入，
    # audio_stream_end 收口出 final；断连/空闲由看门狗与连接清理回收）
    stt_stream: Optional[Any] = None

    # 打断控制
    interrupted: bool = False  # 打断标志
    current_task: Optional[asyncio.Task] = None  # 当前正在执行的任务

    # 视频帧处理
    video_frame_queue: asyncio.Queue[Dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=20))
    video_frame_processing: bool = False

    # Provider 缓存
    stt_runtime_key: Optional[Tuple[str, Optional[str], Optional[str]]] = None
    tts_provider: str = "browser"
    tts_model: Optional[str] = None
    tts_voice: Optional[str] = None
    tts_api_key: Optional[str] = None

    # 控制参数（可动态调整）
    vision_enabled: bool = True
    vision_fps: float = 0.5
    camera_source: str = "usb"  # "usb" | "glasses"
    audio_source: str = "local"  # "local" | "glasses"
    custom_prompt: str = ""  # 个性化提示词

    # 智能检测开关
    danger_alert_enabled: bool = True  # 危险报警（烟雾、锅盖跳动、泡沫溢出）
    oil_smoke_alert_enabled: bool = True  # 油烟检测

    # 智能检测状态（迁移自全局变量，支持多会话隔离）
    last_danger_alert_time: float = 0.0
    last_smoke_alert_time: float = 0.0
    last_pot_alert_time: float = 0.0
    last_oil_smoke_alert_time: float = 0.0
    last_oil_overtemp_alert_time: float = 0.0  # 油温超温报警（200°C 上限）上次提醒时间
    oil_smoke_pending_response: bool = False
    oil_smoke_timeout_timer: Optional[asyncio.Task] = None
    # 烟雾 → 风扇询问待响应状态（与 oil_smoke 同构）
    smoke_pending_fan_response: bool = False
    smoke_timeout_timer: Optional[asyncio.Task] = None

    # LLM 生成参数
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    llm_top_p: float = 0.95
    llm_sample_count: int = 1
    llm_frequency_penalty: float = 0.0
    llm_response_format: str = "md"
    # 🔒 已写死关闭（2026-09-15）：默认 False，且 llm_handler 会忽略前端传来的 True；
    # 真正的权威收口在 providers/unified.THINKING_HARD_DISABLED。置回 True 需三处一起改。
    llm_enable_thinking: bool = False

    # Session 级别的 Provider/Model/API Key 覆盖（避免修改全局 settings）
    # 文字与视觉统一走 VLM 端点，LLM provider 字段已移除
    vlm_provider: Optional[str] = None
    vlm_model: Optional[str] = None
    llm_api_key: Optional[str] = None  # 共享密钥（VLM 复用）
    # 自定义 provider 配置（OpenAI / Anthropic 兼容）
    vlm_base_url: Optional[str] = None
    vlm_api_format: Optional[str] = None
    stt_provider: Optional[str] = None
    stt_model: Optional[str] = None
    stt_api_key: Optional[str] = None

    # 油温监测提醒（Oil Watch）
    oil_watch: Optional[dict] = None  # {target: 目标温度, started_at: 启动时间, consecutive: 连续命中数}
    oil_watch_task: Optional[asyncio.Task] = None  # 监测后台任务

    def add_observation(self, text: str, timestamp: float = None):
        """添加视觉观察记录，自动维护缓存大小"""
        if timestamp is None:
            timestamp = time.time()

        self.vision_observations.append({
            "text": text,
            "timestamp": timestamp
        })

        # 保持缓存大小不超过最大值
        if len(self.vision_observations) > self.max_observations:
            self.vision_observations = self.vision_observations[-self.max_observations:]

        self.last_vision_analysis = timestamp

    def get_recent_observations(self, count: int = None) -> List[str]:
        """获取最近的观察记录文本列表"""
        if count is None:
            count = len(self.vision_observations)
        return [obs["text"] for obs in self.vision_observations[-count:]]

    def clear_observations(self):
        """清空观察记录缓存"""
        self.vision_observations.clear()
        self.last_vision_analysis = 0
    
    def get_frame_hash(self, jpeg_bytes: bytes) -> str:
        """计算图像哈希用于去重
        
        Args:
            jpeg_bytes: JPEG图像字节
            
        Returns:
            图像哈希字符串
        """
        return hashlib.md5(jpeg_bytes[:4096]).hexdigest()
    
    def should_analyze_frame(self, frame_hash: str) -> bool:
        """判断是否需要分析此帧
        
        Args:
            frame_hash: 图像哈希
            
        Returns:
            True表示需要分析，False表示跳过
        """
        now = time.time()
        
        if now - self.last_analysis_time < self.analysis_cooldown:
            return False
        
        if frame_hash in self.frame_hash_cache:
            return False
        
        return True
    
    def get_cached_analysis(self, frame_hash: str) -> Optional[str]:
        """从缓存获取分析结果
        
        Args:
            frame_hash: 图像哈希
            
        Returns:
            缓存的分析结果，如果没有则返回None
        """
        if frame_hash in self.analysis_result_cache:
            result, timestamp = self.analysis_result_cache[frame_hash]
            if time.time() - timestamp < self.result_cache_ttl:
                return result
            del self.analysis_result_cache[frame_hash]
        return None
    
    def cache_analysis_result(self, frame_hash: str, analysis: str):
        """缓存分析结果
        
        Args:
            frame_hash: 图像哈希
            analysis: 分析结果
        """
        now = time.time()
        self.analysis_result_cache[frame_hash] = (analysis, now)
        self.last_analysis_time = now
        self.last_frame_hash = frame_hash
        self.frame_hash_cache.append(frame_hash)
        
        expired_keys = [k for k, (_, ts) in self.analysis_result_cache.items() 
                       if now - ts > self.result_cache_ttl]
        for k in expired_keys:
            del self.analysis_result_cache[k]

    # 做饭模式状态
    cooking_enabled: bool = False  # 是否启用做饭模式
    cooking_state: str = "IDLE"  # IDLE, COLLECTING_INGREDIENTS, RECOMMENDING, AWAITING_SERVINGS, COOKING, STEP_WAITING, FINISHED
    current_recipe: Optional[Dict[str, Any]] = None  # 当前菜谱
    current_step: int = 0  # 当前步骤（1-based）
    completed_steps: List[int] = field(default_factory=list)  # 已完成的步骤列表
    user_ingredients: List[str] = field(default_factory=list)  # 用户食材
    last_recommended_recipe_names: List[str] = field(default_factory=list)  # 最近推荐的菜谱名称
    current_servings: Optional[int] = None  # 当前菜谱份数
    awaiting_servings: bool = False  # 是否正在等待用户确认份数
    awaiting_cooking_exit_confirmation: bool = False  # 是否正在等待用户确认退出做饭模式
    awaiting_step_guide_confirmation: bool = False  # 是否在等待用户确认开始步骤式引导（AI 主动询问后）
    awaiting_ingredients_ready: bool = False  # 智能计算完成后等待用户确认食材已备齐
    # 盐量脉冲链路（2026-09-05）：问"需要放多少盐"→按秤上食材克重静默计算→提示放锅铲
    # →用户"好了"→下料机通电 3 秒脉冲出料。此标志为该链路的"等待锅铲就位确认"门。
    awaiting_salt_pulse_confirmation: bool = False
    salt_pulse_grams: float = 0.0  # 本次按秤重计算出的盐量（仅记日志用，不播报）
    salt_pulse_requested_at: float = 0.0  # 触发时间戳（确认门 10 分钟过期防陈旧标志劫持）
    # 油温盯守确认门（2026-09-06）：油温快答发出"我帮你盯着油温"提议后等用户短确认
    # （"需要，帮我盯着吧"→启动 OilWatch）。一次性监听，下一条非确认即清标志放行，
    # 参照盐量脉冲确认门模式。修复"帮我盯着"被 LLM 自由发挥成 [FAN_ON] 开风扇的误路由。
    awaiting_oil_watch_confirm: bool = False
    oil_watch_proposed_target: Optional[int] = None  # 提议时算出的目标温度（无则用 OilWatch 兜底）
    oil_watch_proposed_at: float = 0.0  # 提议时间戳（确认门 10 分钟过期防陈旧标志劫持）
    # 菜名请求直查到的菜谱缓存：用户确认引导后直接用，避免二次解析
    # （区别于 current_recipe：pending 是"确认前"暂存的候选，确认后才上屏为当前菜谱）
    pending_guide_recipe: Optional[Dict[str, Any]] = None
    # 最近用户消息缓存（用于确认步骤式引导时回找用户说过的菜名）
    recent_user_texts: deque = field(default_factory=lambda: deque(maxlen=5))

    # 上下文引擎（全局对话历史管理）
    context_engine: ContextEngine = field(default_factory=ContextEngine)
