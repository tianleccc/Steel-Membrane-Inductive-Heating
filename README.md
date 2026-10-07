# Steel Membrane · Imaging and Infrared Heating Panel

A local web panel for Raspberry Pi 5 that combines Camera Module 3 fluorescence imaging with MLX90614 infrared temperature control. It provides live preview, timed image acquisition, a photo archive, PID heating control, and CSV temperature logs.

Open `http://<raspberry-pi-ip>:8080` in a browser on the same network.

## Hardware and wiring

### Pin numbering

The software uses **BCM GPIO numbers**, not physical header positions. For example, `heater_gpio: 24` means **GPIO 24 on physical pin 18**, not physical pin 24. The connections below refer to the Raspberry Pi 5 **40-pin GPIO header**.

Turn off the Raspberry Pi and the external load supplies before changing wiring. Identify physical pin 1 using the board markings and the [official Raspberry Pi GPIO reference](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio).

### Connection table

| Device / connection | Raspberry Pi signal | Physical header pin | Notes |
|---|---|---|---|
| Heating MOSFET / driver control input | BCM GPIO 24 | **18** | Active HIGH; default PWM frequency: 100 Hz. Connect to the driver's logic input or an appropriate MOSFET gate interface. |
| Fluorescence excitation LED MOSFET / driver control input | BCM GPIO 16 | **36** | Active HIGH by default. Set `led_active_low` to `true` only if required by the driver. |
| MLX90614 SDA | BCM GPIO 2 / SDA1 | **3** | I2C data; default sensor address: `0x5A`. |
| MLX90614 SCL | BCM GPIO 3 / SCL1 | **5** | I2C clock; bus 1 (`/dev/i2c-1`). |
| MLX90614 GND | GND | **6**, or another GND pin | Connect the sensor ground to the Pi ground. |
| MLX90614 VCC / VIN | 3.3 V, **only if supported by the specific sensor module** | **1** or **17**, for a 3.3 V-compatible module | Verify the module's supply specification first. Different MLX90614 variants and breakout boards have different supply requirements. |
| Heating driver signal ground | GND | **14**, for example | Common signal reference for a non-isolated driver; follow the driver manufacturer's wiring requirements. |
| LED driver signal ground | GND | **34**, for example | Common signal reference for a non-isolated driver. |
| Camera Module 3 ribbon cable | Compatible CAM/DISP camera connector | Not on the 40-pin header | Use a Raspberry Pi 5-compatible camera cable. The detected camera in the initial setup was `imx708_wide`. |

The ground pin choices are suggested wiring positions, not software settings or a record of verified physical connections. Available header ground pins are **6, 9, 14, 20, 25, 30, 34, and 39**.

### Electrical connections

- Pi GPIO signals use **3.3 V logic**. The sensor's SDA/SCL pull-ups must be compatible with 3.3 V, even if its breakout board accepts a different supply voltage. Do not pull the Pi I2C lines up to 5 V.
- The heating circuit and excitation LED require appropriate external power supplies and drivers. GPIO pins provide control signals; they must not power either load directly.
- For non-isolated drivers, connect the Pi, sensor, and driver signal grounds to a common reference. Route high-current load return paths through the load wiring, not through the Pi GPIO header.
- Use a suitable hardware pull-down on each active-HIGH MOSFET gate or control input so the load stays off while the Pi boots or the GPIO is unconfigured. Confirm compatibility with the actual driver circuit.
- Load-side terminal assignments depend on the MOSFET/driver module. Follow its documentation for power input and load output wiring; those assignments cannot be inferred from this repository.

The temperature sensor for this project is **MLX90614 over I2C**. The earlier MAX31855/SPI script is not used. Heating uses **GPIO 24**, as confirmed for this setup; the GPIO 20 comment in the original `temp.py` was inconsistent with its default argument.

### Configuration corresponding to the wiring

Device-specific settings are stored in the untracked `config.json` file:

```json
{
  "heater_enabled": false,
  "heater_gpio": 24,
  "led_gpio": 16,
  "led_active_low": false,
  "sensor_address": 90
}
```

`90` is the decimal representation of I2C address `0x5A`. GPIO values are BCM numbers. Keep `heater_enabled` set to `false` until the sensor and heating driver have been connected and checked.

## Install on a new Raspberry Pi

Use 64-bit Raspberry Pi OS with network access and SSH enabled. The initial installation was verified on Raspberry Pi OS Trixie / Debian 13.

```bash
git clone https://github.com/tianleccc/Steel-Membrane-Inductive-Heating.git ~/steel-membrane
cd ~/steel-membrane
bash scripts/install.sh
```

Run the installer as a normal user, not as root. Enter that device's password when prompted by `sudo`.

The installer enables I2C, installs the system camera/GPIO dependencies, creates a Python virtual environment with access to system packages, and registers a systemd service. The panel starts automatically at boot.

Review `config.json` for the correct GPIO pins and driver polarity. New installations have heating disabled by default. After checking the wiring and obtaining valid sensor readings, set `heater_enabled` to `true` when ready to enable heating controls. Heating cannot start without valid, recent temperature readings.

After connecting the sensor or changing configuration, restart the service:

```bash
sudo systemctl restart steel-membrane
```

Each startup leaves heating, excitation illumination, and acquisition jobs off. Previous experiments are not resumed automatically.

## Using the panel

The panel interface, status messages, and error notifications are in English.

| Feature | Behavior |
|---|---|
| Live preview | Displays a reduced-resolution preview, typically around 3–4 updates per second. Viewing the preview does not automatically switch on the excitation LED. |
| Excitation illumination | Manually enables continuous illumination for fluorescence observation. The page requests that it turn off when hidden; the illumination lease expires after 15 seconds without renewal during normal operation. Continuous illumination can cause photobleaching. |
| Capture one image | Turns on GPIO 16, waits for the default 1-second illumination warmup, saves a 4608 × 2592 JPEG, then turns off the LED unless continuous illumination is enabled. |
| Timed acquisition | Starts the first capture immediately, then schedules subsequent captures by their start times. Missed intervals are skipped rather than queued for catch-up. Supported interval: 2–86,400 seconds; count: 1–100,000 images. |
| Infrared heating | Accepts a target temperature, duration measured from the time Start is pressed, and an output duty-cycle cap. The cap is not a measured power limit. |
| Photo archive | Provides date filtering, pagination, full-image viewing, previous/next navigation, and original-image downloads. Date filtering and filenames use UTC; displayed times use the browser's local timezone. |
| Temperature logs | Creates a separate CSV for each heating run. Each sample is written and flushed immediately rather than waiting for program exit. |
| Stop all | Stops heating, pending acquisition, and excitation illumination. Completed images are retained. |

Image capture preserves the original camera defaults: manual lens position **11.5**, automatic exposure, and automatic white balance.

Heating and timed acquisition operate independently. **Closing the browser does not stop an active heating run or acquisition job.** Use Stop all before leaving if you want both stopped. The default maximum heating duration is 2 hours; output turns off when the selected duration expires.

## Temperature control and protection

The controller retains the original PID defaults: `Kp=12`, `Ki=0.05`, and `Kd=0.1`. Every five 0.1-second cycles, it switches heating output off for infrared sampling. Three readings are combined using a median and an exponential moving average (EMA).

Sensor failures, invalid values, stale readings, or a raw sample reaching the default **110 °C** cutoff latch heating off. Once valid readings return and the temperature is below the cutoff, clear the fault manually before starting another run. Cutoff detection checks individual raw readings before median/EMA filtering. The duty-cycle cap is applied after the minimum-duty rule, so minimum duty cannot override the cap.

The legacy `temp.py` software emissivity correction was incorrect for emissivity values other than 1. This implementation uses the MLX90614's reported temperature directly and does not modify its EEPROM. This matches the legacy behavior with software emissivity set to 1. Infrared measurements of metal depend on surface emissivity, reflected radiation, and the sensor's field of view; validate the measurements and tune the PID after installing the sensor.

The panel does not expose the legacy resistance-based power/current estimates as limits because this system has no measured current or power feedback.

Software protection depends on the sensor, operating system, and application. Keep independent hardware emergency-stop and overtemperature protection for the heating equipment.

## Data and Git workflow

| Path | Contents | Tracked in Git? |
|---|---|---|
| `panel/` | Web application, camera control, and temperature control | Yes |
| `config.example.json` | Default configuration for new installations | Yes |
| `config.json` | Settings specific to the current device | No |
| `data/photos/` | Original images, thumbnails, and per-image JSON metadata | No |
| `data/logs/` | Temperature CSV files | No |
| `.venv/` | Python environment | No |

Image metadata includes the capture time, camera settings, and temperature state at capture. SSH passwords, access tokens, and private keys must not be committed.

Edit and commit on your development computer, then pull the reviewed version on the Pi:

```bash
# Development computer
# Replace the example path with the files you changed.
git add README.md
git commit -m "Describe the change"
git push origin main

# Raspberry Pi: this stops the current experiment and restarts in standby.
cd ~/steel-membrane
bash scripts/update.sh
```

The update script refuses to overwrite uncommitted changes or local commits that have not been pushed. It uses a fast-forward update, installs dependencies, runs the control tests, and restarts the service if these steps succeed.

Pulling this public repository does not require a GitHub password on the Pi. To push directly from a Pi, configure a separate SSH key or suitable deploy key for that device; do not copy another device's private key.

Back up `data/` and `config.json` separately. To duplicate the system, use the installation steps on the new Pi and review its wiring and configuration. Do not copy the old device's SSH host keys or login credentials.

## Service management and troubleshooting

```bash
sudo systemctl status steel-membrane
journalctl -u steel-membrane -n 100 --no-pager
sudo systemctl stop steel-membrane
sudo systemctl start steel-membrane

cd ~/steel-membrane
.venv/bin/python -m unittest discover -s tests -v
```

If the MLX90614 is disconnected, the panel reports a temperature-control initialization failure while imaging remains available. Power down before connecting the sensor, then start or restart the service. Check the SDA/SCL wiring, supply requirements, and configured I2C address if the sensor is still unavailable.

Do not run the legacy `camera.py` or `temp.py` alongside the panel: they would compete for the camera or GPIO pins. The panel uses a single process to own the devices and holds a device lock. Do not run multiple WSGI workers.

The panel is intended for a trusted local network and does not provide user login. Anyone who can access it on that network can control the experiment. Control requests require a same-origin page token. Do not expose port 8080 directly to the internet; use an SSH tunnel for remote access.

## References

- [Raspberry Pi GPIO and 40-pin header documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio)
- [Raspberry Pi camera connection documentation](https://www.raspberrypi.com/documentation/accessories/camera.html)
- [Official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [Melexis MLX90614 datasheet](https://www.melexis.com/-/media/files/documents/datasheets/mlx90614-datasheet-melexis.pdf)
