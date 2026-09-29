#!/usr/bin/env python3
"""
ADMI-X Scientific Dashboard  |  Team THE SONICTRACE  |  SIH 2026 (PS 26144)
Adaptive Differential Microbarometer with Self-Characterizing Pneumatic Reference
& Measurement-Quality Intelligence.

Install : pip install PyQt5 pyqtgraph numpy scipy pandas pyserial h5py
Run     : python admix_dashboard.py

Data sources
  * Simulator      - built-in synthetic infrasound + environment (demo/testing)
  * Serial (STM32) - one ASCII line per sample:  P,<pressure_Pa>,<temp_C>,<hum_%>,<ax_g>,<ay_g>,<az_g>\\n
                     (set ADC/STM32 firmware to output at FS Hz; edit FS below to match)
"""
import sys, json, time
import numpy as np
import pandas as pd
from scipy.signal import welch, spectrogram, coherence
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

# ------------------------------------------------------------------ config
FS = 100                     # sampling rate (Hz)
WIN_S = 60                   # display window (s)
N = FS * WIN_S
RANGE_PA = 250.0             # sensor full-scale (Pa) - used for saturation check
TAU0 = 15.9                  # nominal pneumatic time constant Rp*Cp (s)  -> fc = 1/(2*pi*tau)
STA_S, LTA_S, TRIG = 2, 30, 3.0
CAL = {"calibration_id": "CAL-DEMO-001", "gain": 1.0, "sensitivity_V_per_Pa": 0.0,
       "config": "4-port WNRS, 24-bit dsADC, STM32"}

C_DARK = dict(bg="#0b1220", panel="#111a2e", edge="#1f2c4a", text="#e6edf7", dim="#8a9bb8",
         accent="#3ea6ff", good="#2ecc71", warn="#f5b82e", bad="#ef4b5f", sig="#ff9f43")
C_LIGHT = dict(bg="#f4f7fa", panel="#ffffff", edge="#dcdfe6", text="#2c3e50", dim="#7f8c8d",
         accent="#3498db", good="#27ae60", warn="#f39c12", bad="#c0392b", sig="#e67e22")
C = C_DARK.copy()


# ------------------------------------------------------------------ data sources
class Simulator:
    """Synthetic microbarom (0.2 Hz) + slow swell + wind noise + periodic infrasonic events."""
    def __init__(self):
        self.n, self.last, self.fault = 0, time.monotonic(), 0.0   # fault: tau multiplier offset
        self.rng = np.random.default_rng()

    def read(self):
        now = time.monotonic()
        k = int((now - self.last) * FS)
        if k <= 0:
            return np.empty((0, 6))
        self.last += k / FS
        t = (self.n + np.arange(k)) / FS
        self.n += k
        ph = t % 80
        env = np.where((ph > 60) & (ph < 66), np.sin(np.pi * (ph - 60) / 6), 0)
        p = (0.8 * np.sin(2 * np.pi * 0.2 * t) + 0.3 * np.sin(2 * np.pi * 0.05 * t)
             + 2.0 * env * np.sin(2 * np.pi * 1.5 * t) + 0.15 * self.rng.standard_normal(k))
        temp = 28 + 2 * np.sin(2 * np.pi * t / 600) + 0.02 * self.rng.standard_normal(k)
        hum = 60 + 3 * np.sin(2 * np.pi * t / 900) + 0.05 * self.rng.standard_normal(k)
        vib = 0.002 * self.rng.standard_normal((k, 3))
        return np.column_stack([p, temp, hum, vib])

    def tau_est(self):
        return TAU0 * (1 + self.fault) * (1 + 0.01 * self.rng.standard_normal())


class SerialSource:
    def __init__(self, port, baud=115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0)
        self.tail = b""

    def read(self):
        self.tail += self.ser.read(self.ser.in_waiting or 1)
        *lines, self.tail = self.tail.split(b"\n")
        rows = []
        for ln in lines:
            try:
                v = ln.decode().strip().split(",")
                if v[0] == "P" and len(v) == 7:
                    rows.append([float(x) for x in v[1:]])
            except ValueError:
                pass
        return np.array(rows) if rows else np.empty((0, 6))

    def tau_est(self):
        return TAU0          # replace with firmware-reported value once self-diagnostic is running


# ------------------------------------------------------------------ measurement-quality engine
def assess(p, temp, vib, f, pxx, tau, coh_mean):
    """Return (score 0-100, state, list of (name, ok, detail))."""
    sig = np.trapezoid(pxx[(f >= 0.02) & (f <= 5)], f[(f >= 0.02) & (f <= 5)])
    noi = np.trapezoid(pxx[(f >= 10) & (f <= 20)], f[(f >= 10) & (f <= 20)])
    snr = 10 * np.log10((sig + 1e-20) / (noi + 1e-20))
    drift = (temp[-1] - temp[0]) / (len(temp) / FS / 60)            # deg C / min
    dev = abs(tau / TAU0 - 1) * 100
    checks = [
        ("ADC saturation", np.max(np.abs(p)) < 0.95 * RANGE_PA, f"peak {np.max(np.abs(p)):.2f} Pa", 30),
        ("Signal-to-noise", snr > 10, f"{snr:.1f} dB", 20),
        ("Vibration coupling", coh_mean < 0.6, f"coherence {coh_mean:.2f}", 20),
        ("Temperature drift", abs(drift) < 1.0, f"{drift:+.2f} °C/min", 10),
        ("Pneumatic response", dev < 20, f"τ dev {dev:.1f} %", 20),
    ]
    score = 100 - sum(w for _, ok, _, w in checks if not ok)
    state = "GOOD" if score >= 80 else "DEGRADED" if score >= 50 else "UNTRUSTED"
    return score, state, [(n, ok, d) for n, ok, d, _ in checks], snr


# ------------------------------------------------------------------ widgets
class Card(QtWidgets.QFrame):
    def __init__(self, title, unit=""):
        super().__init__()
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(0)
        t = QtWidgets.QLabel(title.upper()); t.setObjectName("cardTitle")
        self.v = QtWidgets.QLabel("--"); self.v.setObjectName("cardValue")
        self.u = QtWidgets.QLabel(unit); self.u.setObjectName("cardUnit")
        lay.addWidget(t); lay.addWidget(self.v); lay.addWidget(self.u)

    def set(self, text, color=None):
        self.v.setText(text)
        self.v.setStyleSheet(f"color:{color or C['text']}")


def make_plot(title, xl, yl):
    w = pg.PlotWidget(title=title)
    w.showGrid(x=True, y=True, alpha=0.2)
    w.setLabel("bottom", xl); w.setLabel("left", yl)
    w.getPlotItem().titleLabel.setText(title, color=C["dim"], size="10pt")
    return w


# ------------------------------------------------------------------ main window
class Dashboard(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ADMI-X Scientific Dashboard - THE SONICTRACE")
        self.resize(1500, 900)
        self.src = Simulator()
        self.buf = np.zeros((N, 6)); self.filled = 0
        self.rec, self.t0, self.running = [], time.time(), True
        self.in_event, self.tick, self.state = False, 0, "GOOD"
        self.tau, self.coh = TAU0, 0.0
        self._build()
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.update_loop); self.timer.start(50)
        self.log("Dashboard started - source: Simulator", "info")

    # ---------- UI
    def _build(self):
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        g = QtWidgets.QVBoxLayout(root); g.setContentsMargins(14, 10, 14, 10); g.setSpacing(10)

        # header
        h = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("ADMI-X  <span style='color:%s;font-weight:400'>Scientific Dashboard</span>" % C["accent"])
        title.setObjectName("title")
        sub = QtWidgets.QLabel("THE SONICTRACE  |  SIH 2026 - PS 26144  |  0.01-20 Hz infrasound monitoring")
        sub.setObjectName("sub")
        tb = QtWidgets.QVBoxLayout(); tb.setSpacing(0); tb.addWidget(title); tb.addWidget(sub)
        h.addLayout(tb); h.addStretch()
        self.pill = QtWidgets.QLabel("GOOD"); self.pill.setObjectName("pill")
        self.clock = QtWidgets.QLabel(""); self.clock.setObjectName("sub")
        h.addWidget(self.clock); h.addSpacing(14); h.addWidget(self.pill)
        g.addLayout(h)

        # controls
        c = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Pause"); self.btn_run.clicked.connect(self.toggle)
        self.combo = QtWidgets.QComboBox(); self.combo.addItems(["Simulator", "Serial (STM32)"])
        self.combo.activated.connect(self.change_source)
        self.btn_fault = QtWidgets.QPushButton("Inject blockage (demo)"); self.btn_fault.setCheckable(True)
        self.btn_fault.clicked.connect(self.toggle_fault)
        self.btn_exp = QtWidgets.QPushButton("Export CSV / HDF5"); self.btn_exp.clicked.connect(self.export)
        self.btn_theme = QtWidgets.QPushButton("Light Theme"); self.btn_theme.clicked.connect(self.toggle_theme)
        for w in (self.btn_run, QtWidgets.QLabel("Source:"), self.combo, self.btn_fault):
            c.addWidget(w)
        c.addStretch(); c.addWidget(self.btn_theme); c.addWidget(self.btn_exp)
        g.addLayout(c)

        # KPI row
        k = QtWidgets.QHBoxLayout()
        self.k = {n: Card(n, u) for n, u in [("RMS pressure", "Pa"), ("Peak-to-peak", "Pa"),
                 ("Dominant freq", "Hz"), ("SNR", "dB"), ("STA/LTA", f"trigger > {TRIG}"),
                 ("Temperature", "°C"), ("Humidity", "% RH"), ("Vibration RMS", "mg")]}
        for w in self.k.values():
            k.addWidget(w)
        g.addLayout(k)

        # body
        body = QtWidgets.QHBoxLayout(); body.setSpacing(10)
        left = QtWidgets.QVBoxLayout()
        self.p_wave = make_plot("Differential Pressure (live)", "Time (s)", "Pressure (Pa)")
        self.c_wave = self.p_wave.plot(pen=pg.mkPen(C["sig"], width=1.4))
        self.p_wave.setXRange(-WIN_S, 0)
        self.p_psd = make_plot("Power Spectral Density (Welch)", "Frequency (Hz)", "PSD (dB re 1 Pa²/Hz)")
        self.p_psd.setLogMode(x=True, y=False)
        self.c_psd = self.p_psd.plot(pen=pg.mkPen(C["accent"], width=1.6), fillLevel=-160,
                                     brush=pg.mkBrush(62, 166, 255, 40))
        band = pg.LinearRegionItem((np.log10(0.01), np.log10(20)), movable=False,
                                   brush=pg.mkBrush(46, 204, 113, 25))
        self.p_psd.addItem(band)
        self.p_spec = make_plot("Spectrogram (0-20 Hz)", "Time (s)", "Frequency (Hz)")
        self.img = pg.ImageItem(); self.p_spec.addItem(self.img)
        self.img.setLookupTable(pg.colormap.get("viridis").getLookupTable(nPts=256))
        row = QtWidgets.QHBoxLayout(); row.addWidget(self.p_psd); row.addWidget(self.p_spec)
        left.addWidget(self.p_wave, 5); left.addLayout(row, 5)
        body.addLayout(left, 4)

        right = QtWidgets.QVBoxLayout()
        self.q_box = self._panel("Measurement-Quality Engine")
        self.q_score = QtWidgets.QLabel("100"); self.q_score.setObjectName("score")
        self.q_bar = QtWidgets.QProgressBar(); self.q_bar.setRange(0, 100); self.q_bar.setTextVisible(False)
        self.q_list = QtWidgets.QLabel(""); self.q_list.setTextFormat(QtCore.Qt.RichText)
        for w in (self.q_score, self.q_bar, self.q_list):
            self.q_box.layout().addWidget(w)
        self.n_box = self._panel("Pneumatic Self-Diagnostic")
        self.n_txt = QtWidgets.QLabel(""); self.n_txt.setTextFormat(QtCore.Qt.RichText)
        self.n_box.layout().addWidget(self.n_txt)
        self.l_box = self._panel("Event & System Log")
        self.logv = QtWidgets.QPlainTextEdit(); self.logv.setReadOnly(True)
        self.logv.setMaximumBlockCount(500)
        self.l_box.layout().addWidget(self.logv)
        right.addWidget(self.q_box, 3); right.addWidget(self.n_box, 2); right.addWidget(self.l_box, 4)
        body.addLayout(right, 1)
        g.addLayout(body, 1)

        self.statusBar().showMessage("f_c = 1 / (2π·Rp·Cp)     P = (Vout − Voffset) / S     SNR = 20·log10(Psig,rms / Pnoise,rms)")
        self.setStyleSheet(get_style())

    def _panel(self, title):
        f = QtWidgets.QFrame(); f.setObjectName("panel")
        l = QtWidgets.QVBoxLayout(f); l.setContentsMargins(12, 10, 12, 10)
        t = QtWidgets.QLabel(title.upper()); t.setObjectName("cardTitle"); l.addWidget(t)
        return f

    # ---------- controls
    def toggle(self):
        self.running = not self.running
        self.btn_run.setText("Pause" if self.running else "Resume")
        self.log("Acquisition " + ("resumed" if self.running else "paused"), "info")

    def toggle_fault(self):
        if isinstance(self.src, Simulator):
            self.src.fault = 0.6 if self.btn_fault.isChecked() else 0.0
            self.log("Simulated capillary blockage " + ("ON" if self.src.fault else "OFF"), "warn")

    def toggle_theme(self):
        global C
        if self.btn_theme.text() == "Light Theme":
            C.update(C_LIGHT)
            self.btn_theme.setText("Dark Theme")
        else:
            C.update(C_DARK)
            self.btn_theme.setText("Light Theme")
        
        self.setStyleSheet(get_style())
        
        # update pyqtgraph plots
        for p in (self.p_wave, self.p_psd, self.p_spec):
            p.setBackground(C["panel"])
            p.getPlotItem().titleLabel.setText(p.getPlotItem().titleLabel.text, color=C["dim"], size="10pt")
            p.getAxis("bottom").setPen(C["dim"]); p.getAxis("bottom").setTextPen(C["dim"])
            p.getAxis("left").setPen(C["dim"]); p.getAxis("left").setTextPen(C["dim"])
        
        self.c_wave.setPen(pg.mkPen(C["sig"], width=1.4))
        self.c_psd.setPen(pg.mkPen(C["accent"], width=1.6))
        # (fillLevel brush won't auto-update, but lines/bg will)

    def change_source(self, i):
        if i == 0:
            self.src = Simulator(); self.log("Source: Simulator", "info"); return
        port, ok = QtWidgets.QInputDialog.getText(self, "Serial port", "Port (e.g. COM3 or /dev/ttyACM0):")
        try:
            if not ok: raise RuntimeError("cancelled")
            self.src = SerialSource(port); self.log(f"Source: STM32 on {port}", "info")
        except Exception as e:
            self.combo.setCurrentIndex(0); self.src = Simulator()
            self.log(f"Serial failed ({e}) - back to Simulator", "bad")

    def log(self, msg, lvl="info"):
        col = dict(info=C["dim"], warn=C["warn"], bad=C["bad"], good=C["good"])[lvl]
        self.logv.appendHtml(f"<span style='color:{C['dim']}'>{time.strftime('%H:%M:%S')}</span> "
                             f"<span style='color:{col}'>{msg}</span>")

    def export(self):
        if not self.rec:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export", "admix_data.csv", "CSV (*.csv)")
        if not path:
            return
        d = np.vstack(self.rec)
        df = pd.DataFrame(d, columns=["pressure_Pa", "temp_C", "humidity_pct", "ax_g", "ay_g", "az_g"])
        df.insert(0, "t_s", np.arange(len(df)) / FS)
        df.to_csv(path, index=False)
        meta = dict(CAL, fs_hz=FS, tau_nominal_s=TAU0, last_tau_est_s=self.tau,
                    quality_state=self.state, exported=time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(path.rsplit(".", 1)[0] + "_meta.json", "w") as f:
            json.dump(meta, f, indent=2)
        try:
            df.to_hdf(path.rsplit(".", 1)[0] + ".h5", key="raw", mode="w")
        except Exception:
            pass   # h5py/pytables not installed - CSV + JSON still saved
        self.log(f"Exported {len(df)} samples + calibration metadata", "good")

    # ---------- main loop
    def update_loop(self):
        self.clock.setText(time.strftime("%d %b %Y  %H:%M:%S"))
        if not self.running:
            return
        blk = self.src.read()
        if len(blk):
            n = min(len(blk), N)
            self.buf = np.roll(self.buf, -n, 0); self.buf[-n:] = blk[-n:]
            self.filled = min(N, self.filled + n)
            self.rec.append(blk)
            self.tau = self.src.tau_est()
        if self.filled < FS * 10:
            return
        self.tick += 1
        d = self.buf[-self.filled:]
        p = d[:, 0]
        t = np.arange(-len(p), 0) / FS
        self.c_wave.setData(t, p)
        vib = np.linalg.norm(d[:, 3:6] - d[:, 3:6].mean(0), axis=1)

        # fast KPIs (every 250 ms)
        if self.tick % 5 == 0:
            e = p ** 2
            sta = e[-STA_S * FS:].mean(); lta = e[-min(len(e), LTA_S * FS):].mean() + 1e-12
            ratio = sta / lta
            self.k["RMS pressure"].set(f"{np.sqrt(e[-5 * FS:].mean()):.3f}")
            self.k["Peak-to-peak"].set(f"{np.ptp(p):.2f}")
            self.k["STA/LTA"].set(f"{ratio:.2f}", C["bad"] if ratio > TRIG else C["good"])
            self.k["Temperature"].set(f"{d[-1, 1]:.2f}")
            self.k["Humidity"].set(f"{d[-1, 2]:.1f}")
            self.k["Vibration RMS"].set(f"{np.sqrt((vib ** 2).mean()) * 1000:.2f}")
            if ratio > TRIG and not self.in_event:
                self.log(f"EVENT detected - STA/LTA = {ratio:.1f}", "bad")
            self.in_event = ratio > TRIG

        # slow analysis (every 1 s)
        if self.tick % 20 == 0:
            f, pxx = welch(p, FS, nperseg=min(len(p), 2048))
            fc, cxy = coherence(p, vib, FS, nperseg=min(len(p), 512))
            self.coh = float(cxy[(fc >= 1) & (fc <= 20)].mean())
            m = f > 0
            self.c_psd.setData(f[m], 10 * np.log10(pxx[m] + 1e-20))
            self.p_psd.setXRange(np.log10(0.01), np.log10(20))
            band = (f >= 0.02) & (f <= 20)
            self.k["Dominant freq"].set(f"{f[band][np.argmax(pxx[band])]:.3f}")
            score, state, checks, snr = assess(p, d[:, 1], vib, f, pxx, self.tau, self.coh)
            self.k["SNR"].set(f"{snr:.1f}", C["good"] if snr > 10 else C["warn"])
            self.show_quality(score, state, checks)
            self.show_pneumatic()

        # spectrogram (every 3 s)
        if self.tick % 60 == 0:
            fs_, ts, S = spectrogram(p, FS, nperseg=512, noverlap=448)
            m = fs_ <= 20
            dB = 10 * np.log10(S[m] + 1e-20)
            self.img.setImage(dB.T, autoLevels=False)
            self.img.setLevels(np.percentile(dB, [5, 99]))
            self.img.setRect(QtCore.QRectF(ts[0] - len(p) / FS, 0, ts[-1] - ts[0], fs_[m][-1]))
            self.p_spec.setXRange(-len(p) / FS, 0)

    def show_quality(self, score, state, checks):
        col = dict(GOOD=C["good"], DEGRADED=C["warn"], UNTRUSTED=C["bad"])[state]
        if state != self.state:
            self.log(f"Measurement quality: {self.state} → {state}", "good" if state == "GOOD" else "warn")
            self.state = state
        self.pill.setText(f"● {state}"); self.pill.setStyleSheet(f"background:{col}22;color:{col};border:1px solid {col}")
        self.q_score.setText(f"{score}<span style='font-size:14px;color:{C['dim']}'> / 100</span>")
        self.q_score.setStyleSheet(f"color:{col}")
        self.q_bar.setValue(score); self.q_bar.setStyleSheet(f"QProgressBar::chunk{{background:{col};border-radius:3px}}")
        self.q_list.setText("<br>".join(
            f"<span style='color:{C['good'] if ok else C['bad']}'>{'✔' if ok else '✖'}</span> "
            f"{n} <span style='color:{C['dim']}'>- {dd}</span>" for n, ok, dd in checks))

    def show_pneumatic(self):
        dev = (self.tau / TAU0 - 1) * 100
        st, col = (("HEALTHY", C["good"]) if abs(dev) < 10 else ("WATCH", C["warn"]) if abs(dev) < 20
                   else ("BLOCKAGE?" if dev > 0 else "LEAK?", C["bad"]))
        self.n_txt.setText(
            f"Status: <b style='color:{col}'>{st}</b><br>"
            f"τ measured: <b>{self.tau:.2f} s</b> &nbsp; (nominal {TAU0:.1f} s)<br>"
            f"Deviation: <b style='color:{col}'>{dev:+.1f} %</b><br>"
            f"Cutoff f<sub>c</sub>: <b>{1 / (2 * np.pi * self.tau):.4f} Hz</b><br>"
            f"Calibration: {CAL['calibration_id']}")


def get_style():
    return f"""
QMainWindow, QWidget {{ background:{C['bg']}; color:{C['text']}; font-family:'Segoe UI','Inter',sans-serif; font-size:12px; }}
#title {{ font-size:22px; font-weight:700; }}
#sub {{ color:{C['dim']}; font-size:11px; }}
#pill {{ padding:6px 16px; border-radius:14px; font-weight:700; letter-spacing:1px; }}
#card, #panel {{ background:{C['panel']}; border:1px solid {C['edge']}; border-radius:8px; }}
#cardTitle {{ color:{C['dim']}; font-size:10px; font-weight:600; letter-spacing:1px; }}
#cardValue {{ font-size:24px; font-weight:700; }}
#cardUnit {{ color:{C['dim']}; font-size:10px; }}
#score {{ font-size:36px; font-weight:700; }}
QPushButton {{ background:{C['panel']}; border:1px solid {C['edge']}; border-radius:6px; padding:6px 14px; }}
QPushButton:hover {{ border-color:{C['accent']}; }}
QPushButton:checked {{ background:{C['bad']}33; border-color:{C['bad']}; }}
QComboBox {{ background:{C['panel']}; border:1px solid {C['edge']}; border-radius:6px; padding:5px 10px; }}
QProgressBar {{ background:{C['edge']}; border-radius:3px; max-height:6px; }}
QPlainTextEdit {{ background:transparent; border:none; font-family:Consolas,monospace; font-size:11px; }}
QStatusBar {{ color:{C['dim']}; }}
"""


if __name__ == "__main__":
    pg.setConfigOptions(antialias=True, background=C["panel"], foreground=C["dim"])
    app = QtWidgets.QApplication(sys.argv)
    w = Dashboard(); w.show()
    sys.exit(app.exec_())
