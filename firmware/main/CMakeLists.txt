idf_component_register(
    SRCS "main.c" "oled.c" "face_oled.c" "audio.c" "net.c" "usb_setup.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_i2s esp_driver_i2c esp_driver_gpio esp_driver_tsens
                  esp_wifi esp_netif esp_event nvs_flash esp_http_server json lwip esp_timer esp_pm
                  esp_driver_usb_serial_jtag)

target_compile_options(${COMPONENT_LIB} PRIVATE -O2 -ffast-math)
