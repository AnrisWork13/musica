# tuner_ws.py
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

SR = 16000                # we’ll stream 16 kHz from browser
WIN = 4096                # analysis window
MIN_HZ, MAX_HZ = 60, 1200

TUNINGS = {
    "guitar":  {"E2":82.41,"A2":110.00,"D3":146.83,"G3":196.00,"B3":246.94,"E4":329.63},
    "violin":  {"G3":196.00,"D4":293.66,"A4":440.00,"E5":659.25},
    "mandolin":{"G3":196.00,"D4":293.66,"A4":440.00,"E5":659.25},
}

NOTE_ORDER = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
A4 = 440.0

def hz_to_cents(f, ref):
    if f <= 0 or ref <= 0:
        return None
    return 1200.0 * np.log2(f / ref)

def closest_note(f, tuning):
    # pick nearest note in this tuning map
    name = min(tuning, key=lambda n: abs(tuning[n] - f))
    return name, tuning[name]

def autocorr_pitch(y, sr):
    y = y.astype(np.float64)
    y -= np.mean(y)
    if np.max(np.abs(y)) < 1e-4:
        return 0.0
    y *= np.hanning(len(y))
    n = 1
    L = len(y)
    while n < 2 * L:
        n <<= 1
    Y = np.fft.rfft(y, n=n)
    ac = np.fft.irfft(np.abs(Y)**2)
    ac = ac[:L]
    min_lag = int(sr / MAX_HZ)
    max_lag = min(L-1, int(sr / MIN_HZ))
    if max_lag <= min_lag + 2:
        return 0.0
    d = np.diff(ac)
    start = min_lag
    while start < max_lag-1 and d[start] <= 0:
        start += 1
    if start >= max_lag-1:
        return 0.0
    peak = start + np.argmax(ac[start:max_lag])
    if 1 <= peak < len(ac)-1:
        a, b, c = ac[peak-1], ac[peak], ac[peak+1]
        denom = (a - 2*b + c)
        if abs(denom) > 1e-12:
            peak = peak + 0.5*(a - c)/denom
    return float(sr / peak) if peak > 0 else 0.0

@app.websocket("/tune")
async def tune_ws(ws: WebSocket):
    await ws.accept()
    # default tuner = guitar unless client sends {"instrument":"violin"} once
    tuning = TUNINGS["guitar"]
    ring = np.zeros(WIN, dtype=np.float32)
    idx = 0

    try:
        while True:
            data = await ws.receive_bytes()
            # First few messages can be small JSON; handle both:
            if len(data) and data[0] == 0x7B:  # '{'
                try:
                    msg = (data.decode("utf-8"))
                    if '"instrument"' in msg:
                        # tiny parser to avoid importing json repeatedly
                        if "violin" in msg: tuning = TUNINGS["violin"]
                        elif "mandolin" in msg: tuning = TUNINGS["mandolin"]
                        else: tuning = TUNINGS["guitar"]
                    await ws.send_json({"ok": True})
                    continue
                except Exception:
                    pass

            # PCM32F mono frames from browser
            x = np.frombuffer(data, dtype=np.float32)
            L = len(x)
            end = idx + L
            if end <= WIN:
                ring[idx:end] = x
            else:
                part = WIN - idx
                ring[idx:] = x[:part]
                ring[:end % WIN] = x[part:]
            idx = (idx + L) % WIN

            # analyze a full window
            if L == 0:
                continue
            # contiguous slice ending at idx
            if idx >= WIN:
                y = ring[idx-WIN:idx]
            else:
                y = np.concatenate([ring[idx-WIN:], ring[:idx]])

            f0 = autocorr_pitch(y, SR)
            if f0 <= 0:
                await ws.send_json({"freq": 0})
                continue

            note, target = closest_note(f0, tuning)
            cents = hz_to_cents(f0, target)
            arrow = "ok" if cents is not None and abs(cents) < 5 else ("sharp" if cents and cents > 0 else "flat")
            await ws.send_json({
                "freq": round(f0, 2),
                "note": note,
                "target": round(target, 2),
                "cents": None if cents is None else round(cents, 1),
                "state": arrow
            })
    except WebSocketDisconnect:
        return
