# USB Wipe Station

NanoPi NEO (DietPi) service that watches for USB storage devices, shows their
info on an SSD1306 128x32 I2C OLED, counts down 3 seconds, then wipes and
formats the stick as FAT32. Unplugging during the countdown cancels.

**WARNING:** any USB storage device plugged in while the service runs is erased.

## Variants
- `usb_display_with_leds.py` - OLED plus 4 status LEDs (connected, unmounting, formatting, done)
- `usb_display_no_leds.py` - OLED only

## Setup on a new Pi
```bash
git clone <repo-url> /root/Projects/usbFlash
cd /root/Projects/usbFlash
sudo ./install.sh leds      # or: sudo ./install.sh no-leds
sudo reboot                 # only if the script says the I2C overlay was added
```

## Checks
```bash
ls /dev/i2c*                                  # expect /dev/i2c-0
i2cdetect -y 0                                # expect 3c with the OLED wired
sudo journalctl -u usb-display.service -f     # live logs
```

## Wiring (NanoPi NEO)
- OLED: SDA0 = pin 3, SCL0 = pin 5, 3.3V = pin 1, GND = pin 6
- LEDs (LED version): red PG11, blue PA6, yellow PG6, green PG7
