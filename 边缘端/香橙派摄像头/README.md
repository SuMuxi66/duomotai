# 香橙派边缘采集端（摄像头 + 红外测温）

USB 摄像头与 MLX90640 红外热像模组从 Windows 后端机改接香橙派 RK3588：采集客户端常驻
抓帧/读帧 → WebSocket 推流到后端 → 后端在数据到达时接管对应数据源 →
前端取流 / 接口 / 视觉分析 / 油温盯守**零改动**跟随远程数据。

- 摄像头：`opi_camera_client.py` → `/ws/opi-cam` → `remote_camera.remote_feed` 接管 `usb_cam` 视图
- 红外：`opi_ir_client.py` → `/ws/opi-ir` → `remote_ir.process_ir_frame` 复用 temperature.reader 入库
  （协议：115200bps，发 `A5 35 02 DC` 起流，帧头 `5A5A` 定长 1544 字节，768 点小端 u16÷100 + 传感器温度）

## 文件

- `opi_camera_client.py` — 摄像头采集客户端（部署在香橙派 `/home/orangepi/camera/`）
- `opi-camera.service` — 摄像头 systemd 单元（`systemctl link` 到家目录文件，改完 `daemon-reload`）
- `opi_ir_client.py` — 红外测温采集客户端（同目录部署）
- `opi-ir.service` — 红外 systemd 单元（传感器未插入时退避重试，插入即用）

## 当前部署状态（2026-09-06）

- **三后端推送（2026-09-12）**：`OPI_CAM_SERVER` = .96/.252/.236 三路，`OPI_IR_SERVER` = .96/.252/.236 三路（opi_ir_client 已改多目标，与 cam 同构）。.96/.252 实测收流正常；.236 是旧版后端、无 /ws/opi-cam 与 /ws/opi-ir 路由（uvicorn 对不存在的 WS 路由回 HTTP 403），Pi 侧退避重试无副作用，那台后端升级代码后自动接上。
- 香橙派 `192.168.1.127`（主机名 orangepi5max），服务 `opi-camera` 已 `enabled + active`，
  开机自启、断流 3s 自动拉起；摄像头未插入时处于 ~10s 退避重试循环，**插上即用（即插即用）**。
- 本机 SSH 免密登录：`ssh -i C:\Users\Administrator\.ssh\id_ed25519_opi orangepi@192.168.1.127`
  （公钥已装入对方 authorized_keys；旧 `id_ed25519` 格式损坏勿用）。
- 香橙派侧依赖已就绪：python3.10 + opencv-python-headless 4.11 + websockets 12（免安装）。
- 香橙派 → 后端 8010 连通性已验证（Windows 防火墙无需改动）。
- **WiFi 已接入（2026-09-07）**：SSID `sumuxi`（WPA2，路由器 192.168.1.1），wlan0 =
  `192.168.1.248`，NetworkManager 配置 autoconnect=yes 开机自连。有线 enP3p49s0(192.168.1.127)
  metric 100 优先、WiFi metric 600 兜底——**拔掉网线后香橙派 IP 变为 192.168.1.248**，
  到后端 8010 的通路在 WiFi 上实测可用（绑定 wlan0 源地址 curl /health 通过）。
- **断电重启实测通过（2026-09-07，拔网线纯 WiFi 起机）**：下线 ~50s 后以 wlan0 自动重连 sumuxi；
  `docker.service` 已补 `systemctl enable`（原先 disabled 靠 socket 激活，重启后 rokid-asr/kws
  不会自动拉起），重启后容器 26s 内全部自回；opi-camera active 自动恢复重试。
  远程连接复用工具已沉淀为 skill `orangepi-ssh`（paramiko 密钥登录，双地址自动回退）。

## 软件链路验证记录（合成帧/合成数据 E2E）

- **摄像头（2026-09-06）**：香橙派向 `/ws/opi-cam` 推 80+300 帧合成图：后端 `/usb-camera/status` 变
  `source:"remote"`、本地捕获自动挂起（device_index:-1）；前端 5179 实时显示合成帧画面；
  断流 6s 后自动回落 `source:"local"`、本地设备重新打开。双向切换均验证通过。
- **红外（2026-09-07）**：香橙派向 `/ws/opi-ir` 推 30 帧合成 MLX90640 帧：`/temperature` 实时返回
  合成值（target 27.0 / max 32.0 / min 26.0 / sensor 28.5，timestamp 刷新），"现在温度"快答与
  oil_watch 盯守自动跟随（数据过期由下游 timestamp 新鲜度门判定，行为同温重二合一板卡注入路径）。

## 剩余动作

**把 USB 摄像头、MLX90640 红外模组从 Windows 机拔下、插到香橙派上**即可，两个服务 10s 内
自动拾取开始推流（opi-ir 探测口诀同后端 find_port：发 `A5 35 02 DC` 看是否回 `5A5A` 帧）。

## 日常运维

```bash
# 香橙派侧（SSH 免密）
journalctl -u opi-camera -n 30 --no-pager -o cat   # 看日志
sudo systemctl restart opi-camera                   # 重启

# Windows 侧
curl http://127.0.0.1:8010/usb-camera/status        # source 应为 remote；remote.fps/age_seconds 是健康指标
```

**端侧一键拉起（2026-09-14 新增）**：仓库根 `python start_edge.py`——自动上传 `start_edge_services.sh`
到 Pi 并执行，幂等拉起 systemd 四服务（opi-camera / opi-ir / opi-sensors / tts-sambert，enable --now）
+ Docker 两容器（rokid-kws/rokid-asr，compose up -d），最后体检端口 8767/8768/8769。
`python start_edge.py status` 只看状态不拉起。适用于断电重启后个别服务没起来、或容器被手动 stop 过
  （unless-stopped 对手动 stop 不生效）的场景；全部正常时跑一遍是无扰动 no-op。
  凭据走 `../backend/香橙派远程.py`（RKI_USER/RKI_PASS，缺省 orangepi/orangepi；旧机器的 id_ed25519_opi 免密钥匙在当前 27286 账号上不存在）。

## 调参（改 /home/orangepi/camera/opi-camera.service 的 Environment，改完 `sudo systemctl daemon-reload && sudo systemctl restart opi-camera`）

| 变量 | 默认 | 说明 |
|---|---|---|
| OPI_CAM_SERVER | 无（单元文件里已写） | 后端 WS 地址 |
| OPI_CAM_WIDTH / HEIGHT | 1280 / 720 | 目标分辨率（协商失败逐档回退 1080p→720p→原生） |
| OPI_CAM_FPS | 30（service 文件，2026-09-11 有线 2K 档） | 推流帧率上限 |
| OPI_CAM_QUALITY | 75（service 文件，2026-09-11 有线 2K 档） | JPEG 质量 |
| OPI_CAM_ROTATION | 缺省=轮询后端 | 画面旋转（0/90/180/270 编码前转原始帧）；显式设置则固定不走轮询 |
| OPI_CAM_DEVICE | 自动扫 /dev/video0..7 | 指定设备 |

带宽/稳定性重要实测（2026-09-07）：该 WiFi 链路（5GHz、-65dBm、Broadcom dhd 驱动、
**PHY 仅 34Mbit/s**）有效吞吐 ~10Mbit 级且高负载下周期性拥塞——2K(20Mbps)、1080p(9Mbps)
均会在 ~20s 内压满管道（服务端 WS ping 超时判死、SSH 同步饿死、Pi 甚至短暂掉网），
**720p@8fps/q60（≈5-6Mbps）是 WiFi 环境的稳定档**。要上 2K 画质请插回有线网。

**有线 2K 档（2026-09-11 实测定档）**：2560x1440/q75，实测稳定 **13-14fps**（RK3588 单核读帧+编码 ~73ms/帧，带宽仅 1.5MB/s 不是瓶颈；摄像头本身支持 1440p@32）。旋转已在 Pi 端编码前完成（轮询后端 CAMERA_ROTATION，前端"旋转画面"按钮 ~3s 生效），后端对远程帧纯转发。帧率-分辨率实测换算：720p≈30fps、1080p≈20fps、2K≈13.6fps，改 service 里 WIDTH/HEIGHT 即切。注意：日志里 "平均 Nfps" 已修正为按帧计数（此前按目标数双倍误报）；**不要做预读流水线**——两路 OpenCV 重线程并发 GIL 争用实测掉到 7fps。真实生效 unit 在 Pi 的 **/etc/systemd/system/opi-camera.service**（非家目录软链，README 旧说法作废），本仓库 opi-camera.service 是其镜像。

**旋转归属（2026-09-11 终版设计，用户拍板"各侧旋转互不影响"）**：Pi 固定 `OPI_CAM_ROTATION=0` 推原始画面；每台后端自己的 `CAMERA_ROTATION` 只管本机——`/usb-camera/latest` 出帧与视觉链路（vision_handler，0.2fps）转正供 AI，前端按 /config 同角度做 CSS 显示。MJPEG 流原始直通。因此 .96 按按钮只影响 .96，.252 只影响 .252。注意 .252 后端仍是旧代码（无 rotation 字段/无转正逻辑），需同步 2026-09-11 的 backend 改动，否则那台机器的 AI 看到的是倒的。另：有线网卡 enP3p49s0 现在 DHCP 拿到的就是 192.168.1.248（wlan0 无 IPv4），SSH/推流均走有线。
另外 RK3588 供电不足会引发 USB 设备随机掉线（dmesg 可见 `usb x-x: USB disconnect`/
`Failed to resubmit video URB (-19)`）与 WiFi 掉网——**用原装 5V≥4A 电源、避免劣质
USB hub**；设备从 USB 消失后必须物理重插（远程无法恢复），重插后服务 10s 内自动拾取。

客户端自愈机制：进程内连续 3 次找不到设备 → `os._exit(1)` 交由 systemd 拉起干净实例
（防僵死读帧线程一直占用 V4L2/tty 设备导致服务永远无法重开设备，2026-09-07 实测踩坑）。

## 后端侧改动（本仓库）

- `backend/app/remote_camera.py`（新增）：帧仓 RemoteCameraFeed + WS 处理器；
  保鲜期默认 6s（env `REMOTE_CAMERA_FRESH_SECS`），帧需 ≥1KB 且带 JPEG SOI。
- `backend/app/usb_camera.py`：公开视图（latest_bytes/b64/timestamp/is_connected/status）
  远程优先；捕获线程检测到远程保鲜即挂起本地设备、断流自动恢复探测；status 增加
  `source`（remote/local/none）与 `remote` 统计。
- `backend/app/main.py`：新增 `ws://…:8010/ws/opi-cam` 端点。
- 单测：`backend/tests/test_remote_camera.py`。
