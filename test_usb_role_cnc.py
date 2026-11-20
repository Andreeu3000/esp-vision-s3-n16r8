import time
import usb
from usb.device.keyboard import Keyboard
from usb.device.mouse import Mouse
from usb.host.serial import Serial


def run_demo():
    # 1. MODO USB DEVICE: HID
    print("Iniciando USB HID...")
    usb.device.connect()

    keyboard = Keyboard()
    mouse = Mouse()

    # Activa una vez registradas las interfaces HID
    usb.device.connect()
    time.sleep(1)

    # 2. CAMBIAR DE USB DEVICE A USB HOST
    print("Cambiando a USB Host...")
    keyboard = None
    mouse = None
    usb.device.disconnect()
    time.sleep_ms(200)

    usb.host.connect()
    print("USB Host activo:", usb.host.info())

    # 3. CONECTAR CON CONTROLADOR CNC (GRBL)
    while usb.host.device_count() == 0:
        time.sleep_ms(100)

    cnc = Serial()
    cnc.open(baudrate=115200, timeout=1000)

    def cnc_send(command):
        print("Enviando:", command)
        cnc.write((command + "\n").encode("utf-8"))
        response = cnc.readline()
        print("Respuesta:", response)
        return response

    cnc_send("?")
    cnc_send("$X")
    cnc_send("G21")
    cnc_send("G91")
    cnc_send("G1 X5 F100")

    # 4. DESCONECTAR USB HOST Y VOLVER A USB HID
    cnc.close()
    usb.host.disconnect()
    print("USB Host desconectado")

    time.sleep_ms(200)
    usb.device.connect()
    print("USB HID nuevamente activo:", usb.device.core.get().active())
