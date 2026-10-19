# Cooking 步骤式引导 · 软测验收报告

- 日期：2026-08-25（首轮软测）；2026-08-25（修复后回归）
- 被测对象：`backend/app/cooking_core.py`、`backend/app/cooking_tools.py`、`backend/app/ws_handlers/cooking.py`、`backend/app/ws/llm_handler.py`（cooking 工具协议接入）
- 测试环境：Python 3.12.9 / pytest + pytest-asyncio / 真实后端 127.0.0.1:8010（E2E）
- 执行命令：`python -m pytest tests/`（在 `backend/` 目录）
- 修复状态：缺陷 #1~#11 全部修复。默认全量 119 passed / 1 skipped（新代码专项 E2E 需 `E2E_NEW_CODE=1 BACKEND_PORT=8012` 指向新代码实例），含新代码实例 E2E 8/8 全过

---

## 1. 执行结果摘要

| 层级 | 文件 | 通过 | 说明 |
|---|---|---|---|
| 单元 | tests/test_cooking_core.py | 57 | 纯函数：关键词/菜名/份数/倒计时/食材/文本构建/菜谱库匹配（含原 4 条 XFAIL 转正） |
| 单元 | tests/test_cooking_tools.py | 23 | 工具协议：解析/剥离/调度分发（含原 2 条 XFAIL 转正） |
| 集成 | tests/test_cooking_flow.py | 19 | mock ws/runtime，全状态机旅程 |
| 协议链路 | tests/test_cooking_llm_protocol.py | 8 | mock LLM 输出标记 → llm_handler 剥离 → 状态机接管（含缺陷 #7 转正） |
| E2E | tests/test_e2e_cooking.py | 7 | 真实后端 8010，隔离会话驱动 |
| **合计** | | **116** | **全部通过，0 已知缺陷** |

> 3 条无关警告：`timer_manager._cleanup_loop never awaited`（模块级单例清理，非本功能引起）。

## 2. 通过的关键场景（验收标准达成项）

### 2.1 入口路径
- ✅ 路径① 写死关键词：*含「步骤式」→ 直接进入引导*（cooking_enabled=True, COLLECTING_INGREDIENTS）
- ✅ 路径② 设计行为：*「教我做番茄炒蛋」不含「步骤式」→ 返回 False 走普通 LLM 问答*（此为刻意设计，非缺陷，见 §4-B）
- ✅ AI 询问后确认：「可以/好/要/嗯」→ 从最近消息回找菜名进入引导；「不用了」→ 保持问答
- ✅ 长句误判防护：「我要做红烧肉」不会因含「要」被误当确认

### 2.2 状态机主流程（集成 + E2E 双验证）
- ✅ 进入 → 报食材 → 推荐菜谱
- ✅ 说菜名 → 选中菜谱（菜谱库真实匹配：番茄炒蛋/红烧肉/青椒炒肉）→ 等待份数
- ✅ 确认份数（2人份/3人/十份/纯数字）→ COOKING state=1，份量按原份数换算（调料对数收敛 <2.5 倍）
- ✅ 步骤推进：「好了」→ 下一步（completed_steps 记录已完成步骤号）
- ✅ 「跳过」→ 下一步
- ✅ 连续推进至全部完成 → FINISHED 并播报「做好了」
- ✅ 引导中提问（火候/时间）→ cooking_question 上下文（含温度/重量/当前步骤）
- ✅ 换菜：「我要做红烧肉」→ 重置进度 + 选中新菜（份数待确认）；「换一批」→ 基于食材排除当前菜推荐
- ✅ 退出：「退出做饭」→ 二次确认；「是」→ IDLE（含倒计时清理调用）；「继续」→ 取消退出

### 2.3 工具化引导协议（LLM → 预设状态机）
- ✅ `[COOKING:START]`/`[COOKING:SELECT:菜名]`/`[COOKING:CONFIRM_STEP]`/`[COOKING:SKIP_STEP]`/`[COOKING:EXIT]` 大小写不敏感、参数解析
- ✅ 多标记取最后一个；未闭合标记忽略
- ✅ cooking 场景 system prompt 确实注入工具协议（build_chat_prompt cooking_context=True 含 `[COOKING:SELECT]` 说明）
- ✅ mock LLM 输出标记 → llm_handler 逐块+fianl 剥离 → dispatch 调度 → 状态机接管；final 不重复返回，assistant_final 仅含剥离后的极短过渡语
- ✅ 标记在 final 整块出现（最常见形态）→ 剥离 + SELECT 名称正确
- ✅ SELECT 命中本地菜谱 → 交给 control_select_recipe 预设接管
- ✅ SELECT 空参 → 回退 START；未知 action → 返回 False（不误吞 LLM 输出）
- ⚠ 标记跨流式 chunk 拆分时中间块泄漏（缺陷 #7，见下）

### 2.4 健壮性
- ✅ 份数非法输入（“随便做吧”）→ 保持等待并提示「几人份」
- ✅ 本地无菜谱且远程兜底禁用 → 不崩溃，回退引导提示
- ✅ 无菜谱执行 confirm/skip → 提示先选菜；超上限倒计时 → 钳制 24h

## 3. 缺陷修复记录（#1~#7 已全部修复并回归通过）

| # | 位置 | 修复前 | 修复动作 | 验证 |
|---|---|---|---|---|
| 1 | cooking_core `_extract_recipe_name_candidate` | 「红烧肉的做法」→「红烧肉的」 | 后缀按长度降序匹配（"的做法"优先于"做法"） | `test_suffix_zuofa_expected` ✓ |
| 2 | cooking_core `parse_timer_request` | 「定个闹钟五分钟」→ None | 新增 `_extract_cn_value`：剥离触发词后取中文数字；`TIMER_KEYWORDS` 补「闹钟/定时器」 | `test_cn_number_with_naozhong` ✓ |
| 3 | 同上 | 「定个5分钟的煮鸡蛋闹钟」→ None | 触发词表补齐，乱序表述可命中 | `test_naozhong_word_order` ✓ |
| 4 | 同上 | 「倒计时半小时」→ None | `_extract_cn_value` 支持「半」特判与触发词脱壳 | `test_cn_half_hour_with_prefix` ✓ |
| 5 | cooking_tools `strip_cooking_tools` | `strip(None)` → TypeError | 增加 `if not text: return ""` | `test_none_input` ✓ |
| 6 | cooking_tools `dispatch_cooking_tool` | SELECT 未命中 → 调用不存在的 `cooking_handlers.send_cooking_text_with_tts` → AttributeError 被静默吞 | 改用 `cooking_core.send_cooking_text_with_tts`（模块属性访问） | `test_dispatch_select_missing_recipe` / `..._no_crash` ✓ |
| 7 | ws/llm_handler 流式剥离 | 标记跨 chunk 拆分时半截标记泄漏到 assistant_delta | 跨 chunk 缓冲：`cooking_mark_pending` 滞留未闭合 `[COOKING...`，闭合后剥离放行；`buffer` 仍保留原始 delta 供 final 解析 | `test_streaming_marker_stripped_per_chunk` ✓（delta 无泄漏且「请稍等马上开始」完整送达） |

> 回归说明：修复 #6 时曾误改 buffer 仅存剥离文本导致 final 丢标记、dispatch 不触发（已自测发现并恢复，`buffer` 保留原始 delta）。

| 8 | cooking_core `_accepts_step_guide` + ws_handlers/cooking 确认路径 | AI 询问后用户回「需要」→ 未命中确认词 → 落 LLM → 输出 `[COOKING:START]` → **重新询问菜名**（菜名已在上下文中） | ①确认词表补「需要/需要你/需要啊」；②确认分支新增 `_resolve_recipe_from_recent_text`：本地精确 → 本地模糊 → 远程 LLM 生成，拿到菜谱直接进准备流程，不再重问 | `test_needs_confirmation` / `test_accept_needs_reuses_remote_recipe` ✓（断言不再输出"有什么想做的"） |
| 9 | cooking_core `_wants_step_guide` | 「一步一步教我做」等**不带「步骤式」**的同义表达不命中写死入口 → 落入 LLM 等待输出标记（实测表现为"卡住/输出 cooking 标记/重问"） | ①引导词表扩展「一步一步/一步步/手把手」；②拒绝词优先防误判；③wants_step_guide 分支同样走 `_resolve_recipe_from_recent_text` 直接拿菜谱 | `test_synonym_stepwise_matches` / `test_decline_guide_word_priority` ✓；真实链路探针：`一步一步教我做番茄炒鸡蛋` → AWAITING_SERVINGS（4步菜谱），无泄漏不重问 |
| 10 | cooking_core `_build_recipe_lookup_prompt` + 模板 | 远程兜底**从未成功**：①模板占位 `{dish_name}` 未传 → KeyError 被静默吞；②模板还有字面 `{菜品名称}` → 二次 KeyError；③模板要求 markdown 而解析器只认 JSON → 结构性失败；④菜名候选不剥「教我做/一步一步教我做」引导词 | ①调用补 `dish_name`；②模板字面花括号改占位；③模板改为 JSON 输出规范（与 `_normalize_generated_recipe` 对齐）；④前缀表纳入「教我做/一步一步教我做/手把手教我做」；⑤`_collect_llm_text` 超时 15s→25s | `test_format_no_keyerror` ✓；真实链路解析出《番茄炒鸡蛋》4步菜谱 ✓ |
| 11 | 测试基建 | E2E 硬编码端口 8010，无法针对新代码实例验证 | E2E 支持 `BACKEND_PORT` 环境变量；新代码专项用例用 `E2E_NEW_CODE=1` 门槛，在 8012 新实例上 8/8 通过 | `BACKEND_PORT=8012 E2E_NEW_CODE=1` 全绿 ✓ |

## 4. 低严重观察（记录不修复）

- **A.** 份数换算丢空格：「鸡蛋 3 个」×2 →「鸡蛋 6个」（正则吞掉数字与单位间空格，展示格式瑕疵）。
- **B.** 菜名提取前缀表缺「教我做/带我/教」：真实高频说法「教我做番茄炒蛋」不会本地匹配（需先进入 cooking 状态后经 LLM/远程兜底）。与入口路径② 设计叠加后，**首次说「教我做XX」永远走 LLM 问答**，这正是用户此前困惑的来源——详见对话结论，是否放行由产品决策。
- **C.** `build_preparation_text`：「先准备这些原料和工具」与「份量说明」拼接无分隔符。
- **D.** 集成/E2E 观测到 `parse_ingredient_text("2人份")` 会把「2人份」暂列食材候选，但因状态机在 COOKING 态跳过食材分支，无实际影响。

## 5. 覆盖边界/未覆盖

- 未测：真实 LLM 输出标记的端到端（依赖外部 key/稳定性）；TTS 语音外呼（E2E 已用 hello 覆盖为 browser 防外呼）；多会话并发；唤醒词/语音侧。
- E2E 使用隔离 `session_id=qa_e2e_*`，未污染正式会话与运行数据。

## 6. 结论

**验收通过（含修复后回归）**：11 项缺陷全部修复。真实后端（8012 新代码实例）实测用户场景「一步一步教我做番茄炒鸡蛋」→ 直接进入准备流程（远程生成 4 步菜谱，问份数），**不再卡顿、不输出 cooking 标记、不重新询问菜名**。**注意：生产后端 8010 进程仍为旧代码（权限受限无法代停），需在您的终端关闭旧后端后重启 8010 使修复生效。**