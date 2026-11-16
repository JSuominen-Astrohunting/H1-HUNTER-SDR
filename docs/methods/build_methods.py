"""Builds H1-HUNTER_SDR_v1.0_Methods.pdf (technical methods description, English)."""
import sys, os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
                                Image, KeepTogether, PageBreak, NextPageTemplate, CondPageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FD, OUT, FIG = sys.argv[1], sys.argv[2], sys.argv[3]
for n, f in (("Plex", "IBMPlexSans-Regular.ttf"), ("Plex-SB", "IBMPlexSans-SemiBold.ttf"),
             ("Plex-B", "IBMPlexSans-Bold.ttf"), ("Mono", "IBMPlexMono-Regular.ttf")):
    pdfmetrics.registerFont(TTFont(n, os.path.join(FD, f)))
pdfmetrics.registerFontFamily("Plex", normal="Plex", bold="Plex-B", italic="Plex", boldItalic="Plex-B")

INK = colors.HexColor("#1d1f23"); MUTED = colors.HexColor("#5d6470"); ACC = colors.HexColor("#a36b00")
LINE = colors.HexColor("#d4d8de"); BOX = colors.HexColor("#f4f5f7")
body = ParagraphStyle("b", fontName="Plex", fontSize=9.6, leading=13.6, textColor=INK, spaceAfter=5)
small = ParagraphStyle("s", parent=body, fontSize=8.4, leading=11.2, textColor=MUTED)
cell = ParagraphStyle("c", parent=body, fontSize=8.4, leading=11, spaceAfter=0)
cellb = ParagraphStyle("cb", parent=cell, fontName="Plex-SB")
h1 = ParagraphStyle("h1", fontName="Plex-B", fontSize=14, leading=18, textColor=INK, spaceBefore=12, spaceAfter=6)
h2 = ParagraphStyle("h2", fontName="Plex-SB", fontSize=10.8, leading=14, textColor=INK, spaceBefore=8, spaceAfter=3)
eq = ParagraphStyle("eq", parent=body, fontName="Plex", fontSize=9.4, leading=12.5, leftIndent=12, spaceBefore=2,
                    spaceAfter=6, textColor=INK)
bul = ParagraphStyle("bul", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=2)
title = ParagraphStyle("t", fontName="Plex-B", fontSize=22, leading=27, textColor=INK)
sub = ParagraphStyle("st", fontName="Plex", fontSize=12.5, leading=17, textColor=MUTED)

S = []
P = lambda t, st=body: S.append(Paragraph(t, st))
H1 = lambda t: (S.append(CondPageBreak(35 * mm)), S.append(Paragraph(t, h1)))
H2 = lambda t: (S.append(CondPageBreak(28 * mm)), S.append(Paragraph(t, h2)))
EQ = lambda t: S.append(Paragraph(t, eq))
def B(items):
    for t in items:
        S.append(Paragraph(t, bul, bulletText="•"))
    S.append(Spacer(1, 3))
def T(rows, widths, head=True):
    data = [[Paragraph(str(c), cellb if (head and i == 0) else cell) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), BOX))
    t.setStyle(TableStyle(st))
    S.append(KeepTogether([t]) if len(rows) < 12 else t); S.append(Spacer(1, 7))

# ---------------------------------------------------------------- title page
S.append(Spacer(1, 50 * mm))
S.append(Paragraph("H1-HUNTER SDR v1.0 \u2013 Methods", title))
S.append(Spacer(1, 4))
S.append(Paragraph("Measurement, calibration and interference handling", sub))
S.append(Spacer(1, 16 * mm))
P("Jarkko Suominen &nbsp;·&nbsp; Pori, Finland &nbsp;·&nbsp; October 2026")
S.append(Spacer(1, 8 * mm))
P("This document describes how H1-HUNTER SDR turns RTL-SDR samples into calibrated 21 cm hydrogen line spectra: "
  "the spectral estimation, the bandpass removal, the Y-factor calibration and its sky model, the baseline and "
  "velocity scale, and every step that flags or removes interference. All formulas, thresholds and default values "
  "are taken from the program source (version 1.0). Nothing is removed from the saved raw data; every correction "
  "is optional, documented in the file metadata and can be repeated from the saved files.", small)
S.append(NextPageTemplate("body")); S.append(PageBreak())

# ---------------------------------------------------------------- 1
H1("1. Processing chain")
P("Each averaged spectrum (a <i>point</i>) passes through the steps below, in this order. Steps in brackets are "
  "optional and off unless the user enables them.")
T([["#", "Step", "Where"],
   ["1", "IQ block → DC removal → windowed FFT frames (32-bit) → power per channel", "receiver"],
   ["2", "Per-frame interference test (whole band + 16 sub-bands, robust z-score); flagged frames stay in the sum", "receiver"],
   ["3", "Sum of N frames (64-bit) = average; flagged frames stored separately", "averager"],
   ["3b", "[Spectral kurtosis] each channel tested in blocks of 1024 frames (frames not flagged in time); sums of the flagged channel blocks stored", "averager"],
   ["4", "[Remove flagged] average recomputed per channel without the flagged frames and SK channel blocks (exact); Cold and Hot are treated in the same way", "view / saving"],
   ["5", "[Reference relay] division by the gain factor g from the Dicke reference", "view / saving"],
   ["6", "Ratio to the Cold reference R = P / P<sub>cold</sub>: the bandpass (M-curve) is removed by division; [Smooth Cold] uses a model of P<sub>cold</sub>", "products"],
   ["7", "[Remove flagged] narrow RFI channels + user excluded ranges left out of all fits and sums", "products"],
   ["8", "[Gain drift correction] additional division: R / median(R) over line-free channels", "products"],
   ["9", "Y-factor calibration: T<sub>A</sub> = (T<sub>sys</sub> + T<sub>cold</sub>)·R − T<sub>sys</sub>", "products"],
   ["10", "Linear baseline outside the line window removed by subtraction: T<sub>L</sub> = T<sub>A</sub> − baseline; T<sub>B</sub> = T<sub>L</sub> / η<sub>mb</sub>", "products"],
   ["11", "Velocity axes (topocentric, LSR); line integral; atmosphere correction for the maps", "products / maps"]],
  [8, 138, 26])
P("With the default settings a level change between Cold and the measurement is removed only by the baseline "
  "subtraction (step 10); a division by a gain factor g is added only when Gain drift correction (step 8) or the "
  "reference relay (step 5) is on. The bandpass is always removed by division (step 6).", small)

# ---------------------------------------------------------------- 2
H1("2. Spectral estimation")
H2("2.1 Samples, FFT and scaling")
P("The RTL-SDR delivers 8-bit complex samples (AGC always off; gain chosen from the tuner's own steps). Each block is "
  "processed in 32-bit floating point; frames of N<sub>FFT</sub> samples (default 512) start every N<sub>FFT</sub>/2 "
  "samples (50 % overlap, default) and are multiplied by a window (Hann default; Blackman-Harris, Hamming, "
  "Rectangular). The power of channel k is")
EQ("P[k] = |FFT(w·x)[k]|² / (Σ w)²")
P("so that a full-scale complex tone gives 1.0 (0 dBFS). The frames are summed in 64-bit precision. With 2.4 MS/s "
  "and N<sub>FFT</sub> = 512 the channel width is 4.6875 kHz (0.99 km/s).")
H2("2.2 DC spike, LO offset and frequency axis")
B(["<b>Remove DC from IQ</b> (default on): the block mean is subtracted before the FFT.",
   "<b>DC notch</b> (default ±3 kHz): the central channels are replaced by a straight line between the neighbouring "
   "levels and are not trusted. With <b>offset tuning</b> the DC spike lies outside the band; no notch is used.",
   "<b>LO offset</b>: the stick is tuned away from the centre frequency and the spectra are shifted back by whole "
   "channels; channels shifted in from the other edge were never measured and are NaN.",
   "<b>PPM</b>: the integer part is set in the tuner; the fractional part is applied to the frequency axis. "
   "1 ppm ≈ 0.3 km/s. The value is stored in every file.",
   "<b>Trusted band</b>: channels within 0.4·f<sub>s</sub> of the real tuned frequency (central 80 %) and outside the "
   "DC notch (+2.5 channels). Only trusted channels are used in any fit, statistic or sum."])
H2("2.3 Averaging and the radiometer check")
P("One average contains K frames (default 1,050,000, about 112 s at 2.4 MS/s with 50 % overlap). The same K is used "
  "for Cold, Hot and every measurement point. For Gaussian noise the relative standard deviation per channel is")
EQ("σ<sub>exp</sub> = sqrt( v₁ / K  +  v₁ / K<sub>cold</sub> )")
P("where v₁ accounts for the correlation of overlapping windowed frames (computed from the window: v₁ = 1.056 for "
  "Hann with 50 % overlap). The second term is the noise of the Cold reference; it is omitted when the smoothed Cold "
  "reference is used. The measured σ is estimated from differences of neighbouring channels, robustly (MAD × 1.4826), "
  "corrected for the correlation between neighbouring channels caused by the window. The <i>Radiometer check</i> "
  "plots measured and expected σ against K: a measured curve that follows 1/√K shows that the average is limited by "
  "thermal noise, not by interference or drift. Before a Cold exists (and for the Cold itself) the check can only use "
  "the raw spectrum, whose bandpass slope and stable fine structure look like noise in a single spectrum; it is then "
  "shown as a guide only and saved with Radiometer_basis = raw spectrum. Example (real RTL-SDR data, K = 1,050,000, "
  "expected 0.100 %): raw Cold 0.326 %; measurement / measured Cold 0.141 % against 0.142 % expected.")

# ---------------------------------------------------------------- 3
H1("3. Bandpass (M-curve) removal")
P("The receiver's bandpass (the 'M curve' of the RTL-SDR filters) is removed by dividing every spectrum by the Cold "
  "spectrum, which is a measurement of the bandpass on nearly featureless sky:")
EQ("R(ν) = P(ν) / P<sub>cold</sub>(ν)")
P("Because the bandpass is the same in every frame, dividing the average is identical to dividing each frame before "
  "averaging. The ratio R is the basis of all later steps (calibration, baseline, line integral, RFI channel search). "
  "Stable receiver spurs present in both P and P<sub>cold</sub> cancel in R.")
H2("3.1 Smoothed Cold reference (optional)")
P("When enabled, P<sub>cold</sub> is replaced by a model of the bandpass, so that the noise of the Cold spectrum does "
  "not enter R (up to √2 lower noise when Cold and measurement have equal integration). This holds only if the "
  "bandpass has no stable structure finer than the smoothing scales: such structure cancels in P / P<sub>cold</sub> but "
  "remains in P / model. On the author's RTL-SDR the noise of R was 0.219 % with the model and 0.141 % with the "
  "measured Cold (expected 0.142 %), so the measured Cold is better there; the radiometer check shows which is better "
  "for a given receiver. Algorithm, trusted channels only:")
B(["Narrow spikes and dips of the Cold spectrum are found: relative deviation from a 7-channel running median larger "
   "than 6σ, where σ is estimated from second differences (so the bandpass slope does not count as noise); only runs "
   "of ≤ 3 channels (or a ≤ 3+3 up/down pair) are accepted; they are widened by one channel and interpolated over.",
   "Running median over 25 kHz, then a cubic Savitzky-Golay fit over 60 kHz.",
   "Untrusted channels keep the measured values. The measured Cold is always saved unchanged."])
P("With smoothing, receiver spurs no longer cancel in R (the model has none); they appear in R and are handled by the "
  "RFI channel search (section 6.4). Features wider than the smoothing scales, including the Cold sky's own weak "
  "hydrogen emission, remain in the model. If the setting is changed after Hot, the user is asked to repeat Hot so "
  "that Y and T<sub>sys</sub> use the same reference.")

# ---------------------------------------------------------------- 4
H1("4. Calibration")
H2("4.1 Cold sky model")
P("The Cold spectrum is taken at empty sky; its brightness temperature is computed, not assumed:")
EQ("T<sub>cold</sub> = T<sub>CMB</sub> + T<sub>gal</sub>(b) + T<sub>atm</sub>(alt) + T<sub>spill</sub>(alt, D)")
B(["T<sub>CMB</sub> = 2.725 K.",
   "T<sub>gal</sub> = 0.7 K + 4.0 K · exp(−|b| / 8°) (1420 MHz continuum, simple latitude model).",
   "T<sub>atm</sub> = T<sub>air</sub> · (1 − e<super>−τ·X</super>), τ = 0.0075 Np · exp(−h / 8400 m), X = 1 / sin(max(alt, 5°)).",
   "T<sub>spill</sub>: ground radiation at the air temperature, computed from the altitude and the dish diameter D "
   "(section 4.2)."])
P("The user is asked to confirm a Cold pointing with |b| &lt; 20° or altitude &lt; 20°, and the result is marked "
  "(yellow) when the altitude is &lt; 45° or |b| &lt; 30°. Example, D = 0.9 m, T<sub>air</sub> = 279 K, b = −15°, h = 15 m:")
T([["Altitude", "CMB", "Galaxy", "Atmosphere", "Spillover", "T<sub>cold</sub>"],
   ["30°", "2.73", "1.32", "4.15", "8.75", "16.95 K"],
   ["45°", "2.73", "1.32", "2.94", "5.76", "12.75 K"],
   ["60°", "2.73", "1.32", "2.40", "4.57", "11.02 K"],
   ["75°", "2.73", "1.32", "2.16", "3.77", "9.97 K"],
   ["90°", "2.73", "1.32", "2.08", "2.99", "9.12 K"]], [24, 22, 22, 26, 26, 26])
H2("4.2 Spillover model")
P("Three parts, Gaussian main beam with FWHM = 70·λ/D (λ = 0.211 m; 16.4° for D = 0.9 m):")
B(["rim spillover share s<sub>rim</sub> = 0.0214 (gives 3 K at the zenith); half of it sees the ground at the zenith, "
   "all of it at the horizon: s<sub>rim</sub>·(0.5 + 0.5·cos alt);",
   "near sidelobes, share 0.15, Gaussian of 3× the main-beam width: the fraction below the horizon;",
   "main beam, the rest of the power: the fraction below the horizon."])
EQ("T<sub>spill</sub> = T<sub>air</sub> · min(1, rim + 0.15·f<sub>side</sub>(alt) + (1 − 0.15 − s<sub>rim</sub>)·f<sub>main</sub>(alt))")
S.append(Image(FIG, width=150 * mm, height=72.6 * mm)); S.append(Spacer(1, 4))
P("The model is an estimate; the real values depend on the feed illumination and the surroundings.", small)
H2("4.3 Hot load, Y factor and system temperature")
P("Hot is taken on the ground or an absorber at the air temperature T<sub>hot</sub> = T<sub>air</sub>. Its source, "
  "in this order: (1) a value typed by the user; (2) an optional temperature sensor - DS18B20 on the Raspberry Pi "
  "1-Wire bus, a USB serial device printing °C, or an ASCOM (Windows) / INDI (Linux) weather station or focuser; a "
  "reading older than 5 min is not used; (3) an offline seasonal estimate (about ±10 K). The source is written in "
  "the metadata (Hot_note). A 10 K error in T<sub>hot</sub> scales all temperatures by about 10 / (T<sub>hot</sub> − "
  "T<sub>cold</sub>) ≈ 3.7 %. The Y factor is the median of the ratio over trusted, line-free channels (outside the "
  "line window):")
EQ("Y = median( P<sub>hot</sub> / P<sub>cold</sub> ),&nbsp;&nbsp; T<sub>sys</sub> = (T<sub>hot</sub> − Y·T<sub>cold</sub>) / (Y − 1)")
P("Hot is rejected when Y ≤ 1.01. Cold and Hot are valid only for identical receiver settings (frequency, LO offset, "
  "sample rate, gain, PPM, Bias-T, FFT size, window, overlap, DC removal, DC notch, offset tuning); any change requires new calibrations. "
  "Cold or Hot is rejected when more than 0.1 % of the samples are clipped. A Cold older than 60 min gives a warning "
  "before measuring.")
H2("4.4 Antenna and brightness temperature")
EQ("T<sub>A</sub>(ν) = (T<sub>sys</sub> + T<sub>cold</sub>) · R(ν) − T<sub>sys</sub>")
P("This follows from P being proportional to T<sub>sys</sub> + T<sub>A</sub> with the same gain in Cold and the measurement. Without Hot, "
  "R is shown (ratio to Cold, no Kelvin scale). The brightness temperature is T<sub>B</sub> = T<sub>L</sub> / η with "
  "η = 1.0 by default: Galactic HI is extended and fills the main beam and the sidelobes, so the line's antenna "
  "temperature is its brightness temperature averaged over the beam. This was verified against the LAB survey at the "
  "program's beam (5.11.2026, 0.73 m dish, beam 20°: ratio 0.98-1.08). A main-beam efficiency (about 0.5-0.7 for "
  "small dishes) applies only to sources smaller than the beam and is not used. A value measured against a survey can "
  "be entered. No stray-radiation correction is applied. For the maps the line is corrected for atmospheric attenuation by "
  "exp(τ·X).")

# ---------------------------------------------------------------- 5
H1("5. Baseline, line and velocity")
B(["<b>Line window</b>: ±0.8 MHz around 1420.405752 MHz by default, narrowed automatically so that baseline channels "
   "remain (half width = min(0.8, max(0.2, gap − 0.35)) MHz), or set by the user.",
   "<b>Baseline</b>: straight line (least squares) fitted to trusted, non-RFI channels at least 0.2 MHz outside the "
   "line window (fallback: just outside it). T<sub>L</sub> = T<sub>A</sub> − baseline.",
   "<b>Line integral</b>: Σ T<sub>L</sub>·Δν over the channels in the window (K·MHz; × c/ν₀ for K·km/s). Channels removed as "
   "interference (6.2, 6.4, 6.5) are replaced by linear interpolation between their neighbours, not left out of the sum.",
   "<b>Velocity</b>: radio definition v = c·(ν₀ − ν)/ν₀ (topocentric). LSR: barycentric correction (astropy, offline "
   "IERS tables) + standard solar motion 20 km/s towards RA 270.959°, Dec +30.005° (J2000). The correction is written "
   "in every file.",
   "<b>Readouts</b>: peak T<sub>L</sub> from a 31-channel running mean; noise σ from neighbour differences (MAD) "
   "outside the line window."])

# ---------------------------------------------------------------- 6
H1("6. Interference handling")
P("Raw data is never altered. Interference is handled in four independent layers, all reversible.")
H2("6.1 Spectrum (time-domain) flagging")
P("For every FFT frame the mean power of the trusted band and of 16 equal sub-bands is computed. Each of these 17 "
  "values is compared with a running reference (median and MAD of the last 2048 frames, ≥ 256 needed):")
EQ("z = max over 17 values of (v − median) / (1.4826·MAD),&nbsp;&nbsp; flagged if z &gt; 5 (Flag limit sigma)")
P("Only increases are flagged (impulsive RFI adds power). Flagged frames are <b>kept</b> in the average. The first "
  "400 flagged frames of an average are stored as full spectra, the rest are summed (count and sum), so the "
  "removal below is exact.")
H2("6.2 Spectral kurtosis flagging (optional, off by default)")
P("Each channel is tested separately in blocks of M = 1024 frames (about 0.11 s at 2.4 MS/s, N<sub>FFT</sub> = 512, "
  "50 % overlap), using only the frames that are not already flagged in time (6.1):")
EQ("SK = (M + 1)/(M − 1) · (M·S₂ / S₁² − 1),&nbsp;&nbsp; S₁ = Σ P, S₂ = Σ P²&nbsp;&nbsp; (Nita &amp; Gary 2010, d = 1)")
P("For Gaussian noise SK = 1 with σ = sqrt(4M² / ((M−1)(M+2)(M+3))) = 0.0624. The distribution is skewed; the bounds "
  "0.836 and 1.214 are the Pearson type III quantiles for 0.135 % false alarms on each side (the rate of 3σ). A block "
  "with fewer than M frames (frames flagged in time) uses bounds scaled by sqrt(1024 / M); a block with fewer than 512 "
  "is not tested. Flagged channel blocks are removed from that channel only; their sum and number are stored per "
  "channel. A channel that loses more than 50 % of its spectra is left out completely with the RFI channels.")
P("The 21 cm line is itself Gaussian noise, so SK does not respond to it. Checked by simulation through the program's "
  "own windowed, overlapping FFT:")
T([["Test (simulated)", "Result"],
   ["Gaussian noise, 200 blocks × 409 channels", "mean SK 0.9998, σ 0.0624; false alarms 0.31 % (expected 0.27 %)"],
   ["Hydrogen line +40 % above the noise", "flag rate in line channels 0.17 %, off-line 0.32 %"],
   ["Hydrogen line +150 % above the noise", "flag rate in line channels 0.21 %, off-line 0.31 %"],
   ["Line shape after removal (noise only)", "relative bias line vs off-line −0.012 %"],
   ["Pulsed carrier (2 ms every 20 ms)", "channel removed completely; neighbours untouched"],
   ["Weak steady carrier", "not detected by SK (left to 6.4)"]], [70, 102])
H2("6.3 Remove flagged")
EQ("P<sub>clean</sub>(ν) = (Σ<sub>all</sub> P − Σ<sub>flagged</sub> P − Σ<sub>SK</sub>(ν)) / (K − n<sub>flagged</sub> − n<sub>SK</sub>(ν))")
P("Applied in the same way to Cold, Hot and the measurement, so that the treatment cancels in R and in Y; switching "
  "Remove flagged recomputes Y and Tsys from the stored Cold and Hot averages. power_raw in the files always includes the flagged frames; power_used is the cleaned "
  "spectrum.")
H2("6.4 Narrow RFI channel search (with Remove flagged)")
P("Run on R (section 3), trusted channels only, up to three passes:")
B(["work = R with already removed channels interpolated over; r = work − median<sub>7</sub>(work); "
   "broad = work − median<sub>31</sub>(work); σ = 1.4826·MAD(r).",
   "Candidate channel: |r| &gt; n<sub>σ</sub>·σ, sign(r) = sign(broad) and |r| &gt; 0.7·|broad| (most of the excess is "
   "narrow; a broad hydrogen line fails this test).",
   "Accepted runs: a single spike or dip of ≤ W channels, or an adjacent up/down pair with each part ≤ W "
   "(a spur that moved slightly between Cold and the measurement). Accepted runs are widened by one channel.",
   "Later passes cannot grow regions already removed (2-channel exclusion zone). Final hard limit: a region wider "
   "than 2W + 2 channels is never removed.",
   "Removed channels are left out of the baseline fit and the drift factor; in the line integral they are replaced by "
   "interpolation between their neighbours, and the maps interpolate over them. They are "
   "marked in the column rfi_removed."])
T([["Mode", "Widest feature W", "Threshold n<sub>σ</sub>"],
   ["Conservative", "1 channel", "8σ"],
   ["Normal (default)", "3 km/s, at most 3 channels", "6σ"],
   ["Aggressive", "5 km/s, at most 3 channels", "5σ"],
   ["Custom", "1–5 km/s slider, at most 3 channels", "6σ"]], [40, 70, 40])
P("W is converted from km/s to channels with the current channel width and is never more than 3 channels. The "
  "21 cm line (tens of km/s, tens of channels) cannot be removed by this search.")
H2("6.5 User excluded ranges")
P("Fixed frequency ranges typed by the user (e.g. known receiver spurs) are removed together with 6.4 when Remove "
  "flagged is on. They are written in the metadata (RFI_excluded_ranges_MHz) and included in rfi_removed. The program "
  "never adds ranges itself; a range that overlaps the line window is marked in the user interface.")
H2("6.6 Display only")
P("In the plot, removed channels are bridged by linear interpolation so the spectrum has no gaps. This is cosmetic: "
  "it is not used in any computation and is not saved. The on-plot information text states how many spectra and "
  "channels were removed. The RF spectrum and waterfall view use the first 65 536 samples of every block (display "
  "only; the hydrogen line spectra use every sample), and the hydrogen line view is redrawn at most once per second, "
  "so that the processor time goes to the data.")

# ---------------------------------------------------------------- 7
H1("7. Gain drift")
H2("7.1 Line-free normalisation (optional)")
EQ("g = median R over trusted, non-RFI channels with |ν − ν₀| ≥ half + 0.2 MHz;&nbsp;&nbsp; R' = R / g")
P("Removes a common gain change between Cold and the measurement (temperature drift of the receiver). It does not "
  "remove a slope, and it also removes real continuum changes, so it is off by default and written in the metadata "
  "(Gain_drift_g). Division is correct for a gain change, subtraction (the baseline, always made afterwards) for an "
  "additive change; the spectrum alone cannot tell them apart.")
H2("7.2 Dicke-type reference load (optional hardware)")
P("A USB relay switches the LNA input to a resistor. A reference is taken after Cold and then every N sky averages; "
  "the gain factor g = median(P<sub>ref</sub> / P<sub>ref,0</sub>) over the trusted band divides the following sky "
  "spectra. Spectra within 0.5 s of a relay switch are not used.")

# ---------------------------------------------------------------- 8
H1("8. Data and reproducibility")
T([["File", "Content"],
   ["H1_NNNN.csv", "freq_hz, v_topo_kms, v_lsr_kms, power_raw, power_used, spectra_used (with SK), ratio_to_cold, t_ant_K, t_line_K, "
                   "t_b_line_K, trusted, rfi_removed; header with every receiver setting, site, pointing, sky "
                   "position, Cold/Hot values and model terms, flag statistics and every correction used"],
   ["H1_NNNN_flagged.csv", "sum of all frames, each stored flagged frame and (with SK) sk_removed_sum / sk_removed_n per channel: the removal can be repeated exactly"],
   ["H1_NNNN_counts.csv", "time bins of the average (default 0.1 s): spectra, mean and max power, max z, number flagged - for interference analysis"],
   ["H1_cold_*, H1_hot_*, H1_calibrations.json", "calibration spectra (unsmoothed) and the calibration history"],
   ["H1_log_YYYYMMDD.csv", "one row per point: time, RA/Dec, l/b, Az/Alt, counts, line strength, flags"],
   ["iq_YYYYMMDDTHHMMSSZ/", "optional raw IQ of every Cold, Hot and Measure (SigMF cu8, the stick's own bytes): "
                            "iq.sigmf-meta with the phase, the gaps (annotations) and SHA-512; iq_blocks.csv with the "
                            "time of every block, so missing samples are marked"],
   ["Science package", "all of the above, maps, images and settings in one folder with a README; optional FITS cube, "
                       "ezRA data file and raw IQ (SigMF) station packet"]], [52, 120])
P("All files are plain text except the optional raw IQ and FITS files. The program makes no network connections.")

# ---------------------------------------------------------------- 9
H1("9. Known limitations and uncertainties")
B(["Absolute temperature scale ≈ 10–20 %: T<sub>hot</sub> = air temperature (ground emissivity taken as 1), the "
   "estimated air temperature (±10 K if not typed), and the Cold sky and spillover models; η = 1.0 assumes "
   "emission that fills the beam.",
   "The Cold sky contains its own weak hydrogen emission, which is divided out of the measurement; Cold should be taken "
   "at high Galactic latitude.",
   "No stray-radiation correction; small dishes have wide beams (16° for 0.9 m).",
   "Baseline is linear; residual bandpass changes between Cold and the measurement remain as a tilt.",
   "RFI wider than 3 channels is not removed automatically (by design, to protect the line); it can be excluded "
   "manually.",
   "Spectral kurtosis detects pulsed and switching interference only; steady carriers look Gaussian enough to pass. "
   "Its false alarms remove about 0.3 % of clean data evenly (no measurable change of the line shape). Interference "
   "repeating faster than the 0.11 s block length can remove a channel completely.",
   "A temperature sensor measures the air, not the absorber; sunlight or radiative cooling can make the absorber "
   "differ by a few K.",
   "Line shapes and velocities are much more accurate than the absolute scale; comparison with the LAB / HI4PI "
   "surveys is recommended.",
   "Raw IQ station packets (recorded with GPS synchronized clock) are only recorded; H1-HUNTER SDR does not "
   "correlate them (correlation is done in the separate program H1-HUNTER INTERFEROMETER). Time stamps come from the "
   "computer clock, which must be GPS-disciplined at every station (GNSS receiver with PPS, e.g. via gpsd and chrony)."])

def page(c, d):
    c.saveState()
    c.setFont("Plex", 7.8); c.setFillColor(MUTED)
    c.drawString(18 * mm, 10 * mm, "H1-HUNTER SDR v1.0 \u2013 Methods")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, str(d.page))
    c.setStrokeColor(LINE); c.setLineWidth(0.4); c.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    c.restoreState()

doc = BaseDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                      bottomMargin=20 * mm, title="H1-HUNTER SDR v1.0 - Methods", author="Jarkko Suominen",
                      subject="Measurement, calibration and interference handling")
fr = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate("title", [fr]), PageTemplate("body", [fr], onPage=page)])
doc.build(S)
print("ok", OUT)
