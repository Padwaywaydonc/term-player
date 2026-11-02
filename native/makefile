# Builds the tombola audio core (the vendored resonator + granular DSP, MIT).
V        = vendor
CXXFLAGS = -O2 -fPIC -DTEST -msse2 -Wno-unused-variable -Wno-unused-parameter -I$(V)/eurorack -I$(V)
SOURCES  = tombola_core.cc \
           $(V)/eurorack/rings/dsp/fm_voice.cc $(V)/eurorack/rings/dsp/part.cc \
           $(V)/eurorack/rings/dsp/resonator.cc $(V)/eurorack/rings/dsp/string.cc \
           $(V)/eurorack/rings/dsp/string_synth_part.cc $(V)/eurorack/rings/resources.cc \
           $(V)/eurorack/clouds/dsp/correlator.cc $(V)/eurorack/clouds/dsp/granular_processor.cc \
           $(V)/eurorack/clouds/dsp/mu_law.cc $(V)/eurorack/clouds/dsp/pvoc/frame_transformation.cc \
           $(V)/eurorack/clouds/dsp/pvoc/phase_vocoder.cc $(V)/eurorack/clouds/dsp/pvoc/stft.cc \
           $(V)/eurorack/clouds/resources.cc \
           $(V)/stmlib/dsp/atan.cc $(V)/stmlib/dsp/units.cc $(V)/stmlib/utils/random.cc
TARGET   = ../tombola/libtombola_core.so

$(TARGET): $(SOURCES) makefile
	$(CXX) -shared $(CXXFLAGS) $(SOURCES) -o $@

clean:
	rm -f $(TARGET)

.PHONY: clean
