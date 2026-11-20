# Prueba sencilla USB HID: teclado + mouse + controles multimedia.
# Conecta el USB OTG del ESP32-S3 al PC o Android después de ejecutar.

import time
import usb_hid
from usb.device.keyboard import KeyCode as KC

print("Configurando USB HID...")
usb_hid.init()
print("Conecta el USB OTG al equipo. Esperando enumeracion...")

if not usb_hid.wait_ready(15000):
    raise RuntimeError("El host no abrio las interfaces HID")

print("HID listo")
usb_hid.send_text("hola desde esp32 s3", 35)

# Atajo Windows/Linux: abrir Ejecutar (Win+R). En Android,
# las teclas Meta y los atajos dependen del sistema y del teclado.
usb_hid.keyboard.hotkey(KC.LEFT_UI, KC.R)

# Controles Consumer HID: volumen y multimedia.
usb_hid.volume_up()
usb_hid.volume_down()
usb_hid.mute()
usb_hid.play_pause()
usb_hid.next_track()
usb_hid.previous_track()

# Movimiento relativo y clic izquierdo.
usb_hid.move(40, 0)
usb_hid.move(0, 30)
usb_hid.click("left")

print("Prueba terminada")
