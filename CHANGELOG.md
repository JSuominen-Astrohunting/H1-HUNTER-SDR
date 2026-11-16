# Changelog

## v1.0 – 2026-10

First public release.

- RTL-SDR receiver with RF spectrum and waterfall; simulator for use without hardware
- Cold → Hot → Measure Y-factor calibration with a computed Cold-sky model and automatic spillover model
- Bandpass removal by the Cold spectrum or a smoothed bandpass model (narrow Cold spikes removed before smoothing)
- Interference handling: time-domain flagging, Remove flagged, narrow RFI channel search (RFI filter modes),
  user excluded ranges, optional spectral kurtosis flagging per channel
- Gain drift correction (line-free channels) and Dicke-type reference load relay
- Hydrogen line view with line temperature (default), antenna temperature, ratio to Cold, raw power and T_B (η = 1.0 by default, verified against the LAB survey);
  Hold last average; info text
- Maps: RA/Dec, Galactic, polar, channel and moment maps, l–v diagram, spectra by l, Milky Way top view,
  rotation curve, enclosed mass
- Guided pointing with ASCOM (Windows) and INDI (Linux) mounts, read only
- Optional temperature sensor for the Hot load (DS18B20, USB serial, ASCOM / INDI)
- Saved data with full metadata, science package, FITS and ezRA export, raw IQ in SigMF format
- Raw IQ station packets (GPS synchronized clock) for H1-HUNTER INTERFEROMETER; the correlator itself is not part
  of H1-HUNTER SDR (it is the separate program H1-HUNTER INTERFEROMETER)
- User manual (English / Finnish) and Methods document
- Step-by-step installation instructions for Windows, Fedora, Ubuntu and Raspberry Pi
