"""
USB Host serial -> GRBL CNC example for ESP32-S3 N16R8.
Keep the physical emergency stop reachable. Test with spindle/laser disabled.
X5/X-5 are small relative moves, but still move real hardware.
"""
import time
import usb

BAUDRATE = 115200
CONNECT_TIMEOUT_MS = 15000
RESPONSE_TIMEOUT_MS = 5000

def read_line(port, timeout_ms=RESPONSE_TIMEOUT_MS):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    line = bytearray()
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        chunk = port.read(1, 100)
        if chunk:
            if chunk == b"\n":
                return bytes(line).rstrip(b"\r").decode()
            line.extend(chunk)
    raise OSError("timeout esperando respuesta GRBL: " + repr(bytes(line)))

def send_command(port, command):
    print(">>", command)
    port.write(command.encode() + b"\r")
    while True:
        response = read_line(port)
        if response:
            print("<<", response)
        if response == "ok":
            return True
        if response.startswith("error:") or response.startswith("ALARM:"):
            raise RuntimeError("GRBL rechazó el comando: " + response)

try:
    usb.device.disconnect()
except OSError:
    pass
usb.host.connect()
cnc = usb.host.serial(baudrate=BAUDRATE, timeout_ms=CONNECT_TIMEOUT_MS)

try:
    time.sleep_ms(2000)
    banner = cnc.read(512, 200)
    if banner:
        print(banner.decode())
    send_command(cnc, "$X")
    send_command(cnc, "G21")
    send_command(cnc, "G91")
    send_command(cnc, "X5")
    send_command(cnc, "X-5")
    print("Prueba GRBL terminada.")
finally:
    cnc.close()
    usb.host.disconnect()
