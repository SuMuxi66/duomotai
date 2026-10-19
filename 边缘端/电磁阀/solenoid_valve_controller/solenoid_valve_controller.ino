// 本板仅做电磁阀控制（ON/OFF/STATUS/PULSE），禁止集成称重/ADC 读取
// ——重量输出行会导致后端秤通道误连本板（2026-09-07 事故）
#include <Arduino.h>
#include "solenoid_valve.h"

#define RELAY_PIN 7
#define BAUD_RATE 9600

#define CMD_ON "ON"
#define CMD_OFF "OFF"
#define CMD_STATUS "STATUS"
#define CMD_PULSE "PULSE"

SolenoidValve valve(RELAY_PIN);

String serialBuffer = "";
const size_t BUFFER_SIZE = 32;

void setup()
{
  Serial.begin(BAUD_RATE);
  valve.begin();
  Serial.println("READY");
}

void loop()
{
  valve.update();
  while (Serial.available() > 0)
  {
    char c = Serial.read();
    if (c == '\r' || c == '\n')
    {
      if (serialBuffer.length() > 0)
      {
        processCommand(serialBuffer);
        serialBuffer = "";
      }
    }
    else
    {
      if (serialBuffer.length() < BUFFER_SIZE)
      {
        serialBuffer += c;
      }
    }
  }
}

void processCommand(const String &cmd)
{
  if (cmd.startsWith(CMD_PULSE))
  {
    int commaIndex = cmd.indexOf(',');
    if (commaIndex > 0)
    {
      String durationStr = cmd.substring(commaIndex + 1);
      unsigned int duration = durationStr.toInt();
      if (duration > 0 && duration <= 3600000)
      {
        valve.setPulse(duration);
        Serial.println("OK");
      }
      else
      {
        Serial.println("ERR");
      }
    }
    else
    {
      Serial.println("ERR");
    }
  }
  else if (cmd == CMD_ON)
  {
    valve.open();
    Serial.println("OK");
  }
  else if (cmd == CMD_OFF)
  {
    valve.close();
    Serial.println("OK");
  }
  else if (cmd == CMD_STATUS)
  {
    Serial.println(valve.getStateString());
  }
  else
  {
    Serial.println("ERR");
  }
}
