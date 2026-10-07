// LDR step-response test for the RGB LED + photoresistor reservoir
// Board: Arduino Uno R4
// Wiring: LED R/G/B -> D9/D10/D11 (via resistors), LDR divider midpoint -> A0
//
// For each colour channel the sketch applies a sequence of PWM steps and logs
// the LDR reading every SAMPLE_MS. Output is CSV over Serial:
//   channel,step,t_ms,pwm,adc
// where t_ms is time since that step was applied (t=0 is the pre-step value).
//
// Capture the output from the Serial Monitor (copy into a .csv) or with a
// serial logger, then fit exponentials to the rise/decay segments.

const uint8_t LED_PIN[3] = {9, 10, 11};
const char*   CH_NAME[3] = {"R", "G", "B"};
const uint8_t LDR_PIN    = A0;

const uint16_t SAMPLE_MS = 5;      // sampling period
const uint16_t HOLD_MS   = 4000;   // how long each step is held
const uint16_t SETTLE_MS = 5000;   // dark pre-conditioning before each channel

// Sequence of PWM levels applied to each channel (0-255)
const uint8_t LEVELS[] = {255, 0, 64, 0, 128, 0};
const uint8_t N_LEVELS = sizeof(LEVELS) / sizeof(LEVELS[0]);

void setChannel(uint8_t ch, uint8_t value) {
  for (uint8_t i = 0; i < 3; i++) {
    analogWrite(LED_PIN[i], (i == ch) ? value : 0);
  }
}

int readLDR() {
  long sum = 0;
  for (uint8_t i = 0; i < 4; i++) sum += analogRead(LDR_PIN);
  return sum / 4;
}

void holdAndLog(uint8_t ch, uint8_t stepIdx, uint8_t level, uint16_t duration) {
  setChannel(ch, level);
  uint32_t t0   = millis();
  uint32_t next = t0;
  while (millis() - t0 < duration) {
    Serial.print(CH_NAME[ch]);
    Serial.print(',');
    Serial.print(stepIdx);
    Serial.print(',');
    Serial.print(millis() - t0);
    Serial.print(',');
    Serial.print(level);
    Serial.print(',');
    Serial.println(readLDR());
    next += SAMPLE_MS;
    while (millis() < next) { }
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { }
  analogReadResolution(14);   // Uno R4: 14-bit ADC (0-16383)

  for (uint8_t i = 0; i < 3; i++) pinMode(LED_PIN[i], OUTPUT);
  setChannel(0, 0);

  Serial.println("# LDR step test starting in 3 s");
  delay(3000);
  Serial.println("channel,step,t_ms,pwm,adc");

  for (uint8_t ch = 0; ch < 3; ch++) {
    // Pre-condition: hold the LED off so every channel starts from the same state
    setChannel(ch, 0);
    delay(SETTLE_MS);

    for (uint8_t s = 0; s < N_LEVELS; s++) {
      holdAndLog(ch, s, LEVELS[s], HOLD_MS);
    }
  }

  setChannel(0, 0);
  Serial.println("# DONE");
}

void loop() { }
