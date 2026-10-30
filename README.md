# Steel Membrane · Imaging and Infrared Heating Panel

A local web panel for Raspberry Pi 5 that combines Camera Module 3 fluorescence imaging with MLX90614 infrared temperature control. It provides named assays with operator records, coordinated heating and image acquisition, searchable assay folders, live preview, adjustable PID control, and CSV temperature logs.

Open `http://<raspberry-pi-ip>:8080` in a browser on the same network.

## Hardware and wiring

### Pin numbering

The software uses **BCM GPIO numbers**, not physical header positions. For example, `heater_gpio: 23` means **GPIO 23 on physical pin 16**, not physical pin 23. The connections below refer to the Raspberry Pi 5 **40-pin GPIO header**.

Turn off the Raspberry Pi and the external load supplies before changing wiring. Identify physical pin 1 using the board markings and the [official Raspberry Pi GPIO reference](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio).

### Connection table

| Device / connection | Raspberry Pi signal | Physical header pin | Notes |
|---|---|---|---|
| Heating MOSFET / driver control input | BCM GPIO 23 | **16** | Active HIGH; default PWM frequency: 100 Hz. Connect to the driver's logic input or an appropriate MOSFET gate interface. |
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

The temperature sensor for this project is **MLX90614 over I2C**. Each read validates the SMBus PEC (CRC-8) and sensor error bit before accepting the temperature. Corrupt reads enter the output-off retry path. A valid overtemperature sample still stops heating; its raw peak and sample batch are recorded in the fault message and CSV before closing the log. The earlier MAX31855/SPI script is not used. Heating uses **GPIO 23 (physical pin 16)**; move the heater control wire from the previous GPIO 24 (physical pin 18) when updating an existing installation, and set `heater_gpio` to `23` in its `config.json`.

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
  "heater_gpio": 23,
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
4. Heating and acquisition stop together at the end of the assay. **Stop assay** and **Stop all** end the run early without deleting saved data. An overtemperature event or fatal instrument failure ends the assay and records the failure reason; recoverable sensor and per-photo analysis errors do not.
5. Open **Photo archive**, select an operator or search by assay name, and open an assay folder. **View temperature logs** opens the corresponding CSV records.

One named assay owns one folder. Assay names must be unique across operators;
case, repeated/outer whitespace and equivalent Unicode forms do not create a
new name. Deleted assay names are immediately reusable. Duplicate names are rejected before creating
a folder or starting either instrument. Each run also gets a unique internal ID.
The manifest stores the name, operator, target, duration, interval, PID gains,
start/end times and run status. Operator names are searchable labels, not
authenticated accounts or access restrictions. All users share the archive.

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
- Assay folder deletion asks for confirmation, then permanently removes all photos and logs and frees storage. It has no Undo. Running assays and files still being written cannot be deleted.
- Individual photo and temperature-log deletions still use Trash. Use **Undo deletion** to restore the most recently deleted file in the current page session. Restore does not overwrite existing files. To restore several earlier deletions after a page reload, the retained `.trash/<deletion-id>/manifest.json` records each original path and the stored file names for manual recovery.
- Old deleted-assay trash is purged during this update. Individual-file Trash is retained; deleting the whole assay also removes its separately trashed files.

## Temperature control and protection

The editable PID controls start with the device defaults: `Kp=50`, `Ki=0.1`, and `Kd=0`. Every five 0.1-second cycles, it samples infrared temperature without interrupting the heating output. Three readings are combined using a median and an exponential moving average (EMA). Invalid or stale temperature feedback and overtemperature still turn heating off.

With `startup_pulse: true` (the default), each heating run or assay starts with GPIO HIGH for approximately 0.1 seconds, LOW for at least 1 second, and HIGH for approximately 0.1 seconds before returning to PID control. Timing follows the control loop and includes sensor I/O latency. The sequence counts toward the run duration and only runs when the measured temperature is below the target. Stop, expiry, overtemperature, or invalid/stale feedback interrupt it; sensor recovery does not replay it. Set `startup_pulse` to `false` in `config.json` to disable this experimental startup workaround. It does not replace proper gate driving or reproduce disconnecting a floating gate.

Sensor read errors, checksum errors, and stale feedback immediately set heating
output to zero while the same timed run and photo acquisition continue. There is
no five-second latched sensor fault: two consecutive valid batches restore
feedback and PID automatically, without extending the original deadline or
replaying the startup pulse. The old `sensor_recovery_s` setting is retained for
configuration compatibility but no longer cancels an assay. Stop and timer expiry
still end the run even while feedback is unavailable. A raw sample reaching the
**110 °C** cutoff always latches heating off and ends the assay; valid later
readings cannot automatically restart an overtemperature-stopped run.
CSV `sample_status` and `sensor_error` retain retry/recovery events. Missing
temperature at photo time does not prevent fluorescence analysis: the reaction
CSV has an empty `Measured_C` and `Temperature_status=unavailable`, rather than a
stale or invented reading. Melting steps continue on the commanded schedule;
missing feedback can affect actual thermal exposure, so the CSV preserves it.
Cutoff detection checks individual raw readings before median/EMA filtering. PID gains can be configured before a run, from 0 to 1000 each, with at least one nonzero gain. The PID avoids accumulating integral when output is saturated. The configured minimum duty tapers linearly to zero within 3 °C below the target, retaining its full floor farther below target. Output slew remains in the device configuration; final output never exceeds 100%. Old `duty_cap` entries in local configuration are ignored.

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

- **Download all photos (ZIP)** creates a ZIP containing one folder named after
  the assay entered in the panel. Extract it to get the photo folder.
- **Export assay to USB** creates the same photo folder directly on a USB drive
  connected to the **Raspberry Pi**, including when using the offline desktop console.
- Both exports contain original JPEG images, in capture order, named
  `image001.jpg`, `image002.jpg`, and so on. When reaction measurements exist,
  `reaction_log.csv` is included with an `Export_image` column linking each row
  to its numbered JPEG. Temperature logs remain a separate download.
- Spaces and Unicode in assay names are preserved; filesystem-invalid characters
  are replaced with underscores. If a USB folder already exists, a suffix such as
  ` (2)` is added so previous exports are never overwritten.
- Photo exports include the entire assay regardless of the current date filter.
  Wait for ZIP preparation, then click **Download ready ZIP** to save it.

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

## Real-time reaction measurements

Every new saved photo is analyzed at full JPEG resolution, including independent
captures and assays. **Workspace → Reaction curves** shows only the current
acquisition's corrected values for Region 1–4, with no history selectors or
downloads. After a service restart it waits for a new acquisition rather than
loading a saved run. **Check reaction area**, below the excitation-light control, shows
the same four labeled, colored circles over the live preview without changing
the original image or switching on the light. Enable excitation separately when
checking fluorescence. Coordinates are in `panel/reaction_regions.json`, fitted
to a square in the supplied top/right/bottom/left order, with radius **108 px**
on a **4608 × 2592** image. Other capture resolutions are rejected to prevent
misaligned measurements.

The calculation matches the supplied analyzer: OpenCV HSV bounds `[35,50,50]`
to `[85,255,255]`, raw signal `2 × sum(V)` for green pixels within each circle,
EMA alpha `0.35`, and corrected signal `max(0, EMA − running minimum of EMA)`.
The larger circles include surrounding black pixels, but pixels outside the
green HSV mask do not contribute to the raw sum. This is a temporal background
correction, not subtraction of a separate spatial background region.

Each assay or timed acquisition gets an independent baseline and reaction ID;
a single manual capture is its own one-point run. Time is measured from the
first photo using the actual monotonic capture clock, not an assumed interval.
The first corrected value is zero. Open the corresponding **Photo archive**
folder and click the green **View amplification curve** button to scroll to
the two charts below the photos: raw and background-corrected fluorescence.
Named assay folders show their one assay directly, with **Download assay CSV**.
Only **Unassigned**, the collection of independent manual captures, has a manual
acquisition selector and a combined manual-captures CSV. Old photos are
not retroactively analyzed. Auto exposure and white balance remain unchanged;
these are relative fluorescence measurements in arbitrary units.

Each original JPEG has a matching `.reaction.csv` containing its raw, smoothed,
background and corrected values, photo filename, timestamp, reaction ID, ROI
coordinates and algorithm settings. These immutable rows survive restarts and
are combined into one assay CSV download or photo ZIP/USB export. The per-photo
CSV files are measurements of the same assay, not additional assay runs. Reaction
records have no independent delete endpoint: deleting/restoring a photo moves
its CSV together, and deleting/restoring an assay moves the entire folder.
Clearing temperature logs never removes reaction data. Deleting an earlier
photo does not recalculate the remaining historical EMA values.

If analysis fails, the original photo is retained with `reaction_error` in its
JSON metadata. A visible warning reports the missing fluorescence row and
acquisition continues; no false zero is recorded. Fatal camera/storage failures
that prevent saving photos still stop the assay. Install OpenCV on existing Pi installations with
`sudo apt-get install python3-opencv`; `scripts/install.sh` includes this
dependency for new installations (the venv uses system site packages).

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

### Amplification followed by melting

Enable **Melting curve** in **Run an assay** to configure start/end temperatures
and the increment (default 1 °C). Amplification runs first; **Skip → Melting curve**
cancels its remaining acquisition and safely waits for the camera before advancing.
Without melting enabled, the assay still ends after amplification as before.

Melting includes both endpoints (the last increment can be smaller). Each setpoint
command starts a 60-second wait, followed by illumination warmup and one photo.
This is a timed protocol, not a wait for thermal equilibrium. A point fails closed
if capture has not completed within 90 seconds; all points together must
fit the configured maximum heating duration. Existing cutoff and sensor-fault
protection apply throughout; Stop and service restart never resume heating.

The Workspace shows the current assay's amplification and live melting data.
After all melting points complete, it also shows **−dF/dT**: finite differences
of five-point local-quadratic smoothed fluorescence against commanded temperature, with one-sided endpoints and
a nonuniform three-point interior gradient. Smoothing fits actual temperatures, including a short final increment; traces shorter than five points stay unsmoothed. Raw CSV data is unchanged. Melting uses neither amplification's
EMA nor its running-minimum baseline correction. An interrupted sweep has no
completed derivative plot. The same plots are available in its archive folder.

New named assays store photos and linked per-photo CSVs in
`photos/amplification/` and `photos/melting/`; the archive stage selector separates
them. Download assay CSV includes both stages, identified by `Stage`, `Setpoint_C`
and `Measured_C`. Amplification raw/smoothed/background/corrected columns remain
available; melting plots use the raw columns. ZIP/USB exports retain the two
photo subfolders and map each CSV row to its exported image. Photo deletion and
Undo always move the linked measurement with it. Legacy flat folders and older
CSV schemas remain readable without modifying existing files.

Region calibration was updated from the manually selected 2026-10-29 coordinates:
the four centers are least-squares fitted to a square, preserving Region 1–4 order,
with a common 108-pixel radius. Historical CSVs retain their recorded ROI geometry.

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

The temperature card displays the current phase: amplification remaining time, or estimated melting time including all remaining points and illumination warmup. The assay badge separately shows time to the next melting photo. Capture/analysis overhead can extend the estimate. The old generic Clear heating fault button is removed; only an actual latched overtemperature exposes Acknowledge overtemperature after cooling. Missing sensor feedback still holds output at zero and automatically recovers without cancelling the assay.
