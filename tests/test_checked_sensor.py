import unittest
from panel.mlx90614 import crc8,decode_temperature

class CheckedSensorTests(unittest.TestCase):
    def test_manufacturer_read_word_example(self):
        self.assertEqual(crc8(bytes([0xb4,0x07,0xb5,0xd2,0x3a])),0x30)
        self.assertAlmostEqual(decode_temperature(0x5a,7,bytes([0xd2,0x3a,0x30])),28.01)
    def test_corrupted_temperature_is_rejected(self):
        for bit in range(16):
            data=bytearray([0xd2,0x3a,0x30]);data[bit//8]^=1<<(bit%8)
            with self.assertRaises(OSError):decode_temperature(0x5a,7,data)
    def test_sensor_error_bit_is_rejected_even_with_valid_checksum(self):
        data=bytes([0,0x80]);pec=crc8(bytes([0xb4,7,0xb5])+data)
        with self.assertRaises(OSError):decode_temperature(0x5a,7,data+bytes([pec]))
    def test_real_high_temperature_passes_crc_for_cutoff_to_handle(self):
        raw=round((115+273.15)/.02);data=raw.to_bytes(2,'little')
        pec=crc8(bytes([0xb4,7,0xb5])+data)
        self.assertGreater(decode_temperature(0x5a,7,data+bytes([pec])),110)
