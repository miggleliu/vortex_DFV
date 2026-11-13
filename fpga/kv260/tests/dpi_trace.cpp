// SPDX-License-Identifier: Apache-2.0
// Standalone --binary bench has no waveform controller. The upstream utility
// DPI library expects these hooks even when the test does not request tracing.
bool sim_trace_enabled() { return false; }
void sim_trace_enable(bool) {}
