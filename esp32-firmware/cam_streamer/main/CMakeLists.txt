idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES esp32-camera esp_http_server esp_wifi esp_netif nvs_flash esp_timer esp_driver_gpio esp_driver_ledc
)
