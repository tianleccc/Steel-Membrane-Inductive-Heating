# Steel Membrane · Imaging and Infrared Heating Panel

A local web panel for Raspberry Pi 5 that combines Camera Module 3 fluorescence imaging with MLX90614 infrared temperature control. It provides named assays with operator records, coordinated heating and image acquisition, searchable assay folders, live preview, adjustable PID control, and CSV temperature logs.

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
| MLX90614 SDA | BCM GPIO 5 | **29** | I2C data; default sensor address: `0x5A`. |
| MLX90614 SCL | BCM GPIO 6 | **31** | Software I2C clock; bus 20 (`/dev/i2c-20`). |
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

### Sensor bus on GPIO5 / GPIO6

This device uses Linux software I2C bus 20. GPIO5 is SDA (physical pin 29),
GPIO6 is SCL (physical pin 31). The boot overlay is:

```ini
[all]
dtoverlay=i2c-gpio,bus=20,i2c_gpio_sda=5,i2c_gpio_scl=6,i2c_gpio_delay_us=5
```

The installer adds this configuration using `scripts/configure-sensor-bus.sh`.
Reboot after first installation to create `/dev/i2c-20`. For an existing device,
run `sudo bash scripts/configure-sensor-bus.sh`, set `sensor_bus` to `20` in
`config.json`, and reboot. The application explicitly opens the numbered Linux
bus using Adafruit Extended Bus; it no longer implicitly selects GPIO2/3.

GPIO5/6 do not have the dedicated external I2C pull-ups provided on GPIO2/3.
Ensure the sensor module has suitable pull-ups to **3.3 V** on both SDA and SCL;
if absent, add external resistors (typically 4.7 kOhm on short wiring).
Keep signal wiring short and away from heating power wiring. Changing pins alone
cannot guarantee that electrical noise, grounding or supply problems disappear.

### Configuration corresponding to the wiring

Device-specific settings are stored in the untracked `config.json` file:

```json
{
  "heater_enabled": false,
  "heater_gpio": 24,
  "led_gpio": 16,
  "led_active_low": false,
  "sensor_address": 90,
  "sensor_bus": 20
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
| Infrared heating | Accepts a target temperature, duration in minutes measured from the time Start is pressed, and Kp/Ki/Kd settings. PID output is automatically bounded to 0–100%; there is no user duty-cap control. |
| Photo archive | Provides date filtering, pagination, full-image viewing, previous/next navigation, and original-image downloads. Date filtering and filenames use UTC; displayed times use the browser's local timezone. |
| Temperature logs | Creates a separate CSV for each heating run. Each sample is written and flushed immediately rather than waiting for program exit. |
| Stop all | Stops heating, pending acquisition, and excitation illumination. Completed images are retained. |

Image capture preserves the original camera defaults: manual lens position **11.5**, automatic exposure, and automatic white balance.

Heating and timed acquisition operate independently. **Closing the browser does not stop an active heating run or acquisition job.** Use Stop all before leaving if you want both stopped. The default maximum heating duration is 2 hours; output turns off when the selected duration expires.

## Assay workflow

1. In **Run an assay**, enter an assay name and operator name, total duration in **minutes**, target temperature, and photo interval in **seconds**.
2. Adjust **PID settings** in the infrared heating module if needed. These gains apply to the next independent heating run or assay; they cannot be changed during an active run.
3. Select **Start assay**. The duration includes heating up; the clock does not wait for the target temperature. The first capture is scheduled immediately, followed by captures at the selected interval while time remains. The illumination warmup and camera processing may delay a capture; missed intervals are not replayed.
4. Heating and acquisition stop together at the end of the assay. **Stop assay** and **Stop all** end the run early without deleting saved data. A camera or temperature-control fault also ends the assay and records the failure reason.
5. Open **Photo archive**, select an operator or search by assay name, and open an assay folder. **View temperature logs** opens the corresponding CSV records.

Each run gets a unique ID, even when the assay name and operator are repeated. The manifest stores the name, operator, target, duration, interval, PID gains, start/end times, and run status. Operator names are searchable labels, not authenticated accounts or access restrictions. All users on this shared panel can see the archive.

An assay owns both instruments while it runs. Independent start controls are blocked to prevent overlapping jobs. Independent runs still work outside an assay; their data and all existing pre-assay files appear under **Unassigned**. No existing files are relabeled or moved automatically.

Data is stored on disk as:

```text
data/
  assays/
    <UTC-timestamp>_<unique-id>/
      assay.json
      photos/  # JPEG originals, thumbnails, JSON metadata
      logs/    # CSV logs with assay identity and PID gains
  photos/      # Unassigned images
  logs/        # Unassigned logs
  .trash/      # Recoverable deletions
```

Service restarts do not resume assays. Runs that were active are marked **interrupted**, and outputs remain off. Closing the browser does not stop the assay.

## Deleting and restoring data

- Open a photo and choose **Delete photo** to remove its original, thumbnail, and metadata together.
- Choose **Delete** beside a temperature log to remove that CSV, or **Delete assay folder** to remove an entire assay and its data.
- Each action asks for confirmation and moves data into `data/.trash/`, rather than permanently erasing it. Running assays and files still being written cannot be deleted.
- Use **Undo deletion** to restore the most recently deleted item in the current page session. Restore does not overwrite existing files. To restore several earlier deletions after a page reload, the retained `.trash/<deletion-id>/manifest.json` records each original path and the stored file names for manual recovery.
- Trash is retained without automatic purging, so deletion does not immediately reclaim disk space. Include it in backups if recovery is needed.

## Temperature control and protection

The editable PID controls start with the device defaults: `Kp=20`, `Ki=0.1`, and `Kd=0`. Every five 0.1-second cycles, it switches heating output off for infrared sampling. Three readings are combined using a median and an exponential moving average (EMA).

A temporary sensor read error or invalid reading immediately sets heating output to zero and opens a recovery window ending 2 seconds after the last accepted batch. Two consecutive valid batches within that window restore temperature feedback and continue the same run without extending its timer. Assays and photo capture continue during this brief output-off period. Persistent errors or stale feedback latch heating off; a raw sample reaching the default **110 °C** cutoff always latches off immediately, even if other samples in the batch are invalid. CSV columns `sample_status` and `sensor_error` record retry and recovery events; no invalid temperature is used for PID control. Once valid readings return and the temperature is below the cutoff, clear the fault manually before starting another run. Cutoff detection checks individual raw readings before median/EMA filtering. PID gains can be configured before a run, from 0 to 1000 each, with at least one nonzero gain. The existing minimum-duty rule and output slew setting remain in the device configuration; final output never exceeds 100%. Old `duty_cap` entries in local configuration are ignored.

The legacy `temp.py` software emissivity correction was incorrect for emissivity values other than 1. This implementation uses the MLX90614's reported temperature directly and does not modify its EEPROM. This matches the legacy behavior with software emissivity set to 1. Infrared measurements of metal depend on surface emissivity, reflected radiation, and the sensor's field of view; validate the measurements and tune the PID after installing the sensor.

The panel does not expose the legacy resistance-based power/current estimates as limits because this system has no measured current or power feedback.

Software protection depends on the sensor, operating system, and application. Keep independent hardware emergency-stop and overtemperature protection for the heating equipment.

## Data and Git workflow

| Path | Contents | Tracked in Git? |
|---|---|---|
| `panel/` | Web application, camera control, and temperature control | Yes |
| `config.example.json` | Default configuration for new installations | Yes |
| `config.json` | Settings specific to the current device | No |
| `data/assays/<assay-id>/` | Assay manifest, photos, metadata, and temperature logs | No |
| `data/.trash/` | Recoverable deleted files and deletion manifests | No |
| `data/photos/` | Unassigned / standalone images, thumbnails, and JSON metadata | No |
| `data/logs/` | Unassigned / standalone temperature CSV files | No |
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

## Downloading photos and exporting to USB

Open **Photo archive**, then open an assay folder:

- **Download all photos (ZIP)** prepares every original photo in the folder, plus
  photo metadata and the assay manifest. It ignores the current date filter and
  excludes thumbnails and temperature CSVs. When preparation finishes, click
  **Download ready ZIP** to save it on the computer running the browser.
- **Export assay to USB** copies the complete assay (originals, thumbnails,
  metadata, temperature logs and assay manifest) into a new folder on a USB drive
  connected to the **Raspberry Pi**. Select the drive and click the export button.
  It also works offline from the desktop console. No existing USB files are overwritten.

Finish or stop the assay before exporting. Export progress appears in the archive;
closing the page does not stop copying. Reopen the archive to check the latest job.
The export flushes the USB filesystem and verifies each file by SHA-256 before
showing **Export complete**. Then click **Safely eject USB** and wait for the
unmounted confirmation before unplugging it. If the drive has multiple mounted
volumes, eject all of them first. You can also use the Pi desktop file manager. The USB option lists writable, mounted USB storage
under `/media`, `/run/media`, or `/mnt`; if nothing appears, open the drive in the
Pi file manager to mount it and click **Refresh USB drives**. The panel does not
format disks or change their permissions. A drive attached to a remote laptop is
not a Pi USB drive: use ZIP download for that case.

Exports leave original data unchanged. If a USB export fails or the Pi loses power,
a hidden `.steel-export-*.partial` folder may remain; it is incomplete and should
not be treated as a successful export. Re-export after reconnecting the drive.
ZIP preparation needs local free space. Only the latest ZIP is retained in
`data/.exports/`; preparing another export or restarting the service clears the
previous ZIP, so download it before starting another export. USB exports are
independent copies and are not cleared by the panel.

## Instrument sleep and wake

Use **Sleep instruments** at the top of the panel after stopping any assay,
heating or acquisition. Sleep stops both hardware workers, closes the camera and
I2C connection, and turns heating and excitation outputs off. The web server,
archive, downloads and USB exports stay available. There are no temperature
readings or live images while asleep. **Wake instruments** reinitializes both
instruments; wait for readiness before starting a new run. Experiments never
resume automatically. Existing heating faults still require explicit clearing
after valid readings return.

Sleep is remembered across service restarts in `data/instrument-power.json`.
This is instrument sleep, not OS suspend: the Pi, network and desktop keep running.
The sensor supply remains physically connected; stopping reads does not cut its
power. Fully disconnecting sensor power requires additional switched hardware.
Stopping the camera and image processing should reduce load, but the exact
power and temperature reduction depends on the hardware and desktop workload.

## Offline desktop console

On a Pi with Raspberry Pi OS Desktop and Chromium, open **Steel Membrane Console**
from the desktop or application menu. It opens a dedicated, maximized window using
`http://127.0.0.1:8080/` (or the port in `config.json`). No Wi-Fi, Ethernet, Internet,
or fixed IP address is needed. All interface assets are served locally.

The desktop and wireless browser interfaces share the same instrument service,
assays, photos, logs, and live state. Opening the console does not launch a second
hardware controller. The service starts at boot and leaves outputs off; opening
the console does not start heating or acquisition. Closing the console does not
stop an active experiment. Use **Stop assay** or **Stop all** before closing if needed.

Install or recreate the desktop and application-menu entries as the desktop user:

```bash
cd ~/steel-membrane
bash scripts/install-desktop.sh
```

The main installation script also creates these entries when Chromium is present.
On a desktop installation missing Chromium, install it with
`sudo apt-get install chromium` first. A Raspberry Pi OS Lite installation needs
a graphical desktop session before it can show the console. If the desktop asks
whether to execute the launcher, choose **Execute** / **Trust and Launch**.
The console waits briefly for the service during boot and reports an error if it
cannot connect. Its browser profile is separate from the user's normal browser,
under `~/.config/steel-membrane-console`. Wireless access remains available at
`http://<Pi-IP>:8080/` whenever the Pi is connected to the local network.

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
