# ADMI-X — Adaptive Differential Microbarometer

**Self-Characterizing Pneumatic Reference & Measurement-Quality Intelligence**

Smart India Hackathon 2026 · Problem Statement **26144** (Hardware · Smart Automation)
Team **THE SONICTRACE** · Team ID **163643**

> Design & Development of a High-Sensitivity Micro-barometer Infrasound Sensor

---

## Overview

ADMI-X is a differential microbarometer for measuring very-low-frequency atmospheric
pressure fluctuations in the **0.01–20 Hz** infrasonic band. Instead of assuming a fixed
pneumatic response, it **measures its own pneumatic transfer response and its own
measurement quality**, and stores both with every data record.

### Key features

- 4-port symmetrical wind-noise reduction manifold
- Differential sensing against a controlled reference chamber (220 mL design target)
- Interchangeable calibrated capillary / restrictor for pneumatic characterization
- Low-noise differential AFE + 24-bit-compatible ΔΣ ADC (nominal 100 SPS)
- Temperature, humidity and 3-axis vibration monitoring
- Physics-based DSP: FFT, PSD, spectrogram, RMS, STA/LTA, coherence
- **Pneumatic self-diagnostic** – detects possible blockage / leakage from time-constant drift
- **Measurement-Quality Engine** – scores each window GOOD / DEGRADED / UNTRUSTED
- Calibration-aware data export (CSV + HDF5 + JSON metadata)

### Key formulas

| Quantity | Expression |
|---|---|
| Pneumatic cut-off | `fc = 1 / (2π · Rp · Cp)` |
| Pressure from voltage | `P = (Vout − Voffset) / S` |
| SNR | `SNR = 20·log10(P_signal,rms / P_noise,rms)` |

## Repository structure

```
ADMI-X/
├── src/
│   └── admix_dashboard.py     # Scientific dashboard (PyQt5 + pyqtgraph)
├── docs/
│   ├── SIH2026-THE_SONICTRACE.pdf     # SIH idea-submission presentation
│   └── FINAL_SOLUTION-SIH2026.docx    # Master technical solution document
├── requirements.txt
├── LICENSE
└── README.md
```

## Getting started

```bash
git clone https://github.com/<your-username>/ADMI-X.git
cd ADMI-X
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python src/admix_dashboard.py
```

The dashboard starts with the built-in **Simulator** (synthetic microbarom, swell, wind
noise, periodic infrasonic events, plus temperature/humidity/vibration), so it runs with
no hardware attached.

### Dashboard capabilities

- Live pressure trace, PSD, spectrogram
- STA/LTA event detection with event log
- Measurement-quality score with per-check status (saturation, SNR, vibration coupling,
  temperature drift, pneumatic response)
- Pneumatic time-constant / cut-off tracking with a fault-injection demo
- Dark / light theme
- **Export CSV / HDF5** with a `_meta.json` (calibration ID, gain, config, quality state)

### Connecting the STM32 (serial source)

Select the serial source in the dashboard. The firmware should output one ASCII line per
sample at `FS` Hz (default 100; edit `FS` in `admix_dashboard.py` to match):

```
P,<pressure_Pa>,<temp_C>,<humidity_%>,<ax_g>,<ay_g>,<az_g>\n
```

Baud rate: 115200.

## Measurement chain

```
Atmospheric pressure → 4-port WNRS → Differential pressure sensor ⇄ Reference chamber
+ pneumatic restrictor → Low-noise AFE → Anti-alias filter → 24-bit ΔΣ ADC → STM32
→ Temp/Vibration monitoring → Calibration + transfer function → DSP → Measurement-
Quality Engine → Pneumatic self-diagnostic + event detection → Dashboard + calibrated data
```

## Project status

**~50 % of the prototype is built.** Testing and validation (frequency response, noise,
calibration, temperature, wind, stability) are the next stages.
All performance figures are **design targets pending experimental validation**.

Roadmap: **V1** proof-of-physics → **V2** precision prototype (optimized pneumatics, PCB,
thermal/vibration control) → **V3** research platform with matched sensors and
synchronized arrays.

## References

1. *Topographically Scattered Infrasound Waves Observed on Microbarometer Arrays in the Lower Stratosphere*, Earth and Space Science, 2022 — https://doi.org/10.1029/2022EA002226
2. *Performance Comparison of Measurement Microphones and Microbarometers … near Wind Power Plants*, J. Low Freq. Noise Vib. Active Control, 2024 — https://doi.org/10.1177/14613484231200852
3. *On-Site Infrasound Calibration to Correct Wave Parameter Estimation*, JASA, 2024 — https://doi.org/10.1121/10.0025131
4. *An Accurate and Automated Convective Vortex Detection Method for Long-Duration Infrasound Microbarometer Data*, JAOT, 2024 — https://doi.org/10.1175/JTECH-D-23-0037.1
5. *Cyclic Background Noise Variations on Infrasound Microbarometers From Micrometeorology and Human Activity*, GRL, 2025 — https://doi.org/10.1029/2025GL116415

## License

MIT — see [LICENSE](LICENSE).
