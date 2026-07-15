import logging
import subprocess
import asyncio
import os
import signal
import numpy as np

from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality
from pytgcalls.types.input_stream import InputAudioStream
from pytgcalls.exceptions import GroupCallNotFound

from config import (
    active_relays, DEFAULT_VOLUME, DEFAULT_GAIN,
    DEFAULT_BASS, DEFAULT_CLARITY,
    PRIVATE_GROUP_ID, SAMPLE_RATE, CHANNELS,
)
from .audio_processor import ExtremeAudioProcessor

logger = logging.getLogger(__name__)


class VCRelayHandler:
    """
    Handles dual-group VC bridging with extreme audio processing.
    """

    def __init__(self, app: Client, pytgcalls: PyTgCalls):
        self.app = app
        self.pytgcalls = pytgcalls
        self.processors = {}  # target_chat_id -> ExtremeAudioProcessor
        self.ffmpeg_processes = {}  # target_chat_id -> subprocess

    async def _spawn_ffmpeg_capture(self) -> asyncio.subprocess.Process:
        """
        Spawn ffmpeg to capture from the default audio device (pulse/alsa)
        and output raw PCM s16le to stdout.
        """
        # Detect platform and use appropriate audio source
        import sys
        if sys.platform == "linux":
            # Try PulseAudio first, fallback to ALSA
            input_cmd = [
                "ffmpeg",
                "-f", "pulse",
                "-i", "default",
                "-f", "s16le",
                "-ac", str(CHANNELS),
                "-ar", str(SAMPLE_RATE),
                "-acodec", "pcm_s16le",
                "-loglevel", "error",
                "pipe:1",
            ]
        elif sys.platform == "darwin":
            input_cmd = [
                "ffmpeg",
                "-f", "avfoundation",
                "-i", ":0",
                "-f", "s16le",
                "-ac", str(CHANNELS),
                "-ar", str(SAMPLE_RATE),
                "-acodec", "pcm_s16le",
                "-loglevel", "error",
                "pipe:1",
            ]
        else:  # Windows
            input_cmd = [
                "ffmpeg",
                "-f", "dshow",
                "-i", "audio=virtual-audio-capturer",
                "-f", "s16le",
                "-ac", str(CHANNELS),
                "-ar", str(SAMPLE_RATE),
                "-acodec", "pcm_s16le",
                "-loglevel", "error",
                "pipe:1",
            ]

        process = await asyncio.create_subprocess_exec(
            *input_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return process

    async def _spawn_ffmpeg_playback(
        self, input_pipe: asyncio.Queue
    ) -> asyncio.subprocess.Process:
        """
        Spawn ffmpeg that reads raw PCM from a pipe and plays it.
        In practice for pytgcalls, we feed raw PCM bytes directly.
        This is a fallback / alternative approach.
        """
        pass  # We'll use the raw stream approach instead

    async def _raw_audio_generator(self, ffmpeg_process: asyncio.subprocess.Process):
        """
        Reads raw PCM chunks from ffmpeg stdout and yields them.
        Chunk size = 20ms of audio (standard voice frame).
        """
        chunk_size = int(SAMPLE_RATE * 0.02) * CHANNELS * 2  # 20ms in bytes
        # s16le = 2 bytes per sample

        while True:
            chunk = await ffmpeg_process.stdout.read(chunk_size)
            if not chunk or len(chunk) < chunk_size:
                break
            yield chunk

    async def join_and_bridge(
        self,
        target_chat_id: int,
        volume: float = DEFAULT_VOLUME,
        gain_db: float = DEFAULT_GAIN,
        bass_db: float = DEFAULT_BASS,
        clarity_db: float = DEFAULT_CLARITY,
    ):
        """Join both VCs and start the relay with extreme audio processing."""

        # Create the audio processor with extreme settings
        processor = ExtremeAudioProcessor(
            volume=volume,
            gain_db=gain_db,
            bass_db=bass_db,
            clarity_db=clarity_db,
        )
        self.processors[target_chat_id] = processor

        # Start ffmpeg to capture microphone / VC audio
        ffmpeg_proc = await self._spawn_ffmpeg_capture()
        self.ffmpeg_processes[target_chat_id] = ffmpeg_proc

        # Create the raw audio generator with inline processing
        async def processed_audio_generator():
            chunk_size = int(SAMPLE_RATE * 0.02) * CHANNELS * 2
            while True:
                chunk = await ffmpeg_proc.stdout.read(chunk_size)
                if not chunk or len(chunk) < chunk_size:
                    break
                # Apply extreme audio processing
                processed = processor.process_audio_block(chunk)
                yield processed

        try:
            # Join target group VC with our processed audio stream
            await self.pytgcalls.join_group_call(
                target_chat_id,
                MediaStream(
                    # Use the processed generator as audio source
                    InputAudioStream(
                        f="raw",  # Raw PCM
                        input=processed_audio_generator(),
                    ),
                    audio_parameters=AudioQuality.HIGH,
                ),
            )
            logger.info(f"✅ Joined & processing for target: {target_chat_id}")

            active_relays[target_chat_id] = {
                "volume": volume,
                "gain": gain_db,
                "bass": bass_db,
                "clarity": clarity_db,
                "running": True,
            }

            return True

        except GroupCallNotFound:
            logger.error(f"VC not found in target: {target_chat_id}")
            await self._cleanup(target_chat_id)
            return False
        except Exception as e:
            logger.exception(f"Failed: {e}")
            await self._cleanup(target_chat_id)
            return False

    async def _cleanup(self, target_chat_id: int):
        """Clean up processes for a target."""
        if target_chat_id in self.ffmpeg_processes:
            proc = self.ffmpeg_processes[target_chat_id]
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            del self.ffmpeg_processes[target_chat_id]

        if target_chat_id in self.processors:
            del self.processors[target_chat_id]

        if target_chat_id in active_relays:
            del active_relays[target_chat_id]

    async def leave_and_disconnect(self, target_chat_id: int):
        """Leave both VCs and cleanup."""
        try:
            await self.pytgcalls.leave_group_call(target_chat_id)
            await self.pytgcalls.leave_group_call(PRIVATE_GROUP_ID)
        except Exception as e:
            logger.warning(f"Leave error: {e}")

        await self._cleanup(target_chat_id)
        logger.info(f"Disconnected from {target_chat_id}")

    async def update_audio(
        self,
        target_chat_id: int,
        volume: float = None,
        gain_db: float = None,
        bass_db: float = None,
        clarity_db: float = None,
    ):
        """Update audio settings in real-time."""
        proc = self.processors.get(target_chat_id)
        if not proc:
            return False

        proc.update_settings(
            volume=volume,
            gain_db=gain_db,
            bass_db=bass_db,
            clarity_db=clarity_db,
        )

        relay = active_relays.get(target_chat_id)
        if relay:
            if volume is not None:
                relay["volume"] = volume
            if gain_db is not None:
                relay["gain"] = gain_db
            if bass_db is not None:
                relay["bass"] = bass_db
            if clarity_db is not None:
                relay["clarity"] = clarity_db

        return True
