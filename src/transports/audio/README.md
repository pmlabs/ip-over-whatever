# Audio Transport
A somewhat simple audio (as in: soundcard line out / line in) modem/transport
by Gynvael Coldwind // Dragon Sector.

It's basically a pretty simple implementation of an FM modem that uses a bunch
of frequencies to send and receive data. Note that it's made for fun, not for
actual in-production use.

If you want to test on real hardware you'll need:

* a sound chipset that supports 44100 Hz mode (which should be any modern soundcard / sound chip)
* line out (speaker out) audio port
* line in audio port
* some audio cables

**Note**: laptops might have a mixed "out+mic" ports, but no "in" ports.

If you want to test this on a single system, just make a pair of virtual
"soundcarads" (i.e. a virtual speaker to which you'll send audio data and
a virtual mic from which you will read the audio that was sent to the virtual
speaker). You can do this like this (assuming you're using Pulse Audio).

```
pactl load-module module-null-sink sink_name=vspeakerA \
      sink_properties=device.description=virtual_speaker_A
pactl load-module module-remap-source master=vspeakerA.monitor \
      source_name=vmicA source_properties=device.description=virtual_mic_A
pactl load-module module-null-sink sink_name=vspeakerB \
      sink_properties=device.description=virtual_speaker_B
pactl load-module module-remap-source master=vspeakerB.monitor \
      source_name=vmicB source_properties=device.description=virtual_mic_B
```

This will basically create virtual audio cables which you can use like this:

```
        tun_A_out.fifo          -> vspeakerA->vmicA ->
       /              \        /                      \
  TUN-A                audio.py                        audio.py ... TUN-B
       \              /        \                      /
        tun_A_in.fifo           <- vmicB<-vspeakerB <-
```

Does this work with speakers / microphone? Maybe, I didn't want to try since
this sounds horrible.

See also:

  * [annotated spectogram for a ping](ping_modulated_audio_spectogram_zommed_256_annot.png)
  * [amplitude chart for a ping](ping_modulated_audio_amplitude.png)
  * [WAV file with a few calibration packets and a ping or two](ping_modulated_audio.wav)

FAQ:

1. Q: I'm using a real line out / line in, but I'm getting no packets received
      even though some symbols seem to be flowing in.<br>
   A: It's almost certain to be a too high boost ("volume") setting on your
      line in or line out. It has to be a pretty low volume, e.g. 20% works
      for me, but just play around with values.

2. Q: How do I know the names of Pulse Audio devices?<br>
   A: `pactl list short sinks` ← run this to get a list of "line outs"<br>
      `pactl list short sources` ← run this to get a list of "line ins"

3. Q: It would be better if you would use this or that encoding / make this or
      that change.<br>
   A: Absolutely! This is basically an educational piece of code – feel free
      to experiment with it!

4. Q: How do I debug this?<br>
   A: Apart from setting logging/basicConfig below to DEBUG, you should use
      Audacity in Spectogram view with these settings:
      - Scale / Max Frequency: 22050
      - Algorithm / Window size: 64 (FFT_SAMPLE_COUNT) if you want to see what
                                 this applicaton sees.
      - Algorithm / Window size: 256 (SAMPLES_PER_SYMBOL) if you want to see
                                 it in a bit more human-readable way.

5. Q: What's the low-level protocol?<br>
   A: 10 frequencies mixed together that are used in a digital fashion, i.e.
      either the frequency is mixed-in (forming a digital 1) or it's not
      (which makes it a digital 0). These 10 bits form a "symbol". The bits
      are counted from lowest frequency (bit 0) to highest (bit 9).
      All bits set to 0 effectively form silence, which means "nothing is
      being transmitted".

6. Q: What's the high-level protocol?<br>
   A: If the symbol is all 0, that's silence.<br>
      The top-most and bottom-most bits (i.e. bits 9 and 0) are called
      "control bits". The rest of the bits (1-8) are "data bits".<br>
      A packet starts with "lead" symbols, which has both control bits set,
      and alternates the data bits between 0xAA and 0x55. We send 5 of these,
      but require to be able to receive only one.<br>
      The "lead" symbols are immidiately followed by two "packet size"
      symbols. Both have control bits cleared, and use the top two bits of the
      data bits (bit 8 and 7) to denote whether it's the low (0b01) or high
      (0b10) bits of the packet size. The rest of the data bits (6-1) contain
      6 bits of the packet size. The packet size denotes ONLY the actual
      payload size (in symbols / bytes), without the lead symbols, without the
      size symbols, and without the end symbol.<br>
      After this the data starts. The data bits are set to a given byte of
      data (starting with byte 0), and the control bits alternate between
      1-0 and 0-1 (starting with 1-0 for byte 0 of data).<br>
      After all the data symbols are sent, the last symbols is an "end" symbol
      which has both control bits set to 0, and data bits set to 0xFF.

```
      Example of a packet with 2 bytes of data (notation: CTRL9_DATA_CTRL0):
      1_AA_1 1_55_1 1_AA_1 1_55_1 1_AA_1 0_1,2_0 0_2,0_0 1_41_0 0_42_1 0_FF_0
      \___________lead_symbols_________/ \___size:_2___/ \_data:_AB__/  end
```

7. Q: Show me a command line that you used for testing.<br>
   A: 
```
      python audio.py \
             -m both \
             -o /var/run/tun_in.fifo \
             -I 'alsa_input.pci-0000_00_1b.0.analog-stereo' \
             -i /var/run/tun_out.fifo \
             -O 'alsa_output.pci-0000_00_1b.0.analog-stereo'
```


Good luck!
