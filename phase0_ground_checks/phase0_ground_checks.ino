// Phase 0: Ground checks for the RGB LED + LDR reservoir
// Board: Arduino Uno R4
// Wiring: LED R/G/B -> D9/D10/D11 (via resistors), LDR divider midpoint -> A0
//         (LDR from 5V to A0, 100 kOhm from A0 to GND)
//
// Four stages, run in order. The sketch pauses before each one and waits for
// you to press Enter in the Serial Monitor (line ending = "Newline") once
// you've done whatever the instructions ask. Output is CSV:
//   stage,t_ms,adc
// Lines starting with '#' are instructions/notes, not data -- when loading
// in Python use comment='#'.
//
// Stages:
//   A_dark        LEDs off, tube covered/light-tight. Noise floor.
//   B_ambient     LEDs off, tube UNCOVERED. You vary room light during this
//                 window (hand shadow, room light on/off, phone torch nearby)
//                 to see if A0 reacts to anything other than the LED.
//   C_short_gnd   Manually jumper the A0/divider node to GND for this window.
//   C_short_5v    Manually jumper the A0/divider node to 5V for this window.
//
// Save the Serial Monitor output (or use a serial logger) as phase0.csv

const uint8_t LED_PIN[3] = {9, 10, 11};
const uint8_t LDR_PIN    = A0;

const uint16_t SAMPLE_MS      = 5;
const uint32_t DARK_DUR_MS    = 30000;  // 30 s
const uint32_t AMBIENT_DUR_MS = 30000;  // 30 s
const uint32_t SHORT_DUR_MS   = 8000;   // 8 s each

void allOff() {
  for (uint8_t i = 0; i < 3; i++) analogWrite(LED_PIN[i], 0);
}

void waitForEnter(const char *prompt) {
  Serial.println();
  Serial.print("# ");
  Serial.println(prompt);
  Serial.println("# Press Enter in the Serial Monitor to begin this stage...");
  while (Serial.available()) Serial.read();   // flush
  while (!Serial.available()) { }
  while (Serial.available()) Serial.read();   // flush the newline
}

void logStage(const char *stageName, uint32_t durationMs) {
  Serial.print("# Logging stage ");
  Serial.print(stageName);
  Serial.print(" for ");
  Serial.print(durationMs / 1000);
  Serial.println(" s...");
  uint32_t t0 = millis();
  uint32_t next = t0;
  while (millis() - t0 < durationMs) {
    Serial.print(stageName);
    Serial.print(',');
    Serial.print(millis() - t0);
    Serial.print(',');
    Serial.println(analogRead(LDR_PIN));
    next += SAMPLE_MS;
    while (millis() < next) { }
  }
  Serial.print("# Stage ");
  Serial.print(stageName);
  Serial.println(" complete.");
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { }
  analogReadResolution(14);   // Uno R4: 14-bit ADC (0-16383)

  for (uint8_t i = 0; i < 3; i++) pinMode(LED_PIN[i], OUTPUT);
  allOff();

  Serial.println("# Phase 0 ground checks starting.");
  Serial.println("stage,t_ms,adc");

  // --- Stage A: dark noise floor ---
  waitForEnter("STAGE A: Make sure all 3 LED channels are off (they are, "
               "programmatically) and the tube is covered / light-tight. "
               "Don't touch the breadboard once logging starts.");
  allOff();
  logStage("A_dark", DARK_DUR_MS);

  // --- Stage B: ambient light leakage ---
  waitForEnter("STAGE B: Uncover the tube. LEDs stay off. During the 30 s "
               "window, vary room light on purpose: toggle the room light, "
               "wave a hand/shadow over the gap, bring a phone torch near "
               "it briefly. Try to note roughly when you did what.");
  allOff();
  logStage("B_ambient", AMBIENT_DUR_MS);

  // --- Stage C: electrical sanity, short to GND ---
  waitForEnter("STAGE C1: Disconnect the LDR momentarily and jumper the "
               "A0/divider node directly to GND (e.g. move the LDR's top "
               "leg out and bridge that breadboard row to the GND rail). "
               "A0 should read near 0.");
  logStage("C_short_gnd", SHORT_DUR_MS);

  // --- Stage C: electrical sanity, short to 5V ---
  waitForEnter("STAGE C2: Now move that jumper from GND to the 5V rail "
               "instead (same node). A0 should read near max (16383).");
  logStage("C_short_5v", SHORT_DUR_MS);

  waitForEnter("STAGE C3 (cleanup): Remove the jumper and reconnect the LDR "
               "exactly as before (LDR leg back into its original row). "
               "Press Enter once it's restored -- no logging happens here, "
               "this just confirms you're done.");

  Serial.println("# DONE. Copy everything from 'stage,t_ms,adc' onward into phase0.csv");
}

void loop() { }
