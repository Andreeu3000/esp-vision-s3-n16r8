set(IDF_TARGET esp32s3)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.ble
    boards/ESP32_S3_N16R8/sdkconfig.s3_n16r8
    boards/ESP32_S3_N16R8/sdkconfig.board
)

set(MICROPY_PY_BTREE OFF)
