#include <Wire.h>

int readChipId(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0xD0);                          // chip ID register
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(addr, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {}      // wait up to 3 s for the serial monitor
  Wire.begin();                              // default I2C pins (should be D4/D5, check the pinout)
}

void loop() {
  Serial.println("--- I2C scan ---");
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.print("device at 0x");
      Serial.println(a, HEX);
      found++;
    }
  }
  if (!found) Serial.println("no device found");

  const uint8_t addrs[] = {0x76, 0x77};
  for (uint8_t a : addrs) {
    int id = readChipId(a);
    if (id >= 0) {
      Serial.print("0x");
      Serial.print(a, HEX);
      Serial.print(" chip ID = 0x");
      Serial.println(id, HEX);
    }
  }
  delay(2000);
}