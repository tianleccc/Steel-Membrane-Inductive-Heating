"""MLX90614 SMBus read-word with PEC validation before temperature conversion."""
def crc8(data):
    crc=0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc=((crc << 1) ^ 0x07) & 0xff if crc & 0x80 else (crc << 1) & 0xff
    return crc


def decode_temperature(address, register, data):
    if len(data)!=3 or crc8(bytes([address << 1,register,(address << 1)|1])+bytes(data[:2])) != data[2]:
        raise OSError('MLX90614 PEC checksum mismatch; discarded corrupt temperature')
    value=data[0] | (data[1] << 8)
    if value & 0x8000:
        raise OSError('MLX90614 sensor error bit set; discarded invalid temperature')
    return value*.02-273.15


class CheckedMLX90614:
    def __init__(self, bus, address=0x5a):
        from adafruit_bus_device.i2c_device import I2CDevice
        self.device=I2CDevice(bus,address)
        self.address=address

    def temperature(self, register):
        result=bytearray(3)
        with self.device as device:
            device.write_then_readinto(bytes([register]),result)
        return decode_temperature(self.address,register,result)

    @property
    def object_temperature(self):return self.temperature(0x07)

    @property
    def ambient_temperature(self):return self.temperature(0x06)
