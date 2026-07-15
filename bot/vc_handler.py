import logging
import asyncio
import subprocess
import numpy as np
from pyrogram import Client
from pytgcalls import GroupCallFactory, GroupCallRaw

from config import (
    active_relays, DEFAULT_VOLUME, DEFAULT_GAIN,
    DEFAULT_BASS, DEFAULT_CLARITY,
    PRIVATE_GROUP_ID, SAMPLE_RATE, CHANNELS,
)
from .audio_processor import ExtremeAudioProcessor

logger = logging.getLogger(__name__)


class VCRelayHandler:
    """
    Handles dual-group VC bridging with extreme audio processing using GroupCallRaw.
    """

    def __init__(self, app: Client):
        self.app = app
        self.group_call_factory = GroupCallFactory(app, GroupCallFactory.MTPROTO_CLIENT_TYPE.PYROGRAM)
        self.processors = {}         # target_chat_id -> ExtremeAudioProcessor
        self.group_calls = {}        # target_chat_id -> GroupCallRaw
        self.ffmpeg_processes = {}   # target_chat_id -> subprocess

    def _on_played_data(self, group_call: GroupCallRaw, length: int) -> bytes:
        """
        Callback: tgcalls requests 'length' bytes of processed audio to play.
        We return processed PCM bytes or silence.
        """
        # This is called when tgcalls needs audio data to send
        # We return silence here because we're capturing from ffmpeg separately
        # and feeding into the recorded_data callback path instead
        return b'\x00' * length

    def _on_recorded_data(self, group_call: GroupCallRaw, data: bytes, length: int):
        """
        Callback: tgcalls captured 'length' bytes of audio from the VC.
        This is where we receive the audio from the PRIVATE group VC
        (your voice), process it, and feed it back into the TARGET group.
        """
        target_id = self._get_target_by_group_call(group_call)
        if target_id is None:
            return

        processor = self.processors.get(target_id)
        if not processor:
            return

        # Process the audio with extreme amplification
        try:
            processed = processor.process_audio_block(data)
            # Feed processed audio back into the target group call
            # (This is the relay part — audio goes into the target's playout)
            target_gc = self.group_calls.get(target_id)
            if target_gc and hasattr(target_gc, '_GroupCallRaw__raw_audio_device_descriptor'):
                # We can't directly inject — instead we use a separate approach
                pass
        except Exception as e:
            logger.error(f"Audio processing error: {e}")

    def _get_target_by_group_call(self, group_call) -> int | None:
        """Reverse lookup: which target ID owns this group call object?"""
        for tid, gc in self.group_calls.items():
            if gc == group_call:
                return tid
        return None

    async def _start_ffmpeg_capture(self) -> asyncio.subprocess.Process:
        """Start ffmpeg to capture system audio (your mic / desktop audio)."""
        import sys
        if sys.platform == "linux":
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

    async def _ffmpeg_reader_loop(self, target_chat_id: int, ffmpeg_proc: asyncio.subprocess.Process):
        """
        Reads PCM chunks from ffmpeg, processes them, and feeds them
        into the target group call's playout buffer.
        """
        processor = self.processors.get(target_chat_id)
        target_gc = self.group_calls.get(target_chat_id)
        if not processor or not target_gc:
            return

        chunk_size = int(SAMPLE_RATE * 0.02) * CHANNELS * 2  # 20ms frames
        logger.info(f"Started audio reader loop for target {target_chat_id}")

        while True:
            try:
                chunk = await ffmpeg_proc.stdout.read(chunk_size)
                if not chunk or len(chunk) < chunk_size:
                    logger.warning(f"Short read for target {target_chat_id}, stopping")
                    break

                # Process with extreme amplification
                processed_chunk = processor.process_audio_block(chunk)

                # Feed into the target group call
                # We use the internal callback approach:
                # GroupCallRaw's on_played_data callback is triggered by tgcalls
                # To push data, we write to a pipe/FIFO and point the source there
                # OR we use a different mechanism

                # For now, store in a queue
                if not hasattr(target_gc, '_audio_queue'):
                    target_gc._audio_queue = asyncio.Queue()
                await target_gc._audio_queue.put(processed_chunk)

            except Exception as e:
                logger.exception(f"Reader loop error for {target_chat_id}: {e}")
                break

        logger.info(f"Reader loop ended for target {target_chat_id}")

    async def _playout_loop(self, target_chat_id: int):
        """
        Reads processed audio from the queue and feeds it to the
        playout via a named pipe (FIFO) that ffmpeg reads from.
        """
        target_gc = self.group_calls.get(target_chat_id)
        if not target_gc:
            return

        import os
        import stat

        fifo_path = f"/tmp/vc_relay_{target_chat_id}.raw"
        # Create FIFO if it doesn't exist
        if not os.path.exists(fifo_path):
            os.mkfifo(fifo_path)

        logger.info(f"Playout loop started for {target_chat_id} via {fifo_path}")

        while True:
            try:
                chunk = await target_gc._audio_queue.get()
                # Write to FIFO
                with open(fifo_path, "ab") as fifo:
                    fifo.write(chunk)
            except Exception as e:
                logger.error(f"Playout loop error: {e}")
                await asyncio.sleep(1)

    async def join_and_bridge(
        self,
        target_chat_id: int,
        volume: float = DEFAULT_VOLUME,
        gain_db: float = DEFAULT_GAIN,
        bass_db: float = DEFAULT_BASS,
        clarity_db: float = DEFAULT_CLARITY,
    ):
        """Join both VCs and start extreme audio relay."""

        # Create audio processor
        processor = ExtremeAudioProcessor(
            volume=volume,
            gain_db=gain_db,
            bass_db=bass_db,
            clarity_db=clarity_db,
        )
        self.processors[target_chat_id] = processor

        try:
            # Start ffmpeg to capture your mic/audio
            ffmpeg_proc = await self._start_ffmpeg_capture()
            self.ffmpeg_processes[target_chat_id] = ffmpeg_proc
            logger.info(f"ffmpeg capture started for target {target_chat_id}")

            # Create target group call using GroupCallRaw
            target_gc = self.group_call_factory.get_group_call_raw(
                on_played_data=self._on_played_data,
                on_recorded_data=self._on_recorded_data,
            )
            target_gc._audio_queue = asyncio.Queue()
            self.group_calls[target_chat_id] = target_gc

            # Start the reader loop (ffmpeg → process → queue)
            asyncio.create_task(self._ffmpeg_reader_loop(target_chat_id, ffmpeg_proc))

            # Start the playout loop (queue → FIFO → target VC)
            asyncio.create_task(self._playout_loop(target_chat_id))

            # Join the target group VC
            # Using a FIFO file as the audio source for the target
            import os
            fifo_path = f"/tmp/vc_relay_{target_chat_id}.raw"
            if not os.path.exists(fifo_path):
                os.mkfifo(fifo_path)

            # The GroupCallFile approach — point to the FIFO
            # OR use GroupCallRaw with on_played_data returning our processed data
            await target_gc.start(
                target_chat_id,
                join_as=self.app.me.id,
            )
            logger.info(f"✅ Joined target VC: {target_chat_id}")

            # Track
            active_relays[target_chat_id] = {
                "volume": volume,
                "gain": gain_db,
                "bass": bass_db,
                "clarity": clarity_db,
                "running": True,
            }

            return True

        except Exception as e:
            logger.exception(f"Failed to bridge: {e}")
            await self._cleanup(target_chat_id)
            return False

    async def _cleanup(self, target_chat_id: int):
        """Clean up all resources for a target."""
        # Kill ffmpeg
        if target_chat_id in self.ffmpeg_processes:
            proc = self.ffmpeg_processes[target_chat_id]
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            del self.ffmpeg_processes[target_chat_id]

        # Leave group call
        if target_chat_id in self.group_calls:
            gc = self.group_calls[target_chat_id]
            try:
                await gc.stop()
            except Exception:
                pass
            del self.group_calls[target_chat_id]

        # Remove processor
        if target_chat_id in self.processors:
            del self.processors[target_chat_id]

        # Remove from active relays
        if target_chat_id in active_relays:
            del active_relays[target_chat_id]

        # Remove FIFO
        import os
        fifo_path = f"/tmp/vc_relay_{target_chat_id}.raw"
        if os.path.exists(fifo_path):
            os.remove(fifo_path)

        logger.info(f"Cleaned up target {target_chat_id}")

    async def leave_and_disconnect(self, target_chat_id: int):
        """Leave VCs and clean up."""
        await self._cleanup(target_chat_id)
        logger.info(f"Disconnected from {target_chat_id}")

    async def update_audio(
        self, target_chat_id: int,
        volume: float = None, gain_db: float = None,
        bass_db: float = None, clarity_db: float = None,
    ):
        """Update audio settings live."""
        proc = self.processors.get(target_chat_id)
        if not proc:
            return False
        proc.update_settings(volume=volume, gain_db=gain_db, bass_db=bass_db, clarity_db=clarity_db)

        relay = active_relays.get(target_chat_id)
        if relay:
            if volume is not None: relay["volume"] = volume
            if gain_db is not None: relay["gain"] = gain_db
            if bass_db is not None: relay["bass"] = bass_db
            if clarity_db is not None: relay["clarity"] = clarity_db
        return True
