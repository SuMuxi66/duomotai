// =====================================================================
// 电磁阀最小固件（方案A：单字符 O/F + ACK，平时零输出）
// =====================================================================
// 背景：现役"下料机一体板"固件持续以 ~22行/秒 刷称重数据行（Raw: ... | Real Weight: g），
// 主机侧两条命令之间从不读取串口 → 缓冲塞满 → 板卡 TX 背压 → loop() 卡死在
// Serial 发送里 → 收到 'O' 不执行、不回 ACK、不开阀（2026-09-19 全天 SILENT 根因）。
// 本固件移除一切主动输出：平时串口完全静默，只对指令回 ACK，整条故障链不存在。
//
// 协议（与 Pi 端 opi_sensors_client.py / 后端 valve.py 逐字节兼容，两端零改动）：
//   裸 'O'        → 开阀，回 "OK ON\r\n"     （Pi 客户端单字符路径，无行尾）
//   裸 'F'        → 关阀，回 "OK OFF\r\n"    （客户端阀口识别也靠它：'F'→OK OFF）
//   "O"+回车      → 开阀（串口监视器手测）
//   "ON\r\n"      → 开阀，回 "OK ON\r\n"     （兼容全词协议）
//   "OFF\r\n"     → 关阀，回 "OK OFF\r\n"
//   "STATUS\r\n"  → 回 "ON\r\n" / "OFF\r\n"
//   上电          → 继电器释放（关阀），打印一行 "READY"
//   其余输入      → 回 "ERR"
// 串口参数：9600 8N1（与两端现有配置一致，勿改）
//
// ⚠️ 极性约定：默认 HIGH=开阀（与仓库 solenoid_valve.cpp / valve_control.py 实测基准一致）。
//    烧录后先用串口监视器验证：发 O 阀应开。若行为相反（上电阀就吸合/发O反关），
//    把 RELAY_ACTIVE_HIGH 改成 false 重烧，并立即断电检查（极性反=上电即通电，防烧）。
// ⚠️ 两块阀板（电磁阀1、电磁阀2）都要烧同一份。
// =====================================================================

#define RELAY_PIN 7             // 继电器信号脚（与仓库实测基准一致，板子不同再改）
#define RELAY_ACTIVE_HIGH true  // true: HIGH=开阀；false: LOW=开阀
#define BAUD_RATE 9600
#define SINGLE_CHAR_TIMEOUT_MS 50  // 裸单字符判定：停止接收 50ms 后按整行处理
#define BUFFER_SIZE 32

bool valveOpen = false;
String rxBuffer = "";
unsigned long lastCharMs = 0;

void applyRelay()
{
  bool level = RELAY_ACTIVE_HIGH ? valveOpen : !valveOpen;
  digitalWrite(RELAY_PIN, level ? HIGH : LOW);
}

void openValve()
{
  valveOpen = true;
  applyRelay();
  Serial.println("OK ON");
}

void closeValve()
{
  valveOpen = false;
  applyRelay();
  Serial.println("OK OFF");
}

void processLine(const String &line)
{
  if (line == "O" || line == "ON")
  {
    openValve();
    return;
  }
  if (line == "F" || line == "OFF")
  {
    closeValve();
    return;
  }
  if (line == "STATUS")
  {
    Serial.println(valveOpen ? "ON" : "OFF");
    return;
  }
  Serial.println("ERR");
}

void setup()
{
  pinMode(RELAY_PIN, OUTPUT);
  valveOpen = false;
  applyRelay(); // 上电即关阀（继电器释放态）
  Serial.begin(BAUD_RATE);
  Serial.println("READY");
}

void loop()
{
  while (Serial.available() > 0)
  {
    char c = (char)Serial.read();
    if (c == '\r' || c == '\n')
    {
      if (rxBuffer.length() > 0)
      {
        processLine(rxBuffer);
        rxBuffer = "";
      }
    }
    else
    {
      if (rxBuffer.length() < BUFFER_SIZE)
      {
        rxBuffer += c;
      }
    }
    lastCharMs = millis();
  }
  // 裸 'O'/'F'（Pi 客户端无行尾单字符）：字符停 50ms 无后续即按整行执行。
  // 带行尾的指令在上一段立即执行，不走这里。
  if (rxBuffer.length() > 0 && millis() - lastCharMs > SINGLE_CHAR_TIMEOUT_MS)
  {
    processLine(rxBuffer);
    rxBuffer = "";
  }
}
