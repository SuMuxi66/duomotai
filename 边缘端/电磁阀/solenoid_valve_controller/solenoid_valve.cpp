#include "solenoid_valve.h"

SolenoidValve::SolenoidValve(uint8_t controlPin)
{
  pin = controlPin;
  isOpen = false;
  pulseActive = false;
  pulseEndTime = 0;
}

void SolenoidValve::begin()
{
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);
}

bool SolenoidValve::open()
{
  digitalWrite(pin, HIGH);
  isOpen = true;
  pulseActive = false;
  return true;
}

bool SolenoidValve::close()
{
  digitalWrite(pin, LOW);
  isOpen = false;
  pulseActive = false;
  return true;
}

bool SolenoidValve::toggle()
{
  if (isOpen)
  {
    close();
  }
  else
  {
    open();
  }
  return isOpen;
}

bool SolenoidValve::setPulse(unsigned int durationMs)
{
  open();
  pulseActive = true;
  pulseEndTime = millis() + durationMs;
  return true;
}

void SolenoidValve::update()
{
  if (pulseActive && millis() >= pulseEndTime)
  {
    close();
  }
}

bool SolenoidValve::getState()
{
  return isOpen;
}

String SolenoidValve::getStateString()
{
  return isOpen ? "ON" : "OFF";
}
