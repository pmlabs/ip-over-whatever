#!/usr/bin/env python3
# A somewhat simple audio (as in: soundcard line out / line in) modem/transport.
#                                           by Gynvael Coldwind // Dragon Sector
#
# TEMPORARY-NOTE: This works only with datagram UNIX socket IPOW version.
import numpy as np
import numpy.fft as fft
import os
import select
import threading
import tempfile
import argparse
import logging
import socket
import queue
import sys
import pasimple
import time
from struct import pack, unpack

logging.basicConfig(level=logging.INFO)

AUDIO_CHANNELS = 1  # We only support 1 channel.
AUDIO_SAMPLE_FORMAT = pasimple.PA_SAMPLE_S16LE
AUDIO_SAMPLES_PER_SECOND = 44100

# From how many audio sample will we determine the frequencies? Note that this
# also determines which exact frequencies we use (as calculated below).
FFT_SAMPLE_COUNT = 64

# Which frequencies should we use? We're making this easy for ourselves since
# numpy's FFT works nicely with these frequencies.
FREQ_OFFSET = AUDIO_SAMPLES_PER_SECOND / FFT_SAMPLE_COUNT
FREQUENCIES = [FREQ_OFFSET * (4 + i * 3) for i in range(1 + 8 + 1)]
# FREQUENCIES = [FREQ_OFFSET * (5 + i * 3) for i in range(1 + 4 + 1 + 1) if i != 5]
FREQ_INDEXES = [round(freq * FFT_SAMPLE_COUNT / AUDIO_SAMPLES_PER_SECOND)
                for freq in FREQUENCIES]

"""
# Debug code to play with frequencies.
print(FREQUENCIES)
for i in FREQUENCIES:
  for j in FREQUENCIES:
    print(f"{i/j:5.3f}", end=" ")
  print()
sys.exit()
"""

# How long (in samples) do we hold a given frequency pattern when sending?
# This also determines the "step" we use while decoding the samples.
SAMPLES_PER_SYMBOL = 256
assert SAMPLES_PER_SYMBOL >= FFT_SAMPLE_COUNT

# Note: a "symbol" for us is a 10-bit "byte" – bits 1-8 are byte data, and bits
# 0 and 9 are control bits used to denote when data is sent, etc.

# How many calibration/lead symbols to send before sending the payload.
LEAD_SIZE = 5

ABSOLUTELY_MAX_MTU = 20 * 1024


logger = logging.getLogger("audio-modem")
logger_mo = logging.getLogger("audio-[mo]dem")
logger_dem = logging.getLogger("audio-mo[dem]")


class AudioModulator(threading.Thread):
  def __init__(self, audio_sink, tun_outbound_path, the_end):
    super().__init__()
    self.audio_sink = audio_sink
    self.tun_outbound_path = tun_outbound_path
    self.the_end = the_end

    self.tun_outbound = None

    self.t = 0  # Let's at least pretend we have some sinus continuity.

    # Calculate the duration of a symbol in seconds.
    self.t_step = SAMPLES_PER_SYMBOL / AUDIO_SAMPLES_PER_SECOND

    self.available_data = b""
    self.packet_sz = None
    self.packet = b""

  def generate_waveform(self, symbol):
    # This basically generates SAMPLES_PER_SYMBOL symbols which mix sin(freq)
    # for each set bit of the symbol.
    t_next = self.t + self.t_step

    t = np.linspace(self.t, t_next, SAMPLES_PER_SYMBOL, endpoint=False)
    self.t = t_next

    waveform = np.zeros_like(t)
    for i, freq in enumerate(FREQUENCIES):
      if symbol & (1 << i):
        waveform += np.sin(2 * np.pi * freq * t)

    # Normalize the waveform and encode it as int16.
    if (symbol & ((1 << len(FREQUENCIES)) - 1)) != 0:
      waveform /= np.max(np.abs(waveform))

    waveform_i16 = np.int16(waveform * (0x7fff // 2))
    return waveform_i16

  def transmit(self, packet):
    packet_sz = len(packet)
    if packet_sz == 0:
      logger_mo.debug(f"Transmitting empty (calibration) packet")
    else:
      logger_mo.info(f"Transmitting a packet of {packet_sz} bytes")

    symbols = []
    for i in range(LEAD_SIZE):
      # Alternate between sending 0x55 and 0xAA with both control bits set.
      # The lead should both allow for carrier detection, as well as signal
      # calibration.
      if i & 1:
        symbols.append(0b1_01010101_1)
      else:
        symbols.append(0b1_10101010_1)

    # Add size - little endian, 6 bits per symbol, using the following pattern:
    #   0_01bbbbbb_0 - bits 0-5 of size (i.e. bottom 6 bits)
    #   0_10bbbbbb_0 - bits 6-11 of size
    # As such, the size is limited to 4095 bytes of payload.
    symbols.append(0b0_01000000_0 | (((packet_sz >> 0) & 0x3f) << 1))
    symbols.append(0b0_10000000_0 | (((packet_sz >> 6) & 0x3f) << 1))

    # Add payload alternating control bits.
    for i, b in enumerate(packet):
      if i & 1:
        symbols.append(0b0_00000000_1 | (b << 1))
      else:
        symbols.append(0b1_00000000_0 | (b << 1))

    # Finish up with 0xff with both control bits off.
    symbols.append(0b0_11111111_0)

    # Convert symbols to waveforms and send it.
    waveforms = [self.generate_waveform(s) for s in symbols]
    waveform = np.concatenate(waveforms)

    # Check just in case if we shouldn't end before we start blocking.
    if self.the_end.is_set():
      return

    # Play it.
    # Note: Either write() or drain() will be blocking.
    self.audio_sink.write(waveform.tobytes())
    self.audio_sink.drain()

  def worker(self):
    while not self.the_end.is_set():
      rlist, wlist, xlist = select.select([self.tun_outbound], [], [], 0.5)
      if not rlist and not wlist and not xlist:
        # Nothing to send, but still send an empty packet to help the other
        # side calibrate if they just connected.
        self.transmit(b'')
        continue

      if xlist:
        logger_mo.error(f"Disconnected from IPOW's outbound pipe")
        return

      packet, _ = self.tun_outbound.recvfrom(ABSOLUTELY_MAX_MTU)
      if not packet:
        logger_mo.error(f"Something went wrong with getting data from IPOW")
        return

      self.transmit(packet)

  def run(self):
    logger_mo.info(f"Audio modulator (sender) thread online")

    while not self.the_end.is_set():
      with tempfile.TemporaryDirectory() as d:
        sock_name = f"{d}/tun_outbound_receiver"
        try:
          self.tun_outbound = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
          self.tun_outbound.bind(sock_name)

          # Send anything to the IPOW server so it knows where to send data to.
          self.tun_outbound.sendto(b"hi",self.tun_outbound_path)
        except:  # If anything goes wrong, signal the end.
          self.the_end.set()
          raise

        self.worker()

        self.tun_outbound.close()
        self.tun_outbound = None


    logger_mo.info(f"Audio modulator (sender) thread offline")
    self.the_end.set()  # If I exit, everyone exits.


class AudioDemodulator(threading.Thread):
  def __init__(self, tun_inbound_path, audio_source, the_end):
    super().__init__()
    self.audio_source = audio_source
    self.tun_inbound_path = tun_inbound_path
    self.the_end = the_end
    self.state = "NOT_CALIBRATED"
    self.unprocessed_audio_data = []

    self.amp_max = -100000
    self.amp_min =  100000
    self.amp_zero = 0
    self.amp_silence = 100000

    self.packet_sz = None
    self.samples_to_fetch = 1024

  def send_packet(self, s, payload):
    s.sendto(payload, self.tun_inbound_path)

  def get_frequency_magnituted(self, chunk):
    fft_result = fft.fft(chunk)
    fft_magnitude = np.abs(fft_result)[:len(chunk) // 2]
    return [fft_magnitude[idx] for idx in FREQ_INDEXES]

  def symbol_to_str(self, s):
    return f"{(s >> 9) & 1}_{(s >> 1) & 0xff:02x}_{s & 1}"

  def audio_to_symbols(self, data):
    if len(data) < 512:  # We need some data to work with.
      return [], 0

    # Use the first 256 bytes of data to get the right offset.
    best_idx = 0
    best_diff = 0
    for i in range(256 - 64):
      freqs = self.get_frequency_magnituted(data[i:i+64])
      diff = max(freqs) - min(freqs)

      if diff > best_diff:
        best_diff = diff
        best_idx = i

    # Sanity check – is this signal strong enough?
    best_diff = int(best_diff)
    if best_diff < 50000:
      # Not really...
      logger_dem.warning(f"FM signal too weak, best_diff={best_diff}")
      return [], 256

    logger_dem.debug(
        f"FM signal best_diff={best_diff}, best_idx={best_idx}"
    )

    # Read all the symbols until an end symbol.
    symbols = []
    idx = best_idx
    while idx < len(data) - 63:

      freqs = self.get_frequency_magnituted(data[idx:idx+64])

      # We are relying here on there always being a zero sent.
      mag_min = min(freqs)
      mag_max = max(freqs)
      mag_diff = mag_max - mag_min
      mid = mag_min + mag_diff / 5

      s = 0
      for i, f in enumerate(freqs):
        if f > mid:
          s |= 1 << i

      # print(self.symbol_to_str(s), freqs)

      idx += SAMPLES_PER_SYMBOL

      symbols.append(s)

      if s == 0b0_11111111_0:  # Break on end symbol.
        break

    logger_dem.debug(
        f"Received symbols "
        f"{' '.join([self.symbol_to_str(s) for s in symbols])}"
    )

    return symbols, idx

  def worker(self, s_unix):
    while not self.the_end.is_set():
      audio_data = self.audio_source.read(2 * self.samples_to_fetch)
      audio_data = unpack(f"<{len(audio_data)//2}h", audio_data)
      self.samples_to_fetch = 1024

      self.unprocessed_audio_data.extend(audio_data)

      if self.state == "NOT_CALIBRATED":
        # Wait for at least 2 seconds worth of data.
        if len(self.unprocessed_audio_data) < AUDIO_SAMPLES_PER_SECOND * 2:
          continue

        # Figure out maximum/minimum amplitude.
        self.amp_max = max(self.unprocessed_audio_data)
        self.amp_min = min(self.unprocessed_audio_data)

        amp_diff = self.amp_max - self.amp_min

        if amp_diff < 5000:
          logger_dem.warning(
              f"Failed to calibrate, signal to weak "
              f"({100*amp_diff/0x10000:.2f}%)."
          )
          self.unprocessed_audio_data = []
          continue

        self.amp_zero = (self.amp_max + self.amp_min) // 2
        self.amp_silence = int(self.amp_zero + (amp_diff / 2) / 10)

        logger_dem.info(
            f"Calibrated: "
            f"diff={100*amp_diff/0x10000:.2f}% "
            f"zero(i16)={self.amp_zero} "
            f"silence(i16)={self.amp_silence} "
        )

        # Discard all the unprocessed data.
        self.unprocessed_audio_data = []
        self.state = "RECV_FIRST"
        continue

      if self.state == "RECV_FIRST":
        # Find first data which is not silence.
        i = 0
        unprocessed = self.unprocessed_audio_data
        silence = self.amp_silence
        while i < len(unprocessed):
          if unprocessed[i] > self.amp_silence:
            break
          i += 1

        # Was there anything found?
        if i == len(unprocessed):
          # Only silence. Leave a few samples, discard the rest.
          self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
          continue

        i = max(0, i - 32)  # Leave a few samples.
        if i != 0:
          self.unprocessed_audio_data = self.unprocessed_audio_data[i:]

        # Do we have enough data to get at least an empty packet?
        if (len(self.unprocessed_audio_data) <
            (LEAD_SIZE + 2 + 1) * SAMPLES_PER_SYMBOL + 64):
          # Read a bit more to make it easy on ourselves.
          continue

        # Attempt to read size.
        symbols, last_i = self.audio_to_symbols(self.unprocessed_audio_data)

        # Check if we can find any lead symbol in symbols.
        idx = None
        try:
          idx = symbols.index(0b1_10101010_1)
        except ValueError:
          pass

        if idx is None:
          try:
            idx = symbols.index(0b1_01010101_1)
          except ValueError:
            pass

        if idx is None:
          # No lead symbol at all. Discard the data.
          logger_dem.debug(f"Mising leads, skipping data")
          self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
          continue

        # Check if we can get the size.
        while idx < len(symbols):
          s = symbols[idx]

          if s in { 0b1_10101010_1, 0b1_01010101_1 }:
            idx += 1  # Continue to skip lead.
            continue

          if (s & 0b1_11000000_1) == 0b0_01000000_0:
            # Found first symbol with size!
            break

          # Unknown symbol, size was expected. Discard the data.
          idx = -1
          break

        if idx == -1:
          # Some weird data found, discard it.
          logger_dem.warning(f"Weird data found after leads")
          self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
          continue

        if idx == len(symbols):
          # It's apparently leads all the way, but then we run out of data.
          # Save the last symbol worth of data, but discard the rest.
          logger_dem.debug(f"Leads only, wait for more data")
          self.unprocessed_audio_data = (
              self.unprocessed_audio_data[-(32 + SAMPLES_PER_SYMBOL):]
          )
          continue

        # We found the first symbol with size. But is there a next symbol?
        if idx + 1 >= len(symbols):
          # Nah, we don't have enough data. Discard everything apart from
          # last two symbol worth of data and wait for more data.
          logger_dem.debug(f"Waiting for second size symbol")
          self.unprocessed_audio_data = (
              self.unprocessed_audio_data[-(32 + SAMPLES_PER_SYMBOL * 2):]
          )
          continue

        # Verify that the second symbol of size makes sense.
        s2 = symbols[idx + 1]
        if (s2 & 0b1_11000000_1) != 0b0_10000000_0:
          # Corrupted data, discard.
          logger_dem.warning(f"Incorrect second size symbol")
          self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
          continue

        self.packet_sz = ((s >> 1) & 0x3f) | (((s2 >> 1) & 0x3f) << 6)

        # Do we have the full packet?
        what_we_have = len(symbols) - idx - 2 - 1
        if what_we_have < self.packet_sz:
          logger_dem.info(f"Waiting for full packet")
          # Nope, we need more data.
          # NOTE: We should discard some data here, but whatever.
          samples_were_missing = self.packet_sz - what_we_have
          self.samples_to_fetch = SAMPLES_PER_SYMBOL * samples_were_missing + 32
          continue

        idx += 2

        # We have a full packet! Decode it.
        all_good = True
        payload = bytearray(self.packet_sz)
        for i in range(self.packet_sz):
          s = symbols[idx + i]

          if i & 1:
            control_bit = 0b0_00000000_1
          else:
            control_bit = 0b1_00000000_0

          if (s & 0b1_00000000_1) != control_bit:
            # Corrupted data, discard.
            logger_dem.warning(f"Wrong payload control bit {i}")
            self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
            all_good = False
            break

          payload[i] = (s >> 1) & 0xff

        if not all_good:
          continue

        # Check if the last symbol is the end symbol.
        s = symbols[idx + self.packet_sz]
        if s != 0b0_11111111_0:
          # Corrupted data, discard.
          logger_dem.warning(f"Wrong end symbol")
          self.unprocessed_audio_data = self.unprocessed_audio_data[-32:]
          continue

        # All good, we have the payload.
        if len(payload) > 0:
          logger_dem.info(f"Forwarding {self.packet_sz} bytes of data")
          self.send_packet(s_unix, payload)
        else:
          logger_dem.debug(f"Calibration 'ping' received")

        # Remove all received data.
        self.unprocessed_audio_data = self.unprocessed_audio_data[last_i:]
        continue


  def run(self):
    logger_dem.info(f"Audio demodulator (sender) thread online")

    while not self.the_end.is_set():
      s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
      self.worker(s)
      s.close()

    logger_dem.info(f"Audio demodulator (sender) thread offline")
    self.the_end.set()  # If I exit, everyone exits.



def main():
  parser = argparse.ArgumentParser(description="IPOW-compatible audio 'modem'")
  parser.add_argument(
      "-i", "--tun-outbound", type=str,
      help="IPOW's outbound pipe path",
      default='/var/run/tun_out.fifo'
  )
  parser.add_argument(
      "-o", "--tun-inbound", type=str,
      help="IPOW's inbound pipe path",
      default='/var/run/tun_in.fifo'
  )
  parser.add_argument(
      "-m", "--mode", type=str,
      help='Mode: send-audio/receive-audio/both',
      default='both'
  )
  parser.add_argument(
      "-O", "--line-out", type=str,
      help='Pulse Audio sink (line out/speaker); use "pactl list short sinks"',
      default='use--line-out-to-provide-a-sink'
  )
  parser.add_argument("-I", "--line-in", type=str,
      help='Pulse Audio source (line in/mic); use "pactl list short sources"',
      default='use--line-in-to-provide-a-source'
  )
  args = parser.parse_args()

  logging.basicConfig(
      level=logging.DEBUG,
      format='[%(asctime)s] [%(levelname)s] %(funcName)s: %(message)s',
      datefmt='%Y-%m-%d %H:%M:%S',
  )

  mode = args.mode.lower()
  mode_send_audio = "send" in mode or "both" in mode
  mode_recv_audio = "rec" in mode or "both" in mode

  audio_sink = None
  audio_source = None

  if mode_send_audio:
    if not os.path.exists(args.tun_outbound):
      logger.info(
          f"IPOW's outbound named pipe '{args.tun_outbound}' doesn't exist"
      )
      sys.exit()

    try:
      audio_sink = pasimple.PaSimple(
          pasimple.PA_STREAM_PLAYBACK,
          AUDIO_SAMPLE_FORMAT,
          AUDIO_CHANNELS,
          AUDIO_SAMPLES_PER_SECOND,
          device_name=args.line_out
      )
    except pasimple.exceptions.PaSimpleError as e:
      logger.info(f"Couldn't open audio sink '{args.line_out}': {e}")
      sys.exit()

  if mode_recv_audio:
    if not os.path.exists(args.tun_inbound):
      logger.info(
          f"IPOW's inbound named pipe '{args.tun_inbound}' doesn't exist"
      )
      sys.exit()

    try:
      audio_source = pasimple.PaSimple(
          pasimple.PA_STREAM_RECORD,
          AUDIO_SAMPLE_FORMAT,
          AUDIO_CHANNELS,
          AUDIO_SAMPLES_PER_SECOND,
          device_name=args.line_in
      )
    except pasimple.exceptions.PaSimpleError as e:
      logger.info(f"Couldn't open audio source '{args.line_in}': {e}")
      sys.exit()


  audio_modulator_th = None
  audio_demodulator_th = None
  the_end = threading.Event()

  if mode_send_audio:
    audio_modulator_th = AudioModulator(
        audio_sink, args.tun_outbound, the_end
    )
    audio_modulator_th.start()

  if mode_recv_audio:
    audio_demodulator_th = AudioDemodulator(
        args.tun_inbound, audio_source, the_end
    )
    audio_demodulator_th.start()


  # Wait for the end.
  try:
    while not the_end.is_set():
      time.sleep(1)
  except KeyboardInterrupt:
    pass

  logger.info(f"CTRL+C received, telling threads to exit...")

  # The end.
  the_end.set()

  if audio_modulator_th:
    audio_modulator_th.join()

  if audio_demodulator_th:
    audio_demodulator_th.join()


if __name__ == "__main__":
  main()
