// Phase 2: Per-channel effect test, repeated and order-varied.
// Board: Arduino Uno R4
// Wiring: LED R/G/B -> D9/D10/D11 (via resistors), LDR divider midpoint -> A0
//         (LDR from 5V to A0, 100 kOhm from A0 to GND), sealed tube around LED+LDR
//
// Goal (Phase 2 of the protocol): confirm the per-channel LDR response is
// REAL and REPEATABLE, not an artefact of one run's timing/ambient state.
//   - Each channel's step sequence is run N_REPEATS times back-to-back,
//     with a dark settle before each repeat.
//   - The whole thing is run in two different channel orders (R,G,B and
//     B,G,R) to check the result doesn't depend on which channel ran
//     before it (order-independence).
//
// Output is CSV over Serial:
//   order,channel,repeat,step,t_ms,pwm,adc
//
// Capture the Serial Monitor output into a .csv for analysis. Total runtime
// is roughly 2 * 3 channels * N_REPEATS * (SETTLE_MS + 6*HOLD_MS).

const uint8_t LED_PIN[3] = {9, 10, 11};   // index 0=R(D9), 1=G(D10), 2=B(D11)
const char*   CH_NAME[3] = {"R", "G", "B"};
const uint8_t LDR_PIN    = A0;

const uint16_t SAMPLE_MS  = 20;   // 20 ms (50 Hz) keeps total run under 22k rows, preventing Serial Monitor UI/buffer freeze
const uint16_t HOLD_MS    = 4000;   // duration of each step
const uint16_t SETTLE_MS  = 5000;   // dark settle before each repeat
const uint8_t  N_REPEATS  = 3;

// Step sequence applied within one repeat (0-255 PWM)
const uint8_t LEVELS[] = {255, 0, 64, 0, 128, 0};
const uint8_t N_LEVELS = sizeof(LEVELS) / sizeof(LEVELS[0]);

// Two channel orders to test for order-independence
const uint8_t ORDER_A[3] = {0, 1, 2};   // R, G, B
const uint8_t ORDER_B[3] = {2, 1, 0};   // B, G, R

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

void holdAndLog(const char* orderName, uint8_t ch, uint8_t repeat,
                 uint8_t stepIdx, uint8_t level, uint16_t duration) {
  setChannel(ch, level);
  uint32_t t0   = millis();
  uint32_t next = t0;
  char buf[64];

  while (millis() - t0 < duration) {
    uint32_t elapsed = millis() - t0;
    int adcVal = readLDR();

    int len = snprintf(buf, sizeof(buf), "%s,%s,%u,%u,%lu,%u,%d\r\n",
                       orderName, CH_NAME[ch], repeat, stepIdx,
                       (unsigned long)elapsed, level, adcVal);
    if (len > 0) {
      Serial.write((uint8_t*)buf, len);
    }

    next += SAMPLE_MS;
    uint32_t now = millis();
    if (now < next) {
      delay(next - now);
    } else {
      next = now;  // resynchronize if delayed, preventing runaway zero-delay flooding
      yield();
    }
  }
  Serial.flush();
}

void runOrder(const char* orderName, const uint8_t order[3]) {
  for (uint8_t oi = 0; oi < 3; oi++) {
    uint8_t ch = order[oi];
    for (uint8_t r = 0; r < N_REPEATS; r++) {
      setChannel(ch, 0);
      Serial.flush();
      delay(SETTLE_MS);
      for (uint8_t s = 0; s < N_LEVELS; s++) {
        holdAndLog(orderName, ch, r, s, LEVELS[s], HOLD_MS);
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { }
  analogReadResolution(14);   // Uno R4: 14-bit ADC (0-16383)

  for (uint8_t i = 0; i < 3; i++) pinMode(LED_PIN[i], OUTPUT);
  setChannel(0, 0);

  Serial.print("# Phase 2: N_REPEATS=");
  Serial.print(N_REPEATS);
  Serial.println(", two channel orders (A=R,G,B  B=B,G,R). Starting in 3 s.");
  delay(3000);
  Serial.println("order,channel,repeat,step,t_ms,pwm,adc");

  runOrder("A", ORDER_A);
  runOrder("B", ORDER_B);

  setChannel(0, 0);
  Serial.println("# DONE");
}

void loop() { }
