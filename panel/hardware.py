"""Hardware imports are deferred, allowing control tests on a PC."""
class IRHardware:
    def __init__(self, config):
        from gpiozero import PWMOutputDevice
        from adafruit_extended_bus import ExtendedI2C
        import adafruit_mlx90614
        self.heater = None
        self.i2c = None
        try:
            self.heater = PWMOutputDevice(config['heater_gpio'], frequency=config['pwm_hz'],
                                          initial_value=0, active_high=True)
            self.i2c = ExtendedI2C(config.get('sensor_bus', 20))
            self.sensor = adafruit_mlx90614.MLX90614(self.i2c, address=config['sensor_address'])
        except BaseException:
            self.close()
            raise

    def set_duty(self, percent):
        self.heater.value = percent/100

    def read(self):
        # Keep the sensor's configured emissivity. Do not apply the incorrect
        # fourth-power software correction in the legacy script.
        return self.sensor.object_temperature, self.sensor.ambient_temperature

    def close(self):
        try:
            if self.heater:
                self.heater.off()
                self.heater.close()
        finally:
            if self.i2c:
                self.i2c.deinit()
