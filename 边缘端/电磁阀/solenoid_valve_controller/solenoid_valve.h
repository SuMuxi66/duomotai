#ifndef SOLENOID_VALVE_H
#define SOLENOID_VALVE_H

#include <Arduino.h>

class SolenoidValve {
private:
    uint8_t pin;
    bool isOpen;
    unsigned long pulseEndTime;
    bool pulseActive;

public:
    SolenoidValve(uint8_t controlPin);
    void begin();
    bool open();
    bool close();
    bool toggle();
    bool setPulse(unsigned int durationMs);
    void update();
    bool getState();
    String getStateString();
};

#endif
