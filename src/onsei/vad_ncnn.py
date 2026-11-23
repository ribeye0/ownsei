"""Offline FireRedVAD ncnn graph, using the same Kaldi features as the training frontend."""

from contextlib import chdir

import kaldi_native_fbank as knf
import ncnn
import numpy as np


class Detector:
    def __init__(self, directory):
        self.means = np.fromfile(directory / "cmvn_means.bin", dtype="<f4")
        self.istd = np.fromfile(directory / "cmvn_istd.bin", dtype="<f4")
        self.options = knf.FbankOptions()
        self.options.frame_opts.samp_freq = 16000
        self.options.frame_opts.frame_length_ms = 25
        self.options.frame_opts.frame_shift_ms = 10
        self.options.frame_opts.snip_edges = True
        self.options.frame_opts.dither = 0
        self.options.mel_opts.num_bins = 80
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = False
        self.net.opt.num_threads = 1
        # This runs in a dedicated worker. ASCII relative names also work with
        # Windows builds whose native reader cannot open a Unicode path.
        with chdir(directory):
            if self.net.load_param("firered_vad_non_stream.ncnn.param") or self.net.load_model(
                "firered_vad_non_stream.ncnn.bin"
            ):
                raise ValueError("Unable to load the offline FireRedVAD ncnn model")

    def probabilities(self, pcm):
        fbank = knf.OnlineFbank(self.options)
        fbank.accept_waveform(16000, pcm.tolist())
        count = fbank.num_frames_ready
        if not count:
            return np.empty(0, dtype=np.float32)
        features = np.stack([fbank.get_frame(i) for i in range(count)])
        features -= self.means
        features *= self.istd
        # The exported lookahead crop needs at least two frames. Only real
        # frames own timestamps, even for an isolated 25 ms input.
        if count == 1:
            features = np.pad(features, ((0, 1), (0, 0)))
        with self.net.create_extractor() as extractor:
            if extractor.input("in0", ncnn.Mat(features).clone()):
                raise ValueError("Invalid FireRedVAD ncnn input")
            status, output = extractor.extract("out0")
            if status or np.asarray(output).shape != (len(features), 1):
                raise ValueError("Unexpected FireRedVAD ncnn output shape")
            return np.array(output).reshape(-1)[:count]
