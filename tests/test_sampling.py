import json
import tempfile
import unittest
from pathlib import Path

from panel.heating import Heater


class TwoCycles:
    def __init__(self, hardware):
        self.cycles = 0
        self.hardware = hardware

    def is_set(self):
        return self.cycles >= 2

    def wait(self, timeout):
        self.hardware.cycle_duties.append(self.hardware.duty)
        self.cycles += 1
        return self.is_set()


class SamplingTests(unittest.TestCase):
    def run_sampling(self, second_reading):
        class Hardware:
            def __init__(self):
                self.duty = 0
                self.read_duties = []
                self.cycle_duties = []
                self.closed = False

            def set_duty(self, duty):
                self.duty = duty

            def read(self):
                self.read_duties.append(self.duty)
                if len(self.read_duties) > 3:
                    if isinstance(second_reading, Exception):
                        raise second_reading
                    return second_reading
                return (25, 23)

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as folder:
            config = json.loads(Path('config.example.json').read_text())
            config.update(data_dir=folder, heater_enabled=True, sample_every=1, startup_pulse=False)
            hardware = Hardware()
            heater = Heater(config, lambda c: hardware, clock=lambda: 10.0)
            heater.sample([(25, 23)] * 3)
            heater.start(70, 60)
            heater.quit = TwoCycles(hardware)
            heater.run()
            self.assertEqual(hardware.duty, 0)
            self.assertTrue(hardware.closed)
            return hardware, heater

    def test_sampling_keeps_existing_output_on(self):
        hardware, heater = self.run_sampling((25, 23))
        self.assertEqual(hardware.read_duties, [0, 0, 0, 100, 100, 100])
        self.assertEqual(hardware.cycle_duties, [100, 100])
        self.assertIsNone(heater.state['fault'])

    def test_read_error_still_disables_output(self):
        hardware, heater = self.run_sampling(OSError('sensor unavailable'))
        self.assertEqual(hardware.read_duties, [0, 0, 0, 100])
        self.assertEqual(hardware.cycle_duties, [100, 0])
        self.assertTrue(heater.state['recovering'])

    def test_overtemperature_still_latches_fault(self):
        hardware, heater = self.run_sampling((111, 23))
        self.assertEqual(hardware.cycle_duties, [100, 0])
        self.assertIn('cutoff', heater.state['fault'])
