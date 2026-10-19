#!/bin/bash
# start_edge_services.sh — 香橙派端侧服务一键拉起（幂等，可重复执行）
# 用途：断电重启 / 手动停过后，把语音链路相关服务全部拉起并体检。
# 部署位置：/home/orangepi/start_edge_services.sh（由仓库根 香橙派一件启动.py 自动上传执行）
# 手动用法：bash /home/orangepi/start_edge_services.sh
# 说明：orangepi 无免密 sudo，脚本用 sudo -S 喂登录密码；docker 命令免 sudo（orangepi 在 docker 组）。

PASS="orangepi"
COMPOSE_DIR="/home/orangepi/rokid-edge/docker"
UNITS="opi-camera opi-ir opi-sensors tts-sambert"
PORTS="8767 8768 8769"   # 8767=KWS(容器) 8768=ASR(容器) 8769=TTS
FAIL=0

step() { echo; echo "==== $1 ===="; }

step "1/4 systemd 服务（enable --now：没起的拉起，已在跑的不动）"
for u in $UNITS; do
  if echo "$PASS" | sudo -S -p '' systemctl enable --now "$u" >/dev/null 2>&1; then
    echo "  [OK] $u ($(systemctl is-active "$u"))"
  else
    echo "  [FAIL] $u（enable/start 失败）"
    FAIL=1
  fi
done

step "2/4 Docker 容器（rokid-kws 唤醒词 / rokid-asr 流式ASR）"
if ! docker info >/dev/null 2>&1; then
  echo "  [FAIL] docker 守护进程不可用，先手动: systemctl enable --now docker"
  FAIL=1
else
  if ( cd "$COMPOSE_DIR" && docker compose up -d ) 2>&1 | sed 's/^/  /'; then
    :
  else
    echo "  [FAIL] docker compose up 失败"
    FAIL=1
  fi
fi

step "3/4 等待端口监听（最多 60s）"
for i in $(seq 1 30); do
  OK=1
  for p in $PORTS; do ss -tln 2>/dev/null | grep -q ":$p " || OK=0; done
  [ "$OK" = 1 ] && break
  sleep 2
done
for p in $PORTS; do
  if ss -tln 2>/dev/null | grep -q ":$p "; then
    echo "  [OK] 端口 $p 监听中"
  else
    echo "  [FAIL] 端口 $p 未监听"
    FAIL=1
  fi
done

step "4/4 状态总览"
for u in $UNITS; do
  printf "  %-14s %s\n" "$u" "$(systemctl is-active "$u")"
done
echo "  （opi-camera 显示 activating = 摄像头未插，退避重试属正常，插上 10s 内自动拾取）"
docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep '^rokid' || { echo "  [FAIL] rokid 容器未运行"; FAIL=1; }

echo
if [ "$FAIL" = 0 ]; then
  echo "RESULT: ✅ 端侧服务全部就绪"
else
  echo "RESULT: ❌ 有服务未就绪，按上面 [FAIL] 逐项排查"
fi
exit $FAIL
