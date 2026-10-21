import json
import tempfile
import unittest
from pathlib import Path
from panel.heating import Heater


class Hardware:
    def __init__(self):
        self.duty = 0

    def set_duty(self, duty):
        self.duty = duty


class StartupPulseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        c = json.loads(Path('config.example.json').read_text())
        c.update(data_dir=self.tmp.name, heater_enabled=True, startup_pulse=True)
        self.now = 10.0
        self.h = Heater(c, None, clock=lambda: self.now)
        self.h.hw = Hardware()
        self.h.sample([(25, 23)] * 3)

    def tearDown(self):
        self.h.stop()
        self.tmp.cleanup()

    def test_high_low_high_then_pid_without_extra_off_gap(self):
        self.h.start(30, 60, dict(kp=8, ki=0, kd=0))
        self.assertEqual(self.h.hw.duty, 0)
        self.assertEqual(self.h.output(), 100)
        self.now = 10.11
        self.assertEqual(self.h.output(), 0)
        self.now = 11.10
        self.h.sample([(25, 23)] * 3)
        self.assertEqual(self.h.output(), 0)
        self.now = 11.12
        self.assertEqual(self.h.output(), 100)
        self.now = 11.23
        self.assertEqual(self.h.output(), 40)
        self.assertIsNone(self.h.startup_phase)
        self.assertEqual(self.h.deadline, 70)

    def test_stop_during_low_cancels_pending_high(self):
        self.h.start(70, 60)
        self.h.output()
        self.now = 10.11
        self.h.output()
        self.h.stop()
        self.now = 11.2
        self.assertEqual(self.h.output(), 0)
        self.assertIsNone(self.h.startup_phase)

    def test_sensor_error_cancels_pulse_and_recovery_uses_pid(self):
        self.h.start(30, 60, dict(kp=8, ki=0, kd=0))
        self.h.output()
        self.h.sensor_error('test sensor error')
        self.assertEqual(self.h.hw.duty, 0)
        self.assertIsNone(self.h.startup_phase)
        self.now = 10.5
        self.h.sample([(25, 23)] * 3)
        self.assertEqual(self.h.output(), 0)
        self.h.sample([(25, 23)] * 3)
        self.assertEqual(self.h.output(), 40)

    def test_overtemperature_immediately_stops_high(self):
        self.h.start(70, 60)
        self.h.output()
        self.h.sample([(111, 23)] * 3)
        self.assertEqual(self.h.hw.duty, 0)
        self.assertEqual(self.h.output(), 0)
        self.assertIsNone(self.h.startup_phase)

    def test_expiry_prevents_second_high(self):
        self.h.start(70, 1)
        self.h.output()
        self.now = 10.11
        self.h.output()
        self.now = 11.12
        self.assertEqual(self.h.output(), 0)
        self.assertFalse(self.h.state['active'])

    def test_stale_feedback_cancels_pulse(self):
        self.h.start(70, 60)
        self.h.output()
        self.now = 12.1
        self.assertEqual(self.h.output(), 0)
        self.assertIsNone(self.h.startup_phase)

    def test_target_already_reached_skips_pulse(self):
        self.h.start(24, 60)
        self.assertIsNone(self.h.startup_phase)
        self.assertEqual(self.h.output(), 0)

    def test_new_run_repeats_sequence(self):
        self.h.start(70, 60)
        self.h.output()
        self.h.stop()
        self.h.start(70, 60)
        self.assertEqual(self.h.startup_phase, 'pending')
        self.assertEqual(self.h.output(), 100)
