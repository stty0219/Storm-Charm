// Storm Charm bring-up: read BME280 over I2C (address 0x76) in forced mode.
// Needs the "Adafruit BME280 Library" (plus its dependencies) from the Library Manager.
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {}   // wait up to 3 s for the serial monitor

  if (!bme.begin(0x76)) {                 // library default is 0x77; ours is 0x76
    Serial.println("BME280 not found at 0x76");
    while (true) delay(1000);
  }

  // Forced mode, 1x oversampling, no filter: one measurement per request,
  // which is how the real firmware will sample (low power, low self-heating).
  bme.setSampling(Adafruit_BME280::MODE_FORCED,
                  Adafruit_BME280::SAMPLING_X1,   // temperature
                  Adafruit_BME280::SAMPLING_X1,   // pressure
                  Adafruit_BME280::SAMPLING_X1,   // humidity
                  Adafruit_BME280::FILTER_OFF);

  Serial.println("ms,temperature_C,humidity_pct,pressure_hPa");
}

void loop() {
  bme.takeForcedMeasurement();
  float t = bme.readTemperature();        // degC
  float h = bme.readHumidity();           // %RH
  float p = bme.readPressure();           // Pa

  Serial.print(millis());
  Serial.print(',');
  Serial.print(t, 2);
  Serial.print(',');
  Serial.print(h, 2);
  Serial.print(',');
  Serial.println(p / 100.0f, 2);

  delay(2000);
}
