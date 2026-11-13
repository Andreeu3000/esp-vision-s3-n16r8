# ESP32-S3 N16R8

Custom ESP-VISION board configuration for ESP32-S3 hardware.

## Hardware

- ESP32-S3
- 16 MiB Quad SPI Flash
- 8 MiB Octal SPI PSRAM
- CPU frequency: 240 MHz

## Enabled features

- Wi-Fi
- USB Serial/JTAG transport
- UART REPL fallback
- MicroPython
- IMLIB image processing
- Local image file I/O
- FFat filesystem mounted at `/flash`

## Disabled peripherals

- Camera
- Display
- Touch controller
- MicroSD card
- USB CDC/MSC
- TCP transport

Images are processed from files stored in the flash filesystem.
