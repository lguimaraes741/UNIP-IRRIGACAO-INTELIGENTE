idf_component_register(SRCS "main.cpp" "hardware.cpp" INCLUDE_DIRS "../core" REQUIRES nvs_flash mqtt esp_event esp_netif esp_wifi esp_timer json esp_driver_gpio esp_driver_i2c esp_adc)
