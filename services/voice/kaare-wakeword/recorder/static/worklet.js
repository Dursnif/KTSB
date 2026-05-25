/**
 * AudioWorklet processor for recording raw PCM samples.
 * Sends Float32Array chunks to the main thread via port.
 */
class RecorderWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this._recording = false;
    this.port.onmessage = (e) => {
      if (e.data === 'start') this._recording = true;
      if (e.data === 'stop') this._recording = false;
    };
  }

  process(inputs) {
    if (this._recording && inputs[0] && inputs[0][0]) {
      this.port.postMessage(new Float32Array(inputs[0][0]));
    }
    return true;
  }
}

registerProcessor('recorder-worklet', RecorderWorklet);
