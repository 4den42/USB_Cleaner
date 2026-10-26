#!/usr/bin/env python3
"""
usb_display.py

Watches for USB devices being plugged in. If a plugged-in device is USB
mass storage, shows its info on an SSD1306 I2C OLED, counts down on
screen, then unmounts and formats it. Unplugging during the countdown
cancels the format.

Tested target: NanoPi NEO running DietPi.
Display: HiLetgo 0.91in SSD1306 128x32 I2C OLED, confirmed at address 0x3c on bus 0.

*** WARNING: THIS SCRIPT AUTOMATICALLY ERASES ANY USB STORAGE DEVICE  ***
*** YOU PLUG IN, after a short on-screen countdown. Only run this on  ***
*** a machine dedicated to this purpose. Don't plug in anything you   ***
*** care about while this is running. Unplugging the device during    ***
*** the countdown cancels the format for that insertion.              ***

Safety measures built in:
    - COUNTDOWN_SECONDS delay before formatting starts, shown on screen
    - Format is cancelled if the device is unplugged during the countdown
    - Only devices flagged 'removable' in sysfs are ever considered
    - Only /dev/sdX-style USB mass storage is considered (never mmcblk,
      i.e. never the board's own SD/eMMC boot storage)

Requires:
    sudo apt install python3-dev libjpeg-dev zlib1g-dev libfreetype6-dev -y
    sudo apt install dosfstools -y      # for mkfs.vfat (fat32)
    sudo apt install exfatprogs -y      # only if using exfat
    sudo pip3 install luma.oled --break-system-packages
    (pyudev should already be installed from earlier setup)

Run:
    sudo python3 usb_display.py
"""

import os
import time
import threading
import subprocess
from collections import OrderedDict

import pyudev
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont

# ---------- Display setup ----------

I2C_PORT = 0          # confirmed via `ls /dev/i2c*` -> /dev/i2c-0
I2C_ADDRESS = 0x3C    # confirmed via `i2cdetect -y 0`

DEVICE_INIT_RETRIES = 12    # keep trying for up to ~1 minute at boot
DEVICE_INIT_RETRY_DELAY = 5  # seconds between attempts


def init_display():
    """
    Initialize the OLED, retrying on I2C errors. At boot, especially on
    a breadboard connection, the bus or the panel itself can take longer
    to be ready than expected - retry instead of crashing outright.
    """
    last_error = None
    for attempt in range(1, DEVICE_INIT_RETRIES + 1):
        try:
            serial_interface = i2c(port=I2C_PORT, address=I2C_ADDRESS)
            return ssd1306(serial_interface, width=128, height=32)
        except Exception as e:
            last_error = e
            print(f"[display init] attempt {attempt}/{DEVICE_INIT_RETRIES} failed: {e}")
            time.sleep(DEVICE_INIT_RETRY_DELAY)
    raise RuntimeError(f"Could not initialize OLED after {DEVICE_INIT_RETRIES} attempts: {last_error}")


device = init_display()  # HiLetgo 0.91in confirmed 128x32

try:
    font_small = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9
    )
except IOError:
    font_small = ImageFont.load_default()

LINE_HEIGHT = 10  # px per line - tuned for a 32px-tall display
MAX_LINES = 3     # 32px / 10px per line, leaves a hair of margin

# ---------- Auto-format settings ----------

COUNTDOWN_SECONDS = 3           # time to unplug and cancel before formatting starts
FORMAT_FS_TYPE = "fat32"        # "fat32", "exfat", or "ext4"

FS_COMMANDS = {
    "fat32": ["mkfs.vfat", "-F", "32", "-I"],
    "exfat": ["mkfs.exfat"],
    "ext4": ["mkfs.ext4", "-F"],
}

HUB_DEVICE_CLASS = "09"          # USB spec class code for hub devices
LINUX_ROOT_HUB_VENDOR = "1d6b"   # Linux Foundation's reserved vendor ID for virtual root hubs

# Keep a rolling record of currently-connected USB devices so we can
# show something useful even when nothing just changed.
connected_devices = OrderedDict()

# ---------- Screen sleep settings ----------

IDLE_TIMEOUT_SECONDS = 120  # turn the OLED off after this many seconds of no activity

_display_lock = threading.Lock()
_last_activity = time.time()
_screen_is_off = False


# ---------- Display helpers ----------

def show_message(lines):
    """Draw a list of text lines to the OLED, one per row (capped to fit the screen)."""
    global _last_activity, _screen_is_off
    with _display_lock:
        _last_activity = time.time()
        if _screen_is_off:
            device.show()
            _screen_is_off = False
        with canvas(device) as draw:
            y = 0
            for line in lines[:MAX_LINES]:
                draw.text((0, y), line, font=font_small, fill="white")
                y += LINE_HEIGHT


def show_idle_screen():
    lines = [f"USB Monitor: {len(connected_devices)} dev(s)"]
    if not connected_devices:
        lines.append("No USB devices")
    else:
        for dev_id, info in list(connected_devices.items())[:2]:
            lines.append(f"{info['product'][:20]}")
    show_message(lines)


def screen_saver_loop():
    """
    Background loop: turns the OLED off after IDLE_TIMEOUT_SECONDS with
    no display updates. show_message() automatically wakes it back up
    on the next update, so no other code needs to know about this.
    """
    global _screen_is_off
    while True:
        time.sleep(5)
        with _display_lock:
            idle_for = time.time() - _last_activity
            if not _screen_is_off and idle_for >= IDLE_TIMEOUT_SECONDS:
                device.hide()
                _screen_is_off = True


# ---------- USB device helpers ----------

def is_hub(usb_device):
    """Return True if this udev USB device is a hub (including a root hub)."""
    device_class = (
        usb_device.attributes.asstring("bDeviceClass")
        if usb_device.attributes.get("bDeviceClass") else None
    )
    vendor_id = usb_device.get("ID_VENDOR_ID", "")
    if device_class == HUB_DEVICE_CLASS:
        return True
    if vendor_id == LINUX_ROOT_HUB_VENDOR:
        return True
    return False


def format_device_info(usb_device, action):
    """Pull the useful identifying fields off a pyudev USB device."""
    vendor_id = usb_device.get("ID_VENDOR_ID", "????")
    product_id = usb_device.get("ID_MODEL_ID", "????")
    vendor = usb_device.get("ID_VENDOR_FROM_DATABASE") or usb_device.get("ID_VENDOR") or "Unknown"
    product = usb_device.get("ID_MODEL_FROM_DATABASE") or usb_device.get("ID_MODEL") or "Unknown device"
    serial_num = usb_device.get("ID_SERIAL_SHORT", "")
    return {
        "action": action,
        "vendor": vendor.replace("_", " "),
        "product": product.replace("_", " "),
        "vid_pid": f"{vendor_id}:{product_id}",
        "serial": serial_num,
    }


def find_block_device(context, usb_device, retries=6, delay=0.5):
    """
    Find the /dev/sdX block device node created for a newly-plugged USB
    device. The kernel enumerates the block device asynchronously after
    the USB device itself appears, so this retries briefly. Returns None
    if this USB device isn't mass storage (e.g. a keyboard) or isn't
    flagged removable.
    """
    for _ in range(retries):
        for disk in context.list_devices(subsystem="block", DEVTYPE="disk"):
            parent = disk.find_parent("usb", device_type="usb_device")
            if parent is not None and parent.device_path == usb_device.device_path:
                name = disk.sys_name
                removable_path = f"/sys/block/{name}/removable"
                is_removable = False
                if os.path.exists(removable_path):
                    with open(removable_path) as f:
                        is_removable = f.read().strip() == "1"
                # Extra safety: only ever consider sdX-style USB mass
                # storage, and only if flagged removable.
                if name.startswith("sd") and is_removable:
                    return f"/dev/{name}"
        time.sleep(delay)
    return None


def get_mounted_partitions(device_path):
    """Return a list of currently-mounted partitions belonging to device_path."""
    result = subprocess.run(
        ["lsblk", "-ln", "-o", "NAME,MOUNTPOINT", device_path],
        capture_output=True, text=True
    )
    mounted = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            name, mountpoint = parts
            mounted.append((f"/dev/{name}", mountpoint))
    return mounted


def unmount_partitions(mounted):
    for dev, _ in mounted:
        subprocess.run(["umount", dev], check=False)


def wipe_partition_table(device_path):
    """
    Clear any existing partition table / filesystem signatures on the
    device. mkfs.vfat refuses to format a whole-disk device (like
    /dev/sda rather than /dev/sda1) if it detects a leftover partition
    table, even with -I - wiping first avoids that failure reliably.
    """
    subprocess.run(["wipefs", "-a", device_path], capture_output=True, text=True)


def run_format(device_path, fs_type):
    cmd = FS_COMMANDS[fs_type] + [device_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0, result.stderr
    except FileNotFoundError:
        tool = cmd[0]
        return False, f"'{tool}' not found - is the right package installed?"
    except Exception as e:
        return False, str(e)


# ---------- Countdown + format workflow ----------

def countdown_and_format(device_path, product_name):
    """
    Show an on-screen countdown before formatting. If the device is
    unplugged at any point during the countdown, the format is cancelled.
    """
    for remaining in range(COUNTDOWN_SECONDS, 0, -1):
        if not os.path.exists(device_path):
            show_message(["Cancelled", "Device removed", ""])
            time.sleep(2)
            return
        show_message([
            "NEW USB - ERASING",
            product_name[:20],
            f"Unplug to cancel {remaining}s",
        ])
        time.sleep(1)

    if not os.path.exists(device_path):
        show_message(["Cancelled", "Device removed", ""])
        time.sleep(2)
        return

    show_message(["Unmounting...", product_name[:20], ""])
    mounted = get_mounted_partitions(device_path)
    if mounted:
        unmount_partitions(mounted)
    wipe_partition_table(device_path)

    show_message(["Formatting...", product_name[:20], FORMAT_FS_TYPE])
    success, error_msg = run_format(device_path, FORMAT_FS_TYPE)

    if success:
        show_message(["Done!", product_name[:20], "Formatted OK"])
        print(f"[FORMAT OK] {device_path} -> {FORMAT_FS_TYPE}")
    else:
        show_message(["Format FAILED", product_name[:20], "See console"])
        print(f"[FORMAT FAILED] {device_path}: {error_msg}")

    time.sleep(3)


# ---------- Event handling ----------

def handle_event(context, action, usb_device):
    if is_hub(usb_device):
        return  # ignore hub/root-hub churn, only react to real peripherals

    dev_path = usb_device.device_path
    info = format_device_info(usb_device, action)

    if action == "add":
        connected_devices[dev_path] = info
        show_message([
            "USB CONNECTED",
            info["product"][:20],
            f"{info['vendor'][:12]} {info['vid_pid']}",
        ])
        print(f"[+] {info['vendor']} {info['product']} ({info['vid_pid']})")
        time.sleep(2)

        block_device = find_block_device(context, usb_device)
        if block_device:
            countdown_and_format(block_device, info["product"])
        # If it's not storage (e.g. a keyboard), block_device is None and
        # we just fall through to the idle screen below.

    elif action == "remove":
        removed = connected_devices.pop(dev_path, info)
        show_message([
            "USB REMOVED",
            removed["product"][:20],
            removed["vendor"][:20],
        ])
        print(f"[-] {removed['vendor']} {removed['product']}")
        time.sleep(2)

    show_idle_screen()


def populate_existing_devices(context):
    """Seed connected_devices with whatever is already plugged in at startup (excluding hubs)."""
    for usb_device in context.list_devices(subsystem="usb", DEVTYPE="usb_device"):
        if is_hub(usb_device):
            continue
        info = format_device_info(usb_device, "add")
        connected_devices[usb_device.device_path] = info


def main():
    context = pyudev.Context()
    populate_existing_devices(context)
    show_idle_screen()

    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="usb", device_type="usb_device")

    print("Watching for USB events... (Ctrl+C to quit)")
    print(f"AUTO-FORMAT ENABLED: new storage devices will be erased as "
          f"{FORMAT_FS_TYPE} after a {COUNTDOWN_SECONDS}s countdown.")

    def event_loop():
        for usb_device in iter(monitor.poll, None):
            try:
                handle_event(context, usb_device.action, usb_device)
            except Exception as e:
                print(f"[ERROR] handle_event failed: {e}")
                show_message(["Error occurred", str(e)[:20], "See console"])
                time.sleep(3)
                show_idle_screen()

    observer_thread = threading.Thread(target=event_loop, daemon=True)
    observer_thread.start()

    saver_thread = threading.Thread(target=screen_saver_loop, daemon=True)
    saver_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        show_message(["USB Monitor", "stopped.", ""])
        print("\nStopped.")


if __name__ == "__main__":
    main()
