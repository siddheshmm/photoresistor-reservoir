// Phases 3 and 4: single-node, time-multiplexed physical reservoir
// RGB LED (input, optional feedback) -> photoresistor (the physical node)
// Board: Arduino Uno R4
// Wiring: LED R/G/B -> D9/D10/D11 (via 1 kOhm resistors), LDR divider midpoint -> A0
//         (LDR from 5V to A0, 100 kOhm from A0 to GND), LED + LDR inside the sealed tube.
//
// HOW IT IS USED
//   Phase 3 = this sketch with fb=0 (no feedback).
//   Phase 4 = the same sketch with fb>0 (red LED driven by the node's own earlier state).
//   Every run is configured from the Serial Monitor, so there is no editing or
//   re-uploading between runs. After upload, set the Serial Monitor to
//   115200 baud and line ending "Newline", then send ONE line, for example:
//
//       mode=0 seed=1 init=0 in=2 fb=0 tag=blue_s1_dark
//
//   A blank line (just Enter) uses the defaults below. Keys:
//       mode   0 = reservoir run (default), 1 = operating-point sweep of one channel
//       seed   random seed of the input stream (default 1). Same seed = same inputs.
//       init   0 = start from dark (default), 1 = start from full brightness (for the
//              echo-state check: two different starting states, same inputs)
//       in     input channel: 0=R(D9), 1=G(D10), 2=B(D11) (default 2)
//       fb     feedback strength in PWM units on the red LED (default 0 = off)
//       inv    0 = positive feedback (default), 1 = inverted feedback
//       steps  number of input samples (default 1000)
//       nodes  virtual nodes per input sample (default 20, max 64)
//       bias, amp   input drive = bias +/- amp (PWM units). PROVISIONAL defaults below;
//              replace them with values chosen from the mode=1 sweep.
//       xlo, xhi    reading range used to scale the node state to 0..1 for feedback
//              (PROVISIONAL defaults; Phase 3 analysis prints the values to use)
//       tag    free-text label (max 15 chars) echoed into the CSV header
//
// SCHEME (per input sample u[k] in 0..1, spread over N virtual-node slots of THETA_MS):
//   input drive in slot i : pwm_in = bias + amp * mask[i] * (2*u[k] - 1)     mask[i] = +1 or -1
//   feedback drive        : pwm_fb = fb * x_prev[i]       (x_prev[i] = scaled reading of the same
//                           virtual node one input sample ago, 0..1; delay = nodes * THETA_MS)
//   state of node i       : A0 reading averaged over the last WINDOW_US of the slot
//
// OUTPUT (CSV over Serial):  k,i,u,pwm_in,pwm_fb,adc
//   mode 0: k = input sample, i = virtual node, u = input in 0..1
//   mode 1: k = level index, i = sample within the level (150 ON, then 100 OFF), u = 0
// Lines starting with '#' are comments (parameters, warnings, summary, DONE).
//
// The mask is generated from MASK_SEED, separate from the input seed, so the same
// mask is used in every run and a readout trained on one run can be tested on another.

#include <Arduino.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_NODES 64

const uint8_t  LED_PIN[3] = {9, 10, 11};   // 0=R, 1=G, 2=B
const uint8_t  LDR_PIN    = A0;
const uint8_t  FB_CH      = 0;             // feedback goes to red
const uint16_t THETA_MS   = 20;            // virtual-node slot length
const uint32_t SLOT_US    = (uint32_t)THETA_MS * 1000UL;
const uint32_t WINDOW_US  = 4000;          // averaging window at the end of each slot (>= 2 PWM periods)
const uint32_t MASK_SEED  = 42;
const uint32_t INIT_MS    = 10000;         // pre-condition time before a run

// PROVISIONAL per-channel drive ranges. Replace using the mode=1 sweep results.
const int DEF_BIAS[3] = {40, 15, 40};
const int DEF_AMP[3]  = {35, 12, 35};

struct Config {
  int  mode    = 0;
  long seed    = 1;
  int  init    = 0;
  int  inCh    = 2;
  int  fbGain  = 0;
  int  fbInv   = 0;
  int  steps   = 1000;
  int  nodes   = 20;
  int  bias    = -1;     // -1 = use channel default
  int  amp     = -1;
  int  xlo     = 8000;   // PROVISIONAL
  int  xhi     = 12500;  // PROVISIONAL
  char tag[16] = "run";
} cfg;

uint32_t overruns  = 0;
int32_t  maxLateUs = 0;

struct Row {
  uint16_t k, i;
  int u1000, pIn, pFb, adc;
  bool valid;
} pending = {0, 0, 0, 0, 0, 0, false};

// ---------------------------------------------------------------- helpers
void allLEDs(int v) {
  for (uint8_t c = 0; c < 3; c++) analogWrite(LED_PIN[c], v);
}

void driveLEDs(int inVal, int fbVal) {
  for (uint8_t c = 0; c < 3; c++) {
    int v = 0;
    if (c == cfg.inCh) v += inVal;
    if (c == FB_CH)    v += fbVal;
    analogWrite(LED_PIN[c], constrain(v, 0, 255));
  }
}

void writePending() {
  if (!pending.valid) return;
  char buf[64];
  int len = snprintf(buf, sizeof(buf), "%d,%d,%d.%03d,%d,%d,%d\r\n",
                     (int)pending.k, (int)pending.i,
                     pending.u1000 / 1000, pending.u1000 % 1000,
                     pending.pIn, pending.pFb, pending.adc);
  if (len > 0) Serial.write((const uint8_t*)buf, len);
  pending.valid = false;
}

// Average the ADC over the last WINDOW_US before endUs, so the LED's PWM ripple averages out.
int readWindow(uint32_t endUs) {
  uint32_t startUs = endUs - WINDOW_US;
  if ((int32_t)(micros() - startUs) > 0) overruns++;      // already inside the window
  while ((int32_t)(micros() - startUs) < 0) { }
  uint32_t sum = 0;
  uint32_t cnt = 0;
  while ((int32_t)(micros() - endUs) < 0) {
    sum += analogRead(LDR_PIN);
    cnt++;
  }
  if (cnt == 0) return analogRead(LDR_PIN);
  return (int)((sum + cnt / 2) / cnt);
}

// One slot: set the LEDs at the slot start, log the previous row, then measure at the slot end.
int runSlot(uint32_t slotEnd, int inVal, int fbVal, bool doFlush) {
  int32_t late = (int32_t)(micros() - (slotEnd - SLOT_US));
  if (late > maxLateUs) maxLateUs = late;
  driveLEDs(inVal, fbVal);
  writePending();
  if (doFlush) Serial.flush();
  return readWindow(slotEnd);
}

bool readLine(char* buf, size_t n) {
  size_t len = 0;
  while (true) {
    while (!Serial.available()) { }
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { buf[len] = 0; return true; }
    if (len < n - 1) buf[len++] = c;
  }
}

void parseConfig(char* line) {
  char* tok = strtok(line, " ,");
  while (tok) {
    char* eq = strchr(tok, '=');
    if (eq) {
      *eq = 0;
      const char* key = tok;
      const char* val = eq + 1;
      if      (!strcmp(key, "mode"))  cfg.mode   = atoi(val);
      else if (!strcmp(key, "seed"))  cfg.seed   = atol(val);
      else if (!strcmp(key, "init"))  cfg.init   = atoi(val);
      else if (!strcmp(key, "in"))    cfg.inCh   = atoi(val);
      else if (!strcmp(key, "fb"))    cfg.fbGain = atoi(val);
      else if (!strcmp(key, "inv"))   cfg.fbInv  = atoi(val);
      else if (!strcmp(key, "steps")) cfg.steps  = atoi(val);
      else if (!strcmp(key, "nodes")) cfg.nodes  = atoi(val);
      else if (!strcmp(key, "bias"))  cfg.bias   = atoi(val);
      else if (!strcmp(key, "amp"))   cfg.amp    = atoi(val);
      else if (!strcmp(key, "xlo"))   cfg.xlo    = atoi(val);
      else if (!strcmp(key, "xhi"))   cfg.xhi    = atoi(val);
      else if (!strcmp(key, "tag"))   { strncpy(cfg.tag, val, sizeof(cfg.tag) - 1); cfg.tag[sizeof(cfg.tag) - 1] = 0; }
      else { Serial.print("# WARNING unknown key: "); Serial.println(key); }
    }
    tok = strtok(NULL, " ,");
  }
}

void finalizeConfig() {
  bool provisional = (cfg.bias < 0 || cfg.amp < 0);
  if (cfg.inCh < 0 || cfg.inCh > 2) cfg.inCh = 2;
  if (cfg.bias < 0) cfg.bias = DEF_BIAS[cfg.inCh];
  if (cfg.amp  < 0) cfg.amp  = DEF_AMP[cfg.inCh];
  if (cfg.nodes < 2) cfg.nodes = 2;
  if (cfg.nodes > MAX_NODES) cfg.nodes = MAX_NODES;
  if (cfg.steps < 1) cfg.steps = 1;
  if (cfg.steps > 65000) cfg.steps = 65000;
  if (cfg.xhi <= cfg.xlo) { cfg.xlo = 8000; cfg.xhi = 12500; Serial.println("# WARNING xhi<=xlo, reset to defaults"); }
  if (cfg.fbGain < 0) cfg.fbGain = 0;
  if (cfg.mode == 0 && provisional) Serial.println("# NOTE bias/amp are PROVISIONAL defaults");
  if (cfg.mode == 0 && cfg.fbGain > 0 && cfg.inCh == FB_CH) Serial.println("# WARNING input channel equals feedback channel (red)");
}

void printParams() {
  char b[220];
  snprintf(b, sizeof(b),
           "# PARAMS tag=%s mode=%d seed=%ld init=%d in=%d fb=%d inv=%d steps=%d nodes=%d theta_ms=%d "
           "bias=%d amp=%d xlo=%d xhi=%d mask_seed=%lu window_us=%lu",
           cfg.tag, cfg.mode, cfg.seed, cfg.init, cfg.inCh, cfg.fbGain, cfg.fbInv, cfg.steps, cfg.nodes,
           (int)THETA_MS, cfg.bias, cfg.amp, cfg.xlo, cfg.xhi,
           (unsigned long)MASK_SEED, (unsigned long)WINDOW_US);
  Serial.println(b);
}

// ---------------------------------------------------------------- run modes
void runReservoir() {
  const int n = cfg.nodes;
  static int8_t mask[MAX_NODES];
  static float  xprev[MAX_NODES];

  randomSeed(MASK_SEED);
  for (int i = 0; i < n; i++) {
    mask[i]  = random(0, 2) ? 1 : -1;
    xprev[i] = 0.0f;
  }
  randomSeed((unsigned long)cfg.seed);

  allLEDs(cfg.init ? 255 : 0);       // choose the starting state of the physical node
  delay(INIT_MS);
  pending.valid = false;

  uint32_t slotEnd = micros() + SLOT_US;
  for (int k = 0; k < cfg.steps; k++) {
    int   u1000 = (int)random(0, 1001);
    float u0    = 2.0f * (u1000 / 1000.0f) - 1.0f;
    for (int i = 0; i < n; i++) {
      float pIn   = cfg.bias + cfg.amp * mask[i] * u0;
      int   inVal = constrain((int)(pIn + 0.5f), 0, 255);
      float x     = xprev[i];
      float pFb   = cfg.fbGain * (cfg.fbInv ? (1.0f - x) : x);
      int   fbVal = constrain((int)(pFb + 0.5f), 0, 255);

      int adc = runSlot(slotEnd, inVal, fbVal, i == 0);

      float xn = (float)(adc - cfg.xlo) / (float)(cfg.xhi - cfg.xlo);
      xprev[i] = constrain(xn, 0.0f, 1.0f);
      pending.k = k; pending.i = i; pending.u1000 = u1000;
      pending.pIn = inVal; pending.pFb = fbVal; pending.adc = adc; pending.valid = true;
      slotEnd += SLOT_US;
    }
  }
  writePending();
  allLEDs(0);
}

void runSweep() {
  const uint8_t  levels[] = {0, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 255};
  const int      nLevels  = sizeof(levels) / sizeof(levels[0]);
  const uint16_t ON_SAMPLES  = 150;     // 3 s on
  const uint16_t OFF_SAMPLES = 100;     // 2 s off

  allLEDs(0);
  delay(5000);
  pending.valid = false;

  uint32_t slotEnd = micros() + SLOT_US;
  for (int li = 0; li < nLevels; li++) {
    for (uint16_t s = 0; s < ON_SAMPLES + OFF_SAMPLES; s++) {
      int val = (s < ON_SAMPLES) ? levels[li] : 0;
      int adc = runSlot(slotEnd, val, 0, s == 0);
      pending.k = li; pending.i = s; pending.u1000 = 0;
      pending.pIn = val; pending.pFb = 0; pending.adc = adc; pending.valid = true;
      slotEnd += SLOT_US;
    }
  }
  writePending();
  allLEDs(0);
}

// ---------------------------------------------------------------- Arduino entry points
void setup() {
  Serial.begin(115200);
  while (!Serial) { }
  analogReadResolution(14);          // Uno R4: 14-bit ADC (0-16383)
  for (uint8_t c = 0; c < 3; c++) pinMode(LED_PIN[c], OUTPUT);
  allLEDs(0);

  Serial.println("# ldr_reservoir ready. Send ONE line then Enter (blank line = defaults).");
  Serial.println("# example: mode=0 seed=1 init=0 in=2 fb=0 tag=blue_s1_dark");
  Serial.println("# example: mode=1 in=2 tag=sweep_blue");
  char line[120];
  readLine(line, sizeof(line));
  parseConfig(line);
  finalizeConfig();
  printParams();
  Serial.println("k,i,u,pwm_in,pwm_fb,adc");
  Serial.flush();
  delay(1000);

  if (cfg.mode == 1) runSweep();
  else               runReservoir();

  char b[96];
  snprintf(b, sizeof(b), "# overruns=%lu max_slot_start_late_us=%ld",
           (unsigned long)overruns, (long)maxLateUs);
  Serial.println(b);
  Serial.println("# DONE");
  Serial.flush();
}

void loop() { }
