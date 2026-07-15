import numpy as np
import asyncio
import logging
from scipy import signal as scipy_signal
from collections import deque

logger = logging.getLogger(__name__)


class ExtremeAudioProcessor:
    """
    Processes raw PCM audio with extreme amplification:
      - Volume: up to 1000x
      - Gain: any dB value
      - Bass: lowshelf EQ with extreme boost
      - Clarity: high-frequency boost
      - Soft-clip limiter to prevent harsh distortion
    """

    def __init__(
        self,
        volume: float = 400.0,
        gain_db: float = 150.0,
        bass_db: float = 100.0,
        clarity_db: float = 100.0,
        sample_rate: int = 48000,
    ):
        self.volume = volume
        self.gain_db = gain_db
        self.bass_db = bass_db
        self.clarity_db = clarity_db
        self.sample_rate = sample_rate

        # Convert gain from dB to linear
        # gain_linear = 10^(gain_db/20) * volume
        self.total_gain_linear = volume * (10 ** (gain_db / 20))

        # Build EQ filters
        self._build_filters()

        # Soft-clipping parameters
        self.threshold = 0.85       # Start soft-clipping at 85% of max
        self.clip_slope = 0.3       # How hard the clip knee is

        # Rolling stats for dynamic adjustment
        self.peak_history = deque(maxlen=10)

    def _build_filters(self):
        """Design IIR filters for bass and clarity boost."""
        # Bass: Low-shelf filter at 100Hz
        if self.bass_db != 0:
            self.bass_filter = self._design_lowshelf(
                gain_db=self.bass_db,
                freq=100.0,
                q=0.7,
            )
        else:
            self.bass_filter = None

        # Clarity: Peaking EQ at 2kHz
        if self.clarity_db != 0:
            self.clarity_filter = self._design_peaking(
                gain_db=self.clarity_db,
                freq=2000.0,
                q=1.0,
            )
        else:
            self.clarity_filter = None

    def _design_lowshelf(self, gain_db: float, freq: float, q: float = 0.7):
        """Design a low-shelf biquad filter."""
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * freq / self.sample_rate
        alpha = np.sin(w0) / (2 * q)

        cos_w0 = np.cos(w0)

        b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * np.sqrt(A) * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
        b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * np.sqrt(A) * alpha)
        a0 = (A + 1) + (A - 1) * cos_w0 + 2 * np.sqrt(A) * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
        a2 = (A + 1) + (A - 1) * cos_w0 - 2 * np.sqrt(A) * alpha

        # Normalize
        b = np.array([b0, b1, b2]) / a0
        a = np.array([a0, a1, a2]) / a0
        return b, a

    def _design_peaking(self, gain_db: float, freq: float, q: float = 1.0):
        """Design a peaking EQ biquad filter."""
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * freq / self.sample_rate
        alpha = np.sin(w0) / (2 * q)

        cos_w0 = np.cos(w0)

        b0 = 1 + alpha * A
        b1 = -2 * cos_w0
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * cos_w0
        a2 = 1 - alpha / A

        b = np.array([b0, b1, b2]) / a0
        a = np.array([a0, a1, a2]) / a0
        return b, a

    def _apply_biquad(self, data: np.ndarray, b: np.ndarray, a: np.ndarray):
        """Apply a biquad filter to the data using scipy."""
        return scipy_signal.lfilter(b, a, data).astype(np.float64)

    def _soft_clip(self, data: np.ndarray) -> np.ndarray:
        """
        Apply soft-clipping to prevent harsh digital distortion.
        Uses a polynomial knee curve.
        """
        # Track peak for stability monitoring
        peak = np.max(np.abs(data))
        self.peak_history.append(peak)

        # Apply soft-clipping
        # Below threshold: linear
        # Above threshold: smooth knee
        sign = np.sign(data)
        abs_data = np.abs(data)

        # Soft clip using tanh-like curve
        # Data below threshold passes through, above gets compressed
        mask_normal = abs_data <= self.threshold
        mask_clip = abs_data > self.threshold

        result = data.copy()
        # Normal region: apply a slight compression curve for safety
        result[mask_normal] = data[mask_normal] * 0.95

        # Clipping region: smooth rolloff
        if np.any(mask_clip):
            clipped = abs_data[mask_clip]
            # Apply a log-like compression above threshold
            compressed = self.threshold + (
                1.0 - self.threshold
            ) * np.tanh((clipped - self.threshold) / (1.0 - self.threshold) * 2)
            result[mask_clip] = sign[mask_clip] * compressed * 0.95

        return result

    def process_audio_block(self, pcm_bytes: bytes) -> bytes:
        """
        Process a block of raw PCM s16le audio.
        Returns processed PCM s16le bytes.
        """
        if not pcm_bytes or len(pcm_bytes) < 4:
            return pcm_bytes

        # Convert bytes to numpy array (int16 → float64)
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)

        if len(samples) == 0:
            return pcm_bytes

        # Normalize to [-1.0, 1.0]
        samples = samples / 32768.0

        # Apply EQ filters
        if self.bass_filter is not None:
            samples = self._apply_biquad(samples, *self.bass_filter)

        if self.clarity_filter is not None:
            samples = self._apply_biquad(samples, *self.clarity_filter)

        # Apply total gain (volume * gain_db)
        samples = samples * self.total_gain_linear

        # Soft-clip to prevent digital distortion
        samples = self._soft_clip(samples)

        # Convert back to int16
        samples = np.clip(samples, -1.0, 1.0)
        samples_int16 = (samples * 32767).astype(np.int16)

        return samples_int16.tobytes()

    def update_settings(
        self,
        volume: float = None,
        gain_db: float = None,
        bass_db: float = None,
        clarity_db: float = None,
    ):
        """Update processing parameters on the fly."""
        if volume is not None:
            self.volume = volume
        if gain_db is not None:
            self.gain_db = gain_db
        if bass_db is not None:
            self.bass_db = bass_db
            self._build_filters()
        if clarity_db is not None:
            self.clarity_db = clarity_db
            self._build_filters()

        self.total_gain_linear = self.volume * (10 ** (self.gain_db / 20))
        logger.info(
            f"Updated audio: volume={self.volume}x, "
            f"gain={self.gain_db}dB, "
            f"total_linear={self.total_gain_linear:.1f}x, "
            f"bass={self.bass_db}dB, "
            f"clarity={self.clarity_db}dB"
        )


class AudioCaptureProcessor:
    """
    Captures audio from a source (microphone, pipe, or file),
    processes through ExtremeAudioProcessor,
    and yields processed PCM chunks.
    """

    def __init__(self, processor: ExtremeAudioProcessor):
        self.processor = processor

    async def process_stream(self, audio_generator):
        """
        Wraps an async generator that yields PCM bytes,
        processes each chunk, and yields the result.
        """
        async for chunk in audio_generator:
            processed = self.processor.process_audio_block(chunk)
            yield processed
