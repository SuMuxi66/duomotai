#ifndef __HX711_H
#define __HX711_H

#include "main.h"

#define HX711_DT_PIN GPIO_PIN_0
#define HX711_DT_PORT GPIOA
#define HX711_SCK_PIN GPIO_PIN_1
#define HX711_SCK_PORT GPIOA

uint32_t HX711_Read(void);
float HX711_Get_Weight(void);

#endif
