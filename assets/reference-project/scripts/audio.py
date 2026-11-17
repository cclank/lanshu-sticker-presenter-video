# Voice to -15 LUFS, lo-fi bed to -21 LUFS (faded over the last 1.2 s), both 48 kHz stereo.
import subprocess, numpy as np, soundfile as sf, pyloudnorm as pyln
DUR = 811 / 30
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', 'assets/take.mp4', '-vn', '-ac', '2', '-ar', '48000', 'build/voice_raw.wav'], check=True)
subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', 'assets/lofi/a-little-shade.ogg', '-t', f'{DUR:.4f}', '-ac', '2', '-ar', '48000', 'build/music_raw.wav'], check=True)
meter = pyln.Meter(48000)
v, sr = sf.read('build/voice_raw.wav')
v = v[:int(DUR * sr)]
lv = meter.integrated_loudness(v); v = v * 10 ** ((-15 - lv) / 20)
m, _ = sf.read('build/music_raw.wav')
n = int(DUR * sr); m = np.pad(m, ((0, max(0, n - len(m))), (0, 0)))[:n]
lm = meter.integrated_loudness(m[:int((DUR - 1.2) * sr)]); m = m * 10 ** ((-21 - lm) / 20)
f = int(1.2 * sr); m[-f:] *= np.linspace(1, 0, f)[:, None] ** 1.5
fi = int(0.15 * sr); m[:fi] *= np.linspace(0, 1, fi)[:, None]
sf.write('build/voice.wav', v, sr, subtype='PCM_24'); sf.write('build/music.wav', m, sr, subtype='PCM_24')
mix = v + m
print('voice', round(meter.integrated_loudness(v), 2), 'music', round(meter.integrated_loudness(m), 2),
      'mix', round(meter.integrated_loudness(mix), 2), 'peak dB', round(20 * np.log10(np.abs(mix).max()), 2))
