// tombola audio core: the 48 kHz stereo mix for play --tombola.
//
// Ball strikes go through the resonator and the grain layer through the
// granular processor from Emilie Gillet's open-source eurorack code (MIT,
// vendored under native/vendor, see NOTICE). This file is the glue: a C API
// for Python (ctypes), the ring-chord voices, played-back note buffers, the
// 48k <-> 32k resampling around the granular processor and the master reverb.
//
// Threading: Python calls tc_set_params / tc_strike / tc_play / tc_set_rings
// from its main thread and tc_render from its audio thread. Those calls only
// touch a mutex-guarded staging area; tc_render applies it at block start.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <vector>
#include <pmmintrin.h>
#include <xmmintrin.h>

#include "clouds/dsp/granular_processor.h"   // declares the frame types reverb.h needs
#include "clouds/dsp/fx/reverb.h"
#include "elements/dsp/part.h"
#include "marbles/random/random_generator.h"
#include "marbles/random/random_sequence.h"
#include "marbles/random/random_stream.h"
#include "rings/dsp/part.h"
#include "stmlib/utils/buffer_allocator.h"

#include "drums.h"
#include "mods.h"
#include "scatter.h"
#include "synth_voice.h"
#include "warp.h"

namespace {

const int kRate = 48000;
const int kChunk = 48;                 // 48 frames @48k = 2 resonator blocks = 32 frames @32k
const int kResonatorBlock = 24;
const int kCloudsBlock = 32;
const int kTable = 2048;
const int kMaxVoices = 24;
const float kResonatorGain = 4.0f;     // the resonator's output sits well below full scale
const float kCloudsDrive = 3.0f;       // like the module's input gain: feed the buffer properly
const float kFeedbackMax = 0.7f;       // from ~0.49 dense clouds sustain forever: the top of FEEDBACK is an endless bed
const float kCloudsOut = 4.0f;         // wet makeup: grains sit with (or over) the dry sound, as on the module
// Soft ceiling for the wet grains (unity for small signals, never past +-1.5): loud, never harsh.
inline float wet_limit(float x) { return 1.5f * std::tanh(x / 1.5f); }

struct Params {                        // mirrors TcParams in tombola/native.py
  float volume, reverb, ring_level;
  int ring_timbre;
  int res_model, res_poly;
  float res_structure, res_brightness, res_damping, res_position;
  float grain_level, grain_density, grain_size, grain_position, grain_pitch;
  float grain_spread, grain_texture, grain_feedback;
  int grain_freeze, grain_source, grain_mode;
  // synth voice for the balls (24 engines) and for the ring chord (ring_engine < 0: built-in timbres)
  int synth_engine;
  float synth_harmonics, synth_timbre, synth_morph, synth_decay, synth_colour;
  int ring_engine;
  float ring_harmonics, ring_timbre2, ring_morph;
  // physical-modelling voice for the balls: exciter 0 strike, 1 bow, 2 blow, 3 all
  int phys_exciter;
  float phys_geometry, phys_brightness, phys_damping, phys_position, phys_space;
  // WARP: carrier 0 rings, 1 drums, 2 sine, 3 tri, 4 saw (at warp_note); mix 0 = off
  int warp_carrier;
  float warp_algo, warp_timbre, warp_drive, warp_mix, warp_note;
  // DRUMS
  int drum_play;
  float drum_bpm, drum_x, drum_y, drum_chaos, drum_density[3], drum_level, drum_tone, drum_decay, drum_note;
  // MODS: output mode 0 phase, 1 frequency, 2 amplitude; lane targets index kModTargets
  int mod_mode;
  float mod_rate, mod_shape, mod_slope, mod_smooth, mod_shift;
  int mod_target[4];
  float mod_depth[4];
  // Master clock: bpm, and each tab's ratio to it (x4 = 4, /2 = 0.5). Hits wait for the next
  // 1/quant beat of the balls' clock (quant 0 = play at once). mod_cycle: beats per MODS cycle
  // when locked (0 = free-running at mod_rate).
  float bpm, ball_ratio, drum_ratio, mod_ratio;
  int quant;
  float mod_cycle;
  // Tab on/off, each faded (no clicks): balls, rings, grains, drums, warp, radio.
  float bus_on[6];
  float radio_level;
  int radio_paused;                    // 1: hold the radio where it is (nothing is read)
  float grain_wash;                    // the grain engine's own reverb, 0..1 (WASH)
};

const int kRadioFrames = 48000 * 4;   // 4 s ring buffer the radio feeder thread fills

enum { kBusBalls, kBusRings, kBusGrains, kBusDrums, kBusWarp, kBusRadio, kBuses };

const int kScatterMode = 4;            // grain_mode index of SCATTER (after the four cloud modes)

// MODS targets, in the order of MOD_TARGETS in tombola/params.py (0 = off). Each is a
// 0..1 setting a lane sweeps by up to +-depth; DENSITY runs -1..1, so it gets twice the swing.
float* mod_slot(Params& p, int target) {
  switch (target) {
    case 1: return &p.grain_position;   case 2: return &p.grain_size;
    case 3: return &p.grain_density;    case 4: return &p.grain_texture;
    case 5: return &p.grain_level;      case 6: return &p.synth_timbre;
    case 7: return &p.synth_harmonics;  case 8: return &p.synth_morph;
    case 9: return &p.res_brightness;   case 10: return &p.res_structure;
    case 11: return &p.res_position;    case 12: return &p.phys_geometry;
    case 13: return &p.phys_brightness; case 14: return &p.ring_timbre2;
    case 15: return &p.ring_morph;      case 16: return &p.ring_level;
    case 17: return &p.reverb;          case 18: return &p.warp_algo;
    case 19: return &p.warp_timbre;     case 20: return &p.drum_x;
    case 21: return &p.drum_y;          case 22: return &p.drum_chaos;
    default: return nullptr;            // 0 off; 23 TILT is applied by Python (it moves the rings)
  }
}

struct Hit { float note, strength; };

// 32 kHz -> 48 kHz linear upsampler (stereo), fed one sample at a time.
struct Upsampler {
  float pos = 0, prev_l = 0, prev_r = 0;
  template <typename F>
  void run(const float* in_l, const float* in_r, int n_in, float* out_l, float* out_r, int n_out, F add) {
    int k = 0;
    for (int i = 0; i < n_out; ++i) {
      float cl = in_l[std::min(k, n_in - 1)], cr = in_r[std::min(k, n_in - 1)];
      add(out_l[i], prev_l + (cl - prev_l) * pos);
      add(out_r[i], prev_r + (cr - prev_r) * pos);
      pos += 2.0f / 3.0f;
      if (pos >= 1.0f) { pos -= 1.0f; prev_l = cl; prev_r = cr; ++k; }
    }
  }
};

// Sustained ring-chord timbres: (ratio, amplitude) harmonic sets.
const float kTimbres[5][8][2] = {
  {{1, 1.0f}, {2, 0.45f}, {3, 0.2f}},                                             // WARM
  {{1, 1.0f}, {2, 0.2f}},                                                          // GLASS
  {{1, 1.0f}},                                                                     // SINE
  {{1, 1.0f}, {2, 0.6f}, {3, 0.4f}, {4, 0.25f}, {6, 0.15f}},                       // ORGAN
  {{1, 1.0f}, {2, 0.5f}, {3, 0.333f}, {4, 0.25f}, {5, 0.2f}, {6, 0.167f},
   {7, 0.143f}, {8, 0.125f}},                                                      // SAW
};

struct Table { std::vector<float> data; };

// One cycle of a timbre with only the harmonics below max_ratio (no aliasing).
const float* timbre_table(int timbre, int max_ratio) {
  static Table tables[5][9];
  max_ratio = std::max(1, std::min(8, max_ratio));
  Table& t = tables[timbre][max_ratio];
  if (t.data.empty()) {
    t.data.assign(kTable, 0.0f);
    float peak = 0.0f;
    for (int i = 0; i < kTable; ++i) {
      float x = 0.0f;
      for (int h = 0; h < 8; ++h) {
        float r = kTimbres[timbre][h][0], a = kTimbres[timbre][h][1];
        if (r <= 0 || r > max_ratio) continue;
        x += a * std::sin(2.0f * float(M_PI) * r * i / kTable);
      }
      t.data[i] = x;
      peak = std::max(peak, std::fabs(x));
    }
    for (float& x : t.data) x /= peak;
  }
  return t.data.data();
}

struct RingVoice {                     // one chord note: two detuned table oscillators
  float freq = 0, target = 0, p1 = 0, p2 = 0, gain = 0, goal = 0;
  void render(float* l, float* r, int n, int timbre, float scale, float pan) {
    freq += (target - freq) * 0.15f;                              // ~glide on chord changes
    const float* t = timbre_table(timbre, int(0.45f * kRate / std::max(freq, 1.0f)));
    float s1 = kTable * freq * 0.997f / kRate, s2 = kTable * freq * 1.003f / kRate;
    float g1 = gain + std::max(-0.02f, std::min(0.02f, goal - gain));   // ramped: no zipper noise
    if (gain > 0 || g1 > 0) {
      for (int i = 0; i < n; ++i) {
        float g = scale * (gain + (g1 - gain) * i / n);
        float a = t[int(p1) & (kTable - 1)], b = t[int(p2) & (kTable - 1)];
        p1 += s1; p2 += s2;
        l[i] += g * (a * (1 - pan) + b * pan);
        r[i] += g * (a * pan + b * (1 - pan));
      }
    } else {
      p1 += s1 * n; p2 += s2 * n;
    }
    p1 = std::fmod(p1, float(kTable)); p2 = std::fmod(p2, float(kTable));
    gain = g1;
  }
};

struct Sample {                        // a played-back note buffer (other ball instruments)
  std::vector<float> data;
  size_t pos = 0;
  float gain = 1;
  int group = -1;
  int poly = 0;
  int fade = -1;                       // >= 0: choked, samples left of the fade-out
};

struct Strike { float note, strength; };

float midi_to_freq(float m) { return 440.0f * std::pow(2.0f, (m - 69.0f) / 12.0f); }

struct Engine {
  std::mutex lock;
  // --- staging (written under lock by the Python main thread) ---
  Params staged{};
  std::deque<Strike> strikes;
  std::deque<Sample> incoming;
  float ring_midis[4]{}, ring_gains[4]{};
  int ring_count = 0;
  // --- audio state (audio thread only) ---
  Params p{};
  rings::Part part;
  uint16_t part_reverb[65536];
  float impulse = 0;
  RingVoice ring[4];
  int rings_live = 0;
  std::vector<Sample> samples;
  clouds::GranularProcessor clouds;
  uint8_t clouds_large[118784];
  uint8_t clouds_small[65536 - 128];
  clouds::Reverb reverb;
  uint16_t reverb_buffer[16384];
  float down_pos = 0, up_pos = 0;      // resampler phases
  float down_prev_l = 0, down_prev_r = 0, lp_l = 0, lp_r = 0;
  float up_prev_l = 0, up_prev_r = 0;
  std::vector<float> c_in_l, c_in_r, c_out_l, c_out_r;
  long grains = 0;                     // grain estimate for the visual sparks
  SynthVoice synth[4];                 // ball synth voices, round-robin
  int synth_next = 0;
  std::deque<Hit> synth_hits;
  SynthVoice ring_synth[4];            // ring chord voices when the ring timbre is a synth engine
  elements::Part physical;             // physical-modelling ball voice
  uint16_t physical_reverb[32768];
  std::deque<Hit> physical_hits;
  int physical_gate = 0;               // 32 kHz blocks left with the gate on
  float physical_note = 60, physical_strength = 0;
  Upsampler physical_up;
  float grain_clock = 0;
  Mods mods;
  Drums drums;
  double beats = 0;                    // master clock, in beats since start
  double chunk_from = 0;               // ... at the start of the chunk being rendered
  bool release_hits = true;            // false: quantized hits wait for the next grid line
  float bus[kBuses] = {1, 1, 1, 1, 1, 1};   // faded tab gains
  // RADIO: decoded 48 kHz stereo from a feeder thread. Own lock: the feeder never waits on
  // the engine's, and the audio thread plays silence when the buffer runs dry.
  std::mutex radio_lock;
  std::vector<int16_t> radio_buf = std::vector<int16_t>(2 * kRadioFrames);
  long radio_read = 0, radio_write = 0;     // frame counters (modulo kRadioFrames)

  void render_radio(float* l, float* r) {
    float g = p.radio_level * bus[kBusRadio];
    if (p.radio_paused) return;
    std::lock_guard<std::mutex> lk(radio_lock);
    for (int i = 0; i < kChunk && radio_read < radio_write; ++i, ++radio_read) {
      long at = 2 * (radio_read % kRadioFrames);
      if (g > 0) { l[i] += g * radio_buf[at] / 32768.0f; r[i] += g * radio_buf[at + 1] / 32768.0f; }
    }
  }
  Warp warp;
  Scatter scatter;

  void init() {
    part.Init(part_reverb);
    clouds.Init(clouds_large, sizeof(clouds_large), clouds_small, sizeof(clouds_small));
    clouds.set_num_channels(2);
    clouds.set_low_fidelity(false);
    clouds.set_playback_mode(clouds::PLAYBACK_MODE_GRANULAR);
    clouds.Prepare();
    reverb.Init(reverb_buffer);
    for (auto& v : synth) v.init(false);
    for (auto& v : ring_synth) v.init(true);
    physical.Init(physical_reverb);
    mods.init();
    drums.init();
    warp.init();
    scatter.init();
    // The new processors draw from the shared random generator while starting up: put it back
    // to its initial state so the voices start from the same noise as before they existed.
    stmlib::Random::Seed(0x21);
    reverb.set_diffusion(0.7f);
    reverb.set_lp(0.7f);
    reverb.set_time(0.75f);
    reverb.set_input_gain(0.2f);
  }

  void apply_staging() {
    std::lock_guard<std::mutex> g(lock);
    p = staged;
    for (int k = 0; k < 4; ++k) {
      if (k < ring_count) {
        if (ring[k].freq == 0) ring[k].freq = midi_to_freq(ring_midis[k]);
        ring[k].target = midi_to_freq(ring_midis[k]);
        ring[k].goal = ring_gains[k];
      } else {
        ring[k].goal = 0;
      }
    }
    rings_live = 4;
    ring_count_live = ring_count;
    for (int k = 0; k < ring_count; ++k) { ring_notes_live[k] = ring_midis[k]; ring_gains_live[k] = ring_gains[k]; }
    while (release_hits && !incoming.empty()) {
      Sample s = std::move(incoming.front());
      incoming.pop_front();
      if (s.group >= 0 && s.poly > 0) {                        // choke the oldest beyond poly
        int ringing = 0;
        for (auto& o : samples) ringing += (o.group == s.group && o.fade < 0);
        for (auto& o : samples) {
          if (ringing < s.poly) break;
          if (o.group == s.group && o.fade < 0) { o.fade = 256; --ringing; }
        }
      }
      if (int(samples.size()) >= kMaxVoices) samples.erase(samples.begin());
      samples.push_back(std::move(s));
    }
  }

  // Resonator: one strike per 24-sample block at most (0.5 ms granularity).
  void render_resonator(float* l, float* r) {
    part.set_model(static_cast<rings::ResonatorModel>(
        std::max(0, std::min(int(rings::RESONATOR_MODEL_LAST) - 1, p.res_model))));
    int poly = std::max(1, std::min(4, p.res_poly));
    if (poly != part.polyphony()) part.set_polyphony(poly);   // it reconfigures the voices: only on change
    rings::Patch patch;
    patch.structure = p.res_structure;
    patch.brightness = p.res_brightness;
    patch.damping = p.res_damping;
    patch.position = p.res_position;
    for (int b = 0; b < kChunk; b += kResonatorBlock) {
      rings::PerformanceState ps;
      std::memset(&ps, 0, sizeof ps);
      ps.internal_exciter = false;
      {
        std::lock_guard<std::mutex> g(lock);
        if (release_hits && !strikes.empty()) {
          Strike s = strikes.front();
          strikes.pop_front();
          ps.strum = true;
          ps.note = s.note;
          impulse = s.strength;
          last_note = s.note;
        } else {
          ps.note = last_note;
        }
      }
      float in[kResonatorBlock], out[kResonatorBlock], aux[kResonatorBlock];
      for (int j = 0; j < kResonatorBlock; ++j) { in[j] = impulse; impulse *= 0.99f; }
      part.Process(ps, patch, in, out, aux, kResonatorBlock);
      // With polyphony, odd and even voices come out on separate outputs: blend them so a
      // single strike sounds on both sides while the voices still spread across the field.
      for (int j = 0; j < kResonatorBlock; ++j) {
        l[b + j] += kResonatorGain * (0.7f * out[j] + 0.3f * aux[j]);
        r[b + j] += kResonatorGain * (0.3f * out[j] + 0.7f * aux[j]);
      }
    }
  }
  float last_note = 60;

  // Trigger/gate length for synth hits: short pings, but the DX7-style engines (2-4) use it as
  // a held gate (their envelopes need note-on time), so it follows DECAY there.
  int gate_blocks() const {
    int engine = std::max(0, std::min(23, p.synth_engine));
    if (engine >= 2 && engine <= 4) return 4 + int(p.synth_decay * 160);   // ~2 ms .. 80 ms per 0.5 ms
    return 2;
  }

  void render_synth(float* l, float* r) {
    {
      std::lock_guard<std::mutex> g(lock);
      while (release_hits && !synth_hits.empty()) {
        Hit h = synth_hits.front();
        synth_hits.pop_front();
        SynthVoice& v = synth[synth_next];
        synth_next = (synth_next + 1) % 4;
        v.patch.note = h.note;
        v.gain = 0.9f * h.strength;
        v.trigger = gate_blocks();
      }
    }
    for (int k = 0; k < 4; ++k) {
      SynthVoice& v = synth[k];
      v.patch.engine = std::max(0, std::min(23, p.synth_engine));
      v.patch.harmonics = p.synth_harmonics; v.patch.timbre = p.synth_timbre; v.patch.morph = p.synth_morph;
      v.patch.decay = p.synth_decay; v.patch.lpg_colour = p.synth_colour;
      for (int b = 0; b < kChunk; b += 24) v.render(l + b, r + b, 24, 0.25f + 0.17f * k);
    }
  }

  void render_ring_synth(float* l, float* r) {
    for (int k = 0; k < 4; ++k) {
      SynthVoice& v = ring_synth[k];
      bool live = k < ring_count_live;
      v.patch.engine = std::max(0, std::min(23, p.ring_engine));
      v.patch.note = live ? ring_notes_live[k] : v.patch.note;
      v.patch.harmonics = p.ring_harmonics; v.patch.timbre = p.ring_timbre2; v.patch.morph = p.ring_morph;
      v.patch.decay = 0.5f; v.patch.lpg_colour = 0.5f;
      float target = live ? ring_gains_live[k] : 0.0f;
      v.mod.level += (target - v.mod.level) * 0.2f;           // smooth: no zipper on the gate
      v.gain = 0.6f * p.ring_level;
      for (int b = 0; b < kChunk; b += 24) v.render(l + b, r + b, 24, 0.2f + 0.2f * k);
    }
  }
  int ring_count_live = 0;
  float ring_notes_live[4]{}, ring_gains_live[4]{};

  void render_physical(float* l, float* r) {
    elements::Patch* ep = physical.mutable_patch();
    int ex = std::max(0, std::min(3, p.phys_exciter));
    ep->exciter_envelope_shape = ex == 0 ? 0.0f : 0.6f;
    ep->exciter_strike_level = (ex == 0 || ex == 3) ? 0.8f : 0.0f;
    ep->exciter_bow_level = (ex == 1 || ex == 3) ? 0.8f : 0.0f;
    ep->exciter_blow_level = (ex == 2 || ex == 3) ? 0.8f : 0.0f;
    ep->exciter_strike_meta = 0.5f; ep->exciter_strike_timbre = 0.5f;
    ep->exciter_bow_timbre = 0.5f; ep->exciter_blow_meta = 0.5f; ep->exciter_blow_timbre = 0.5f;
    ep->exciter_signature = 0.0f;
    ep->resonator_geometry = p.phys_geometry;
    ep->resonator_brightness = p.phys_brightness;
    ep->resonator_damping = p.phys_damping;
    ep->resonator_position = p.phys_position;
    ep->resonator_modulation_frequency = 0.0f;
    ep->resonator_modulation_offset = 0.0f;
    ep->reverb_diffusion = 0.7f; ep->reverb_lp = 0.7f;
    ep->space = p.phys_space;
    ep->modulation_frequency = 0.0f;
    {
      std::lock_guard<std::mutex> g(lock);
      if (release_hits && !physical_hits.empty()) {
        Hit h = physical_hits.front();
        physical_hits.pop_front();
        physical_note = h.note;
        physical_strength = h.strength;
        physical_gate = ex == 0 ? 6 : 1600;                   // 16-sample blocks: strike 3 ms, bow/blow 0.8 s
      }
    }
    const int kBlock32 = 16;
    float main32[32], aux32[32], silence[kBlock32]{};
    for (int b = 0; b < 32; b += kBlock32) {
      elements::PerformanceState ps;
      ps.gate = physical_gate > 0;
      ps.note = physical_note;
      ps.modulation = 0.0f;
      ps.strength = physical_strength;
      if (physical_gate > 0) --physical_gate;
      physical.Process(ps, silence, silence, main32 + b, aux32 + b, kBlock32);
    }
    static const float kExciterGain[4] = {2.5f, 0.6f, 0.1f, 0.1f};   // measured: even levels out
    const float g = kExciterGain[ex];
    physical_up.run(main32, aux32, 32, l, r, kChunk, [g](float& o, float x) { o += g * x; });
  }

  // WARP: the balls modulate the carrier; MIX crossfades both into the warped sound.
  void render_warp(float* bl, float* br, float* rl, float* rr, float* dl, float* dr) {
    if (p.warp_mix <= 0) return;
    int kind = std::max(0, std::min(4, p.warp_carrier));
    float* cl = kind == 1 ? dl : rl;
    float* cr = kind == 1 ? dr : rr;
    float carrier[kChunk], mod[kChunk], warped[kChunk];
    for (int i = 0; i < kChunk; ++i) { carrier[i] = 0.5f * (cl[i] + cr[i]); mod[i] = 0.5f * (bl[i] + br[i]); }
    warp.render(kind, carrier, mod, warped, kChunk, p.warp_algo, p.warp_timbre, p.warp_drive, p.warp_note);
    float dry = 1.0f - p.warp_mix;
    for (int i = 0; i < kChunk; ++i) {
      bl[i] = dry * bl[i] + p.warp_mix * warped[i];
      br[i] = dry * br[i] + p.warp_mix * warped[i];
      if (kind <= 1) { cl[i] *= dry; cr[i] *= dry; }            // an internal carrier has no dry part
    }
  }

  void render_samples(float* l, float* r) {
    for (auto& s : samples) {
      for (int i = 0; i < kChunk && s.pos < s.data.size(); ++i, ++s.pos) {
        float g = s.gain;
        if (s.fade >= 0) { g *= std::max(0.0f, s.fade / 256.0f); s.fade = std::max(0, s.fade - 1); }
        float x = s.data[s.pos] * g;
        l[i] += x; r[i] += x;
      }
    }
    samples.erase(std::remove_if(samples.begin(), samples.end(), [](const Sample& s) {
      return s.pos >= s.data.size() || s.fade == 0;
    }), samples.end());
  }

  // 48k stereo source -> granular processor at 32 kHz -> back to 48k.
  void render_clouds(const float* sl, const float* sr, float* l, float* r) {
    auto* cp = clouds.mutable_parameters();
    cp->position = p.grain_position;
    cp->size = p.grain_size;
    cp->pitch = p.grain_pitch;
    cp->density = 0.5f + 0.5f * p.grain_density;          // <0.5 steady, 0.5 none, >0.5 random
    cp->texture = p.grain_texture;
    cp->dry_wet = 1.0f;                                    // wet only; dry stays in our mix
    cp->stereo_spread = p.grain_spread;
    cp->feedback = kFeedbackMax * p.grain_feedback;
    cp->reverb = p.grain_wash;                             // the module's own cloud reverb: the pad wash
    cp->freeze = p.grain_freeze != 0;
    cp->trigger = false;
    cp->gate = false;
    clouds.set_playback_mode(static_cast<clouds::PlaybackMode>(
        std::max(0, std::min(int(clouds::PLAYBACK_MODE_LAST) - 1, p.grain_mode))));

    // Downsample 48 -> 32 kHz (one-pole anti-alias, linear interpolation).
    clouds::ShortFrame in[kCloudsBlock], out[kCloudsBlock];
    int o = 0;
    for (int i = 0; i < kChunk && o < kCloudsBlock; ++i) {
      lp_l += 0.6f * (sl[i] - lp_l); lp_r += 0.6f * (sr[i] - lp_r);
      while (down_pos < 1.0f && o < kCloudsBlock) {
        float a = down_pos;
        float xl = down_prev_l + (lp_l - down_prev_l) * a, xr = down_prev_r + (lp_r - down_prev_r) * a;
        in[o].l = int16_t(std::max(-32767.0f, std::min(32767.0f, kCloudsDrive * xl * 32767.0f)));
        in[o].r = int16_t(std::max(-32767.0f, std::min(32767.0f, kCloudsDrive * xr * 32767.0f)));
        ++o;
        down_pos += 1.5f;
      }
      down_pos -= 1.0f;
      down_prev_l = lp_l; down_prev_r = lp_r;
    }
    for (; o < kCloudsBlock; ++o) in[o].l = in[o].r = 0;
    clouds.Process(in, out, kCloudsBlock);
    clouds.Prepare();

    // Upsample 32 -> 48 kHz (linear).
    int k = 0;
    for (int i = 0; i < kChunk; ++i) {
      float cl = out[std::min(k, kCloudsBlock - 1)].l / 32768.0f;
      float cr = out[std::min(k, kCloudsBlock - 1)].r / 32768.0f;
      l[i] += p.grain_level * wet_limit((kCloudsOut / kCloudsDrive) * (up_prev_l + (cl - up_prev_l) * up_pos));
      r[i] += p.grain_level * wet_limit((kCloudsOut / kCloudsDrive) * (up_prev_r + (cr - up_prev_r) * up_pos));
      up_pos += 2.0f / 3.0f;
      if (up_pos >= 1.0f) { up_pos -= 1.0f; up_prev_l = cl; up_prev_r = cr; ++k; }
    }
    float rate = std::fabs(p.grain_density) * 40.0f;          // visual sparks only
    if (p.grain_level > 0) { grain_clock += rate * kChunk / kRate; grains += long(grain_clock); grain_clock -= long(grain_clock); }
  }

  // MODS: one control-rate step, then sweep the targeted settings around their knobs.
  void apply_mods() {
    bool any = false;
    for (int k = 0; k < kModLanes; ++k) any |= p.mod_target[k] > 0 && p.mod_depth[k] != 0;
    if (!any) return;
    float ramp = -1.0f;
    if (p.mod_cycle > 0) {
      double pos = beats * p.mod_ratio / p.mod_cycle;
      ramp = float(pos - std::floor(pos));
    }
    mods.step(p.mod_mode, p.mod_rate, p.mod_shape, p.mod_slope, p.mod_smooth, p.mod_shift, ramp);
    for (int k = 0; k < kModLanes; ++k) {
      float* slot = mod_slot(p, p.mod_target[k]);
      if (!slot) continue;
      bool bipolar = p.mod_target[k] == 3;
      float lo = bipolar ? -1.0f : 0.0f;
      *slot = std::max(lo, std::min(1.0f, *slot + (bipolar ? 2 : 1) * 0.5f * p.mod_depth[k] * mods.out[k]));
    }
  }

  // Advance the master clock one chunk; quantized hits go out when a grid line is crossed.
  void advance_clock() {
    chunk_from = beats;
    beats += double(kChunk) * std::max(1.0f, p.bpm) / (60.0 * kRate);
    if (p.quant <= 0) { release_hits = true; return; }
    double grid = p.quant * std::max(0.01f, p.ball_ratio);          // grid lines per master beat
    release_hits = std::floor(beats * grid) > std::floor(chunk_from * grid);
  }

  void fade_buses() {
    for (int b = 0; b < kBuses; ++b) {
      float target = std::max(0.0f, std::min(1.0f, p.bus_on[b]));
      bus[b] += std::max(-0.05f, std::min(0.05f, target - bus[b]));   // ~20 ms fade
    }
  }

  static void scale(float* l, float* r, float g) {
    if (g == 1.0f) return;
    for (int i = 0; i < kChunk; ++i) { l[i] *= g; r[i] *= g; }
  }

  void render_chunk(int16_t* out) {
    { std::lock_guard<std::mutex> g(lock); p = staged; }
    advance_clock();
    apply_staging();
    fade_buses();
    p.warp_mix *= bus[kBusWarp];
    apply_mods();
    p.grain_level *= bus[kBusGrains];
    float balls_l[kChunk]{}, balls_r[kChunk]{}, rings_l[kChunk]{}, rings_r[kChunk]{};
    render_resonator(balls_l, balls_r);
    render_synth(balls_l, balls_r);
    render_physical(balls_l, balls_r);
    render_samples(balls_l, balls_r);
    if (p.ring_engine >= 0) {
      for (auto& v : ring) v.goal = 0;                         // hand over from the built-in voices
      render_ring_synth(rings_l, rings_r);
    } else {
      for (auto& v : ring_synth) v.mod.level = 0;
    }
    for (int k = 0; k < rings_live; ++k)
      ring[k].render(rings_l, rings_r, kChunk, std::max(0, std::min(4, p.ring_timbre)),
                     0.3f * p.ring_level, 0.2f + 0.2f * k);
    float drums_l[kChunk]{}, drums_r[kChunk]{};
    scale(balls_l, balls_r, bus[kBusBalls]);
    scale(rings_l, rings_r, bus[kBusRings]);
    drums.render(drums_l, drums_r, kChunk, p.drum_play != 0, beats * p.drum_ratio, p.drum_x, p.drum_y,
                 p.drum_chaos, p.drum_density, p.drum_level, p.drum_tone, p.drum_decay, p.drum_note);
    scale(drums_l, drums_r, bus[kBusDrums]);
    render_warp(balls_l, balls_r, rings_l, rings_r, drums_l, drums_r);
    float src_l[kChunk], src_r[kChunk];
    for (int i = 0; i < kChunk; ++i) {        // ALL feeds the grains the drums too
      src_l[i] = (p.grain_source != 2 ? balls_l[i] : 0) + (p.grain_source != 1 ? rings_l[i] : 0)
                 + (p.grain_source == 0 ? drums_l[i] : 0);
      src_r[i] = (p.grain_source != 2 ? balls_r[i] : 0) + (p.grain_source != 1 ? rings_r[i] : 0)
                 + (p.grain_source == 0 ? drums_r[i] : 0);
    }
    float mix_l[kChunk], mix_r[kChunk];
    for (int i = 0; i < kChunk; ++i) {
      mix_l[i] = balls_l[i] + rings_l[i] + drums_l[i];
      mix_r[i] = balls_r[i] + rings_r[i] + drums_r[i];
    }
    if (p.grain_mode == kScatterMode) {
      float wet_l[kChunk]{}, wet_r[kChunk]{};
      if (p.grain_level > 0)
        scatter.render(src_l, src_r, wet_l, wet_r, kChunk, p.grain_density, p.grain_size, p.grain_position,
                       p.grain_texture, p.grain_pitch, p.grain_spread, p.grain_feedback, p.grain_freeze != 0,
                       p.grain_wash);
      for (int i = 0; i < kChunk; ++i) {
        mix_l[i] += p.grain_level * wet_limit(wet_l[i]); mix_r[i] += p.grain_level * wet_limit(wet_r[i]);
      }
      float rate = std::fabs(p.grain_density) * 40.0f;           // visual sparks only
      if (p.grain_level > 0) { grain_clock += rate * kChunk / kRate; grains += long(grain_clock); grain_clock -= long(grain_clock); }
    } else {
      render_clouds(src_l, src_r, mix_l, mix_r);
    }

    clouds::FloatFrame frames[kChunk];
    for (int i = 0; i < kChunk; ++i) { frames[i].l = mix_l[i]; frames[i].r = mix_r[i]; }
    reverb.set_amount(0.6f * p.reverb);
    reverb.Process(frames, kChunk);
    float radio_l[kChunk]{}, radio_r[kChunk]{};
    render_radio(radio_l, radio_r);       // after the reverb: a finished track needs no more room
    for (int i = 0; i < kChunk; ++i) { frames[i].l += radio_l[i]; frames[i].r += radio_r[i]; }
    for (int i = 0; i < kChunk; ++i) {
      out[2 * i] = int16_t(32000.0f * std::tanh(p.volume * frames[i].l));
      out[2 * i + 1] = int16_t(32000.0f * std::tanh(p.volume * frames[i].r));
    }
  }
};

}  // namespace

extern "C" {

void* tc_create() {
  Engine* e = new Engine();
  e->init();
  return e;
}

void tc_destroy(void* h) { delete static_cast<Engine*>(h); }

void tc_set_params(void* h, const Params* params) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  e->staged = *params;
}

void tc_strike(void* h, float note, float strength) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  if (e->strikes.size() < 16) e->strikes.push_back({note, strength});
}

// Play a mono float buffer (48 kHz). group >= 0 with poly > 0: at most poly
// voices of that group ring; the oldest is choked with a short fade.
void tc_play(void* h, const float* data, int n, float gain, int group, int poly) {
  Engine* e = static_cast<Engine*>(h);
  Sample s;
  s.data.assign(data, data + n);
  s.gain = gain;
  s.group = group;
  s.poly = poly;
  std::lock_guard<std::mutex> g(e->lock);
  e->incoming.push_back(std::move(s));
}

void tc_set_rings(void* h, int count, const float* midis, const float* gains) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  e->ring_count = std::max(0, std::min(4, count));
  for (int k = 0; k < e->ring_count; ++k) { e->ring_midis[k] = midis[k]; e->ring_gains[k] = gains[k]; }
}

// Render frames (multiple of 48) of interleaved stereo s16. Releases nothing
// itself; ctypes.CDLL drops the Python interpreter lock around this call.
int tc_render(void* h, int16_t* out, int frames) {
  // Flush denormals to zero, as the module's ARM chip does: decaying filters otherwise
  // produce tiny numbers that x86 handles very slowly. Per call: the setting is per thread.
  _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
  _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
  Engine* e = static_cast<Engine*>(h);
  int done = 0;
  for (; done + kChunk <= frames; done += kChunk) e->render_chunk(out + 2 * done);
  return done;
}

void tc_synth_hit(void* h, float note, float strength) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  if (e->synth_hits.size() < 16) e->synth_hits.push_back({note, strength});
}

void tc_physical_hit(void* h, float note, float strength) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  if (e->physical_hits.size() < 4) e->physical_hits.push_back({note, strength});
}

// The random-loop source for the RANDOM chord mode: a deja-vu random sequence.
struct Sequence {
  marbles::RandomGenerator generator;
  marbles::RandomStream stream;
  marbles::RandomSequence sequence;
};

void* tc_seq_create(unsigned seed) {
  Sequence* s = new Sequence();
  s->generator.Init(seed);
  s->stream.Init(&s->generator);
  s->sequence.Init(&s->stream);
  return s;
}

void tc_seq_destroy(void* h) { delete static_cast<Sequence*>(h); }

// Next value in [0, 1): deja_vu 0 = always new, 0.5 = locked loop, 1 = shuffled loop.
float tc_seq_next(void* h, float deja_vu, int length) {
  Sequence* s = static_cast<Sequence*>(h);
  s->sequence.set_deja_vu(deja_vu);
  s->sequence.set_length(length);
  return s->sequence.NextValue(false, 0.0f);
}

// RADIO: queue decoded 48 kHz stereo s16 frames; returns how many fit (0 = full, try later).
int tc_radio_push(void* h, const int16_t* data, int frames) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> lk(e->radio_lock);
  long room = kRadioFrames - (e->radio_write - e->radio_read);
  int n = int(std::min<long>(room, frames));
  for (int i = 0; i < n; ++i, ++e->radio_write) {
    long at = 2 * (e->radio_write % kRadioFrames);
    e->radio_buf[at] = data[2 * i];
    e->radio_buf[at + 1] = data[2 * i + 1];
  }
  return n;
}

// Drop whatever is queued (a new track, or the radio stopped).
void tc_radio_clear(void* h) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> lk(e->radio_lock);
  e->radio_read = e->radio_write;
}

// Seconds of radio audio queued.
float tc_radio_queued(void* h) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> lk(e->radio_lock);
  return float(e->radio_write - e->radio_read) / kRate;
}

// The master clock, in beats since start.
double tc_beats(void* h) { return static_cast<Engine*>(h)->beats; }

// The four MODS lanes' latest values (-1..1), for the meters and for TILT.
void tc_mods(void* h, float* out) {
  Engine* e = static_cast<Engine*>(h);
  for (int k = 0; k < kModLanes; ++k) out[k] = e->mods.out[k];
}

// DRUMS: the step playing now (0..31), and the map's levels at x, y (3 parts x 32 steps).
int tc_drum_step(void* h) { return static_cast<Engine*>(h)->drums.step; }

void tc_drum_levels(void* h, float x, float y, uint8_t* out) {
  Engine* e = static_cast<Engine*>(h);
  for (int part = 0; part < kDrumParts; ++part)
    for (int s = 0; s < kDrumSteps; ++s) out[part * kDrumSteps + s] = e->drums.map.read(part, s, x, y);
}

long tc_grains(void* h) { return static_cast<Engine*>(h)->grains; }

int tc_voices(void* h) { return int(static_cast<Engine*>(h)->samples.size()); }

}  // extern "C"
