// Phase 1: Baseline check -- is the system driven by light at all,
// independent of colour, and does it respond monotonically to brightness?
// Board: Arduino Uno R4
// Wiring: LED R/G/B -> D9/D10/D11 (via resistors), LDR divider midpoint -> A0
//         (LDR from 5V to A0, 100 kOhm from A0 to GND), sealed tube around LED+LDR
//
// Two stages:
//   onoff  - all 3 channels driven together at full brightness (white-ish),
//            alternated ON/OFF for N_CYCLES, to confirm the LDR reacts to
//            combined light at all and recovers the same way each time.
//   sweep  - all 3 channels together swept through PWM levels
//            0,64,128,192,255,192,128,64,0 to check the plateau rises
//            monotonically with brightness (and returns the same way
//            going back down).
//
// Output is CSV over Serial: stage,idx,pwm,t_ms,adc
// Capture the Serial Monitor output into phase1.csv

const uint8_t  LED_PIN[3] = {9, 10, 11};
const uint8_t  LDR_PIN    = A0;

const uint16_t SAMPLE_MS = 20;
const uint16_t HOLD_MS   = 4000;
const uint16_t SETTLE_MS = 5000;
const uint8_t  N_CYCLES  = 5;

const uint8_t SWEEP_LEVELS[] = {0, 64, 128, 192, 255, 192, 128, 64, 0};
const uint8_t N_SWEEP = sizeof(SWEEP_LEVELS) / sizeof(SWEEP_LEVELS[0]);

void setAll(uint8_t value) {
  for (uint8_t i = 0; i < 3; i++) analogWrite(LED_PIN[i], value);
}

int readLDR() {
  long sum = 0;
  for (uint8_t i = 0; i < 4; i++) sum += analogRead(LDR_PIN);
  return sum / 4;
}

void holdAndLog(const char* stage, uint8_t idx, uint8_t level, uint16_t duration) {
  setAll(level);
  uint32_t t0 = millis();
  uint32_t next = t0;
  char buf[64];
  while (millis() - t0 < duration) {
    uint32_t elapsed = millis() - t0;
    int adcVal = readLDR();
    int len = snprintf(buf, sizeof(buf), "%s,%u,%u,%lu,%d\r\n",
                        stage, idx, level, (unsigned long)elapsed, adcVal);
    if (len > 0) Serial.write((uint8_t*)buf, len);
    next += SAMPLE_MS;
    uint32_t now = millis();
    if (now < next) delay(next - now);
    else { next = now; yield(); }
  }
  Serial.flush();
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { }
  analogReadResolution(14);
  for (uint8_t i = 0; i < 3; i++) pinMode(LED_PIN[i], OUTPUT);
  setAll(0);

  Serial.println("# Phase 1: baseline white-light on/off + PWM sweep. Starting in 3 s.");
  delay(3000);
  Serial.println("stage,idx,pwm,t_ms,adc");

  // Stage 1: on/off cycles
  setAll(0);
  delay(SETTLE_MS);
  for (uint8_t c = 0; c < N_CYCLES; c++) {
    holdAndLog("onoff", c, 255, HOLD_MS);
    holdAndLog("onoff", c, 0, HOLD_MS);
  }

  // Stage 2: brightness sweep, up then back down
  setAll(0);
  delay(SETTLE_MS);
  for (uint8_t s = 0; s < N_SWEEP; s++) {
    holdAndLog("sweep", s, SWEEP_LEVELS[s], HOLD_MS);
  }

  setAll(0);
  Serial.println("# DONE");
}

void loop() { }
