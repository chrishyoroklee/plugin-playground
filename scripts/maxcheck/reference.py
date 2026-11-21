"""Boil a folder of rendered WAVs down to a small level profile that a test can hold the
C++ to without a browser or Max at hand: for every test, each channel's level in tenth-of-
a-second windows, in hundredths of a dB. tests/test_patchc.py renders the same tests
through the compiled C++ and compares.
Usage: reference.py tests.json signal.wav <dir of WAVs> out.json [what rendered them]"""
import json, sys
import numpy as np
import analyze as A

WINDOW = 4410      # samples: a tenth of a second at 44.1 kHz
FLOOR = -120.0     # dB; anything quieter reads as this

def profile(path):
    """Level per window and channel, in centi-dB: [[left...], [right...]]."""
    x = A.load(path)
    n = len(x) // WINDOW
    w = x[:n * WINDOW].reshape(n, WINDOW, x.shape[1])
    db = 10 * np.log10(np.maximum((w * w).mean(axis=1), 10 ** (FLOOR / 10)))
    return [[int(round(v * 100)) for v in db[:, c]] for c in range(x.shape[1])]

if __name__ == '__main__':
    tests, signal, folder, out = sys.argv[1:5]
    source = sys.argv[5] if len(sys.argv) > 5 else 'the browser engine'
    ref = {
        'about': 'Level per tenth of a second (centi-dB, left then right) of the maxcheck signal through each test, as rendered by %s. Made by scripts/maxcheck/reference.py.' % source,
        'signal': profile(signal),
        'window': WINDOW,
        'tests': [{'name': t['name'], 'settings': t['settings'], 'levels': profile('%s/%s.wav' % (folder, t['name']))}
                  for t in json.load(open(tests))],
    }
    json.dump(ref, open(out, 'w'), separators=(',', ':'))
    print(len(ref['tests']))
