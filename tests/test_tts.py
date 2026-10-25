import io
import json
import os
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hq import tts


def tone(n=800):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\x01\x00" * n)
    return b.getvalue()


def streaming_header(b):
    """Make a WAV look like a streamed one: RIFF and data sizes set to 0xFFFFFFFF."""
    b = bytearray(b); b[4:8] = b"\xff\xff\xff\xff"; i = b.index(b"data"); b[i + 4:i + 8] = b"\xff\xff\xff\xff"
    return bytes(b)


class Fake(BaseHTTPRequestHandler):
    seen = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append(body)
        if Fake.mode == "terms":
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"model terms must be accepted"}'); return
        assert self.headers["Authorization"] == "Bearer testkey" and len(body["input"]) <= tts.CHUNK
        self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.end_headers(); self.wfile.write(streaming_header(tone()) if Fake.mode == "stream" else tone())


class Tts(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        Fake.seen, Fake.mode = [], "ok"
        self.old = (tts.AUDIO_DIR, dict(os.environ))
        tts.AUDIO_DIR = tempfile.mkdtemp()
        os.environ.update(HQ_OPENAI_BASE=f"http://127.0.0.1:{self.srv.server_port}/v1", HQ_OPENAI_KEY="testkey")

    def tearDown(self):
        self.srv.shutdown()
        tts.AUDIO_DIR = self.old[0]
        os.environ.clear(); os.environ.update(self.old[1])

    def test_chunks_respect_limit_and_keep_all_words(self):
        text = ("The agent has handed over. " + "Check-in closes soon, and the driver has not confirmed, so a person must decide now; " * 6).strip()
        parts = tts.chunks(text)
        self.assertTrue(all(len(p) <= tts.CHUNK for p in parts))
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))

    def test_long_text_is_merged_into_one_wav_and_cached(self):
        text = "This is a sentence about a ferry. " * 12
        audio = tts.synth(text)
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual(w.getnframes(), 800 * len(Fake.seen))
        self.assertGreater(len(Fake.seen), 1)
        calls = len(Fake.seen)
        self.assertEqual(tts.synth(text), audio)
        self.assertEqual(len(Fake.seen), calls)  # second call came from the cache, no request
        self.assertTrue(os.path.exists(tts.path_for(text)))

    def test_cache_works_without_any_key(self):
        tts.synth("Hello there.")
        del os.environ["HQ_OPENAI_KEY"]
        self.assertTrue(tts.synth("Hello there.").startswith(b"RIFF"))
        with self.assertRaises(tts.TtsError):
            tts.synth("Never spoken before.")

    def test_streamed_wav_with_placeholder_sizes_is_repaired(self):
        Fake.mode = "stream"
        audio = tts.synth("A sentence about a ferry. " * 12)
        with wave.open(io.BytesIO(audio)) as w:  # the stdlib reader now accepts it, with the right length
            self.assertEqual(w.getnframes(), 800 * len(Fake.seen))

    def test_provider_error_is_reported(self):
        Fake.mode = "terms"
        with self.assertRaises(tts.TtsError) as c:
            tts.synth("Anything.")
        self.assertIn("400", str(c.exception))


if __name__ == "__main__":
    unittest.main()
