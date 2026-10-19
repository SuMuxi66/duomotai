#include "HX711.h"

uint32_t HX711_Read(void)
{
    uint32_t data = 0;
    uint8_t i;

    while (HAL_GPIO_ReadPin(HX711_DT_PORT, HX711_DT_PIN) == 1)
        ;

    for (i = 0; i < 24; i++)
    {
        HAL_GPIO_WritePin(HX711_SCK_PORT, HX711_SCK_PIN, GPIO_PIN_SET);
        data <<= 1;

        if (HAL_GPIO_ReadPin(HX711_DT_PORT, HX711_DT_PIN))
            data++;

        HAL_GPIO_WritePin(HX711_SCK_PORT, HX711_SCK_PIN, GPIO_PIN_RESET);
    }

    HAL_GPIO_WritePin(HX711_SCK_PORT, HX711_SCK_PIN, GPIO_PIN_SET);
    HAL_GPIO_WritePin(HX711_SCK_PORT, HX711_SCK_PIN, GPIO_PIN_RESET);

    return data;
}

float HX711_Get_Weight(void)
{
    uint32_t ad = HX711_Read();
    float weight = (ad - 8388608) / 100.0f;
    return weight;
}
