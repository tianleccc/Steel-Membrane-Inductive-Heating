import sys
import unittest
from unittest.mock import Mock,patch
from panel.hardware import IRHardware

class SensorBusTests(unittest.TestCase):
    def test_numbered_bus_used_and_outputs_initially_off(self):
        gpio=Mock();extended=Mock();mlx=Mock()
        with patch.dict(sys.modules,{'gpiozero':gpio,'adafruit_extended_bus':extended,'adafruit_mlx90614':mlx}):
            hw=IRHardware(dict(heater_gpio=24,pwm_hz=100,sensor_bus=20,sensor_address=90))
            extended.ExtendedI2C.assert_called_once_with(20)
            gpio.PWMOutputDevice.assert_called_once_with(24,frequency=100,initial_value=0,active_high=True)
            mlx.MLX90614.assert_called_once_with(extended.ExtendedI2C.return_value,address=90)
            hw.close()
            extended.ExtendedI2C.return_value.deinit.assert_called_once()
