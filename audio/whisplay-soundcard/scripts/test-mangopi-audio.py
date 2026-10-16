#!/usr/bin/env python3
"""Run on MQ Quad as root; stop/restore daemon and verify real PCM samples.

Digital modes briefly enable documented H616/ES8389 loopbacks and restore them.
Microphone mode plays the same test tone through the speaker, but never enables
either digital loopback. Keep its WAV for acoustic inspection.
"""
import argparse
import array
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def register(address, value=None):
    args = ['busybox', 'devmem', hex(address), '32']
    if value is not None:
        args.append(hex(value))
    result = command(*args)
    return int(result, 16) if result else None


def analyze(path, mode, pcm_format):
    with wave.open(str(path)) as wav:
        rate = wav.getframerate()
        samples = array.array('h' if pcm_format == 'S16_LE' else 'i',
                              wav.readframes(wav.getnframes()))
    shift = {'S16_LE': 0, 'S24_LE': 8, 'S32_LE': 16}[pcm_format]
    channels = []
    for channel, frequency in enumerate((997, 1499)):
        if mode == 'microphone':
            frequency = 997  # HAT amplifier uses the left DAC output.
        # Inspect steady-state tone, avoiding ADC startup and shutdown.
        data = [x >> shift for x in samples[channel::2][rate:rate*2]]
        rms = math.sqrt(sum(x*x for x in data) / len(data))
        def amplitude(hz):
            real = sum(x*math.cos(2*math.pi*hz*i/rate) for i,x in enumerate(data))
            imag = sum(x*math.sin(2*math.pi*hz*i/rate) for i,x in enumerate(data))
            return 2*math.hypot(real, imag)/len(data)
        channels.append({'rms': round(rms, 2), 'peak': max(map(abs, data)),
                         'distinct': len(set(data)),
                         'expected_tone_hz': frequency,
                         'tone_amplitude': round(amplitude(frequency), 2),
                         'other_tone_amplitude': round(amplitude(1499 if frequency == 997 else 997), 2)})
    return channels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('soc-loopback', 'codec-loopback', 'microphone'))
    parser.add_argument('--device', default='hw:whisplaysound')
    parser.add_argument('--format', choices=('S16_LE', 'S24_LE', 'S32_LE'), default='S16_LE')
    parser.add_argument('--codec-address', default='0x12', choices=('0x10', '0x12'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run as root on the MQ Quad')
    if not Path('/sys/bus/platform/devices/5097000.whisplay-ahub0').exists():
        parser.error('MQ Quad vendor AHUB device is absent')
    output = args.output or Path(tempfile.mkdtemp(prefix='mq-audio-'))
    output.mkdir(parents=True, exist_ok=True)
    tone = output / ('tone-' + args.format + '.raw')
    capture = output / (args.mode + '-' + args.format + '.wav')
    shift = {'S16_LE': 0, 'S24_LE': 8, 'S32_LE': 16}[args.format]
    data = array.array('h' if args.format == 'S16_LE' else 'i')
    for i in range(48000*3):
        data.extend((int(3000*math.sin(2*math.pi*997*i/48000)) << shift,
                     int(2000*math.sin(2*math.pi*1499*i/48000)) << shift))
    tone.write_bytes(data.tobytes())
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'whisplay-daemon.service']).returncode == 0
    if active:
        subprocess.run(['systemctl', 'stop', 'whisplay-daemon.service'], check=True)
    recorder = player = None
    old_loop = old_codec = None
    try:
        if args.mode == 'microphone':
            adc_mix = int(command('i2cget', '-f', '-y', '0', args.codec_address, '0x31', 'b'), 16)
            if register(0x5097200) & (1 << 20) or adc_mix & 0xc0:
                raise RuntimeError('disable existing digital loopbacks before acoustic testing')
        if args.mode == 'codec-loopback':
            old_codec = int(command('i2cget', '-f', '-y', '0', args.codec_address, '0x31', 'b'), 16)
        recorder = subprocess.Popen(['arecord', '-q', '-D', args.device, '-f', args.format,
                                     '-r', '48000', '-c', '2', '-d', '5', str(capture)])
        time.sleep(.3)
        if args.mode == 'soc-loopback':
            old_loop = register(0x5097200) & (1 << 20)
            register(0x5097200, register(0x5097200) | (1 << 20))
        if args.mode == 'codec-loopback':
            command('i2cset', '-f', '-y', '0', args.codec_address, '0x31', '0xc0', 'b')
        started = time.monotonic()
        player = subprocess.Popen(['aplay', '-q', '-D', args.device, '-t', 'raw',
                                   '-f', args.format, '-r', '48000', '-c', '2', str(tone)])
        if player.wait(timeout=10):
            raise RuntimeError('playback failed')
        elapsed = time.monotonic()-started
        if recorder.wait(timeout=10):
            raise RuntimeError('capture failed')
        channels = analyze(capture, args.mode, args.format)
        passed = all(ch['distinct'] > 32 and ch['rms'] > 4 and ch['peak'] < 32000
                     for ch in channels)
        if args.mode != 'microphone':
            passed &= all(ch['tone_amplitude'] > 1000 for ch in channels)
        else:
            passed &= all(ch['tone_amplitude'] > 20 for ch in channels)
        # Codec ADC mixer adds DAC data to the live microphone signal.
        # Only the SoC loopback is isolated enough to measure crosstalk.
        if args.mode == 'soc-loopback':
            passed &= all(ch['other_tone_amplitude'] < 20 for ch in channels)
        passed &= 2.8 < elapsed < 3.4
        report = {'mode': args.mode, 'format': args.format, 'play_seconds': elapsed, 'channels': channels,
                  'passed': passed, 'capture': str(capture)}
        (output / (args.mode + '-' + args.format + '.json')).write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report))
        return 0 if passed else 1
    finally:
        for proc in (player, recorder):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
        if old_loop is not None:
            register(0x5097200, (register(0x5097200) & ~(1 << 20)) | old_loop)
        if old_codec is not None:
            command('i2cset', '-f', '-y', '0', args.codec_address, '0x31', hex(old_codec), 'b')
        if active:
            subprocess.run(['systemctl', 'start', 'whisplay-daemon.service'], check=True)


if __name__ == '__main__':
    raise SystemExit(main())
