# USB HID convenience layer for ESP-VISION ESP32-S3.
#
# Usage:
#   import usb_hid
#   usb_hid.init()
#   usb_hid.send_text("hola")
#
# The keyboard, mouse and Consumer Control interfaces are configured together.
# This module does not alter USB role switching; use usb.device/usb.host for it.

import time
import usb.device
from usb.device.hid import HIDInterface
from usb.device.keyboard import KeyboardInterface, KeyCode as KC
from usb.device.mouse import MouseInterface


# Consumer Page (0x0C), Consumer Control (0x01), 16-bit usage array.
_CONSUMER_REPORT_DESC = (
    b"\x05\x0c"      # Usage Page (Consumer)
    b"\x09\x01"      # Usage (Consumer Control)
    b"\xa1\x01"      # Collection (Application)
    b"\x15\x00"      # Logical Minimum (0)
    b"\x26\xff\x03"  # Logical Maximum (0x03ff)
    b"\x19\x00"      # Usage Minimum (0)
    b"\x2a\xff\x03"  # Usage Maximum (0x03ff)
    b"\x75\x10"      # Report Size (16)
    b"\x95\x01"      # Report Count (1)
    b"\x81\x00"      # Input (Data, Array, Absolute)
    b"\xc0"
)


class Consumer:
    VOLUME_UP = 0x00E9
    VOLUME_DOWN = 0x00EA
    MUTE = 0x00E2
    PLAY_PAUSE = 0x00CD
    NEXT_TRACK = 0x00B5
    PREVIOUS_TRACK = 0x00B6
    STOP = 0x00B7
    AC_HOME = 0x0223
    AC_BACK = 0x0224
    AC_FORWARD = 0x0225
    AC_REFRESH = 0x0227
    AC_SEARCH = 0x0221
    AC_BOOKMARKS = 0x022A

    def __init__(self):
        self._interface = HIDInterface(
            _CONSUMER_REPORT_DESC,
            interface_str="MicroPython Consumer Control",
        )
        self._report = bytearray(2)

    def send_usage(self, usage, timeout_ms=100):
        if not 0 <= usage <= 0x03FF:
            raise ValueError("usage")
        # The HID patch adds bounded waits and disconnect-aware submission.
        self._interface._wait_ready(timeout_ms)
        self._report[0] = usage & 0xFF
        self._report[1] = usage >> 8
        self._interface._submit_report(self._report)
        return True

    def release(self, timeout_ms=100):
        self._interface._wait_ready(timeout_ms)
        self._report[0] = 0
        self._report[1] = 0
        self._interface._submit_report(self._report)
        return True

    def tap(self, usage, hold_ms=30, timeout_ms=100):
        self.send_usage(usage, timeout_ms)
        time.sleep_ms(hold_ms)
        return self.release(timeout_ms)

    def volume_up(self):
        return self.tap(self.VOLUME_UP)

    def volume_down(self):
        return self.tap(self.VOLUME_DOWN)

    def mute(self):
        return self.tap(self.MUTE)

    def play_pause(self):
        return self.tap(self.PLAY_PAUSE)

    def next_track(self):
        return self.tap(self.NEXT_TRACK)

    def previous_track(self):
        return self.tap(self.PREVIOUS_TRACK)

    def stop(self):
        return self.tap(self.STOP)

    def home(self):
        return self.tap(self.AC_HOME)

    def back(self):
        return self.tap(self.AC_BACK)

    def forward(self):
        return self.tap(self.AC_FORWARD)

    def browser_search(self):
        return self.tap(self.AC_SEARCH)


# US keyboard layout mapping. HID keyboard reports key positions, not text;
# the host interprets them according to its active keyboard layout.
_PUNCTUATION = {
    "!": (KC.N1, KC.LEFT_SHIFT), "@": (KC.N2, KC.LEFT_SHIFT),
    "#": (KC.N3, KC.LEFT_SHIFT), "$": (KC.N4, KC.LEFT_SHIFT),
    "%": (KC.N5, KC.LEFT_SHIFT), "^": (KC.N6, KC.LEFT_SHIFT),
    "&": (KC.N7, KC.LEFT_SHIFT), "*": (KC.N8, KC.LEFT_SHIFT),
    "(": (KC.N9, KC.LEFT_SHIFT), ")": (KC.N0, KC.LEFT_SHIFT),
    "-": (KC.MINUS,), "_": (KC.MINUS, KC.LEFT_SHIFT),
    "=": (KC.EQUAL,), "+": (KC.EQUAL, KC.LEFT_SHIFT),
    "[": (KC.OPEN_BRACKET,), "{": (KC.OPEN_BRACKET, KC.LEFT_SHIFT),
    "]": (KC.CLOSE_BRACKET,), "}": (KC.CLOSE_BRACKET, KC.LEFT_SHIFT),
    "\\": (KC.BACKSLASH,), "|": (KC.BACKSLASH, KC.LEFT_SHIFT),
    ";": (KC.SEMICOLON,), ":": (KC.SEMICOLON, KC.LEFT_SHIFT),
    "'": (KC.QUOTE,), '"': (KC.QUOTE, KC.LEFT_SHIFT),
    "`": (KC.GRAVE,), "~": (KC.GRAVE, KC.LEFT_SHIFT),
    ",": (KC.COMMA,), "<": (KC.COMMA, KC.LEFT_SHIFT),
    ".": (KC.DOT,), ">": (KC.DOT, KC.LEFT_SHIFT),
    "/": (KC.SLASH,), "?": (KC.SLASH, KC.LEFT_SHIFT),
}


class Keyboard(KeyboardInterface):
    def __init__(self):
        super().__init__()
        self.consumer = None

    def tap(self, key, *modifiers, hold_ms=30):
        self.send_keys(tuple(modifiers) + (key,))
        time.sleep_ms(hold_ms)
        return self.send_keys(())

    def hotkey(self, *keys, hold_ms=40):
        if not keys:
            raise ValueError("at least one key is required")
        self.send_keys(keys)
        time.sleep_ms(hold_ms)
        return self.send_keys(())

    def send_text(self, text, delay_entre_letras=10, press_ms=10):
        if not isinstance(text, str):
            raise TypeError("text must be str")
        if delay_entre_letras < 0 or press_ms < 0:
            raise ValueError("delays must be >= 0")

        sent = 0
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                keys = (KC.ENTER,)
            elif char == "\t":
                keys = (KC.TAB,)
            elif char == " ":
                keys = (KC.SPACE,)
            elif "a" <= char <= "z":
                keys = (ord(char) - ord("a") + KC.A,)
            elif "A" <= char <= "Z":
                keys = (ord(char.lower()) - ord("a") + KC.A, KC.LEFT_SHIFT)
            elif "0" <= char <= "9":
                keys = ({ "0": KC.N0, "1": KC.N1, "2": KC.N2,
                          "3": KC.N3, "4": KC.N4, "5": KC.N5,
                          "6": KC.N6, "7": KC.N7, "8": KC.N8,
                          "9": KC.N9 }[char],)
            elif char in _PUNCTUATION:
                keys = _PUNCTUATION[char]
            else:
                raise ValueError("unsupported character for US HID layout: " + repr(char))

            self.send_keys(keys)
            if press_ms:
                time.sleep_ms(press_ms)
            self.send_keys(())
            sent += 1
            if delay_entre_letras:
                time.sleep_ms(delay_entre_letras)
        return sent

    def _consumer_action(self, method):
        if self.consumer is None:
            raise OSError("USB_HID_NOT_INITIALIZED")
        return getattr(self.consumer, method)()

    def volume_up(self):
        return self._consumer_action("volume_up")

    def volume_down(self):
        return self._consumer_action("volume_down")

    def mute(self):
        return self._consumer_action("mute")

    def play_pause(self):
        return self._consumer_action("play_pause")

    def next_track(self):
        return self._consumer_action("next_track")

    def previous_track(self):
        return self._consumer_action("previous_track")

    def home(self):
        return self._consumer_action("home")

    def back(self):
        return self._consumer_action("back")


class Mouse(MouseInterface):
    def move(self, dx, dy):
        return self.move_by(dx, dy)

    def press(self, button="left"):
        method = {"left": self.click_left, "right": self.click_right,
                  "middle": self.click_middle}.get(button)
        if method is None:
            raise ValueError("button must be left, right or middle")
        return method(True)

    def release(self, button="left"):
        method = {"left": self.click_left, "right": self.click_right,
                  "middle": self.click_middle}.get(button)
        if method is None:
            raise ValueError("button must be left, right or middle")
        return method(False)

    def click(self, button="left", hold_ms=30):
        self.press(button)
        time.sleep_ms(hold_ms)
        return self.release(button)


keyboard = None
mouse = None
consumer = None


def init():
    """Create and configure keyboard, mouse and media-control HID interfaces."""
    global keyboard, mouse, consumer
    if keyboard is not None and mouse is not None and consumer is not None:
        return connect()

    keyboard = Keyboard()
    mouse = Mouse()
    consumer = Consumer()
    keyboard.consumer = consumer

    dev = usb.device.get()
    dev.config(keyboard, mouse, consumer)
    return usb.device.connect()


def connect():
    """Reconnect the already configured USB HID Device after Host mode."""
    if keyboard is None or mouse is None or consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return usb.device.connect()


def disconnect():
    """Disconnect USB Device before switching to USB Host mode."""
    return usb.device.disconnect()


def wait_ready(timeout_ms=10000):
    if keyboard is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while not (keyboard.is_open() and mouse.is_open() and consumer._interface.is_open()):
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            return False
        time.sleep_ms(20)
    return True


def send_text(text, delay_entre_letras=10):
    if keyboard is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return keyboard.send_text(text, delay_entre_letras)


def send_keys(keys, timeout_ms=100):
    if keyboard is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return keyboard.send_keys(keys, timeout_ms)


def move(dx, dy):
    if mouse is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return mouse.move(dx, dy)


def click(button="left", hold_ms=30):
    if mouse is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return mouse.click(button, hold_ms)


def volume_up():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.volume_up()


def volume_down():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.volume_down()


def mute():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.mute()


def play_pause():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.play_pause()


def next_track():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.next_track()


def previous_track():
    if consumer is None:
        raise OSError("USB_HID_NOT_CONFIGURED: call usb_hid.init() first")
    return consumer.previous_track()
