"""Offline workflow and real FFmpeg tests; no model calls or private media."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_project", ROOT / "scripts/check_project.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
EXAMPLE = json.loads((ROOT / "examples/desk-styling.production.json").read_text(encoding="utf-8"))
MEDIA_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = copy.deepcopy(EXAMPLE)

    def test_example_covers_complete_timeline(self):
        errors, _ = checker.validate(self.plan, ROOT)
        self.assertEqual(errors, [])

    def test_gaps_and_overlaps_are_rejected(self):
        for delta in (-0.5, 0.5):
            with self.subTest(delta=delta):
                plan = copy.deepcopy(self.plan)
                plan["scenes"][1]["start_s"] += delta
                errors, _ = checker.validate(plan, ROOT)
                self.assertTrue(any("gap or overlaps" in e for e in errors), errors)

    def test_out_of_scene_anchor_is_rejected(self):
        self.plan["scenes"][0]["anchors"][0]["time_s"] = 100
        errors, _ = checker.validate(self.plan, ROOT)
        self.assertTrue(any("anchor outside" in e for e in errors), errors)

    def test_picture_audio_must_be_muted(self):
        self.plan["audio"]["picture_audio"] = True
        errors, _ = checker.validate(self.plan, ROOT)
        self.assertTrue(any("picture_audio" in e for e in errors), errors)

    def test_duplicate_voice_and_voice_disguised_as_music_are_rejected(self):
        voice = self.plan["narration"]["audio"]
        for second_role in ("voice", "music"):
            with self.subTest(role=second_role):
                edl = {"tracks": [{"clips": [{"audio": False}]}], "audio": [
                    {"role": "voice", "src": voice}, {"role": second_role, "src": voice}
                ]}
                errors, _ = checker.validate(self.plan, ROOT, edl=edl)
                self.assertTrue(any("repeats" in e for e in errors), errors)

    def test_valid_single_voice_edl_passes(self):
        edl = {"tracks": [{"clips": [{"audio": False}]}], "audio": [
            {"role": "voice", "src": self.plan["narration"]["audio"]},
            {"role": "music", "src": "assets/music.wav"}
        ]}
        errors, _ = checker.validate(self.plan, ROOT, edl=edl)
        self.assertEqual(errors, [])

    def test_picture_clip_requires_explicit_mute(self):
        edl = {"tracks": [{"clips": [{"src": "picture.mp4"}]}], "audio": [
            {"role": "voice", "src": self.plan["narration"]["audio"]}
        ]}
        errors, _ = checker.validate(self.plan, ROOT, edl=edl)
        self.assertTrue(any("audio=false" in e for e in errors), errors)


class ScaffoldTests(unittest.TestCase):
    def test_new_topic_and_existing_project_protection(self):
        with tempfile.TemporaryDirectory(prefix="sticker project ") as directory:
            target = Path(directory) / "new topic"
            first = run(sys.executable, str(ROOT / "scripts/prepare_project.py"), str(target),
                        "--topic", "客服工作怎么变")
            self.assertEqual(first.returncode, 0, first.stderr)
            plan_file = target / "production.json"
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            self.assertEqual(plan["topic"], "客服工作怎么变")
            self.assertTrue((target / "reference-project/scripts/render.py").is_file())
            self.assertIn("客服工作怎么变", (target / "BRIEF.md").read_text(encoding="utf-8"))
            before = plan_file.read_bytes()
            second = run(sys.executable, str(ROOT / "scripts/prepare_project.py"), str(target),
                         "--topic", "different topic")
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(plan_file.read_bytes(), before)

    def test_readme_local_links_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+)\)", readme):
            if "://" in link or link.startswith("#"):
                continue
            self.assertTrue((ROOT / link.split("#", 1)[0]).is_file(), link)


@unittest.skipUnless(MEDIA_AVAILABLE, "FFmpeg / ffprobe required for real media checks")
class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sticker media ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        result = run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                     "color=c=green:s=160x284:r=24:d=2", "-f", "lavfi", "-i",
                     "sine=frequency=440:duration=2", "-c:v", "libx264", "-threads", "1",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.root / "take.mp4"))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run("ffmpeg", "-y", "-loglevel", "error", "-i", str(self.root / "take.mp4"),
                     "-vn", str(self.root / "voice.wav"))
        self.assertEqual(result.returncode, 0, result.stderr)
        # An SVG item is enough for path validation; the video is probed for real.
        (self.root / "item.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.words = self.root / "words.json"
        self.words.write_text(json.dumps({"words": [{"start": 0, "end": 1.5, "word": "测试"}]}))
        self.plan = copy.deepcopy(EXAMPLE)
        self.plan["duration_s"] = 2
        self.plan["assets"] = {"take": "take.mp4", "items": {"item": "item.svg"}}
        self.plan["narration"].update(audio="voice.wav", words="words.json")
        self.plan["audio"]["voice_tracks"] = ["voice.wav"]
        self.plan["scenes"] = [{"id": "all", "start_s": 0, "end_s": 2,
                                "summary": "A complete synthetic test clip", "anchors": []}]

    def test_valid_media_passes_command_line_check(self):
        production = self.root / "production.json"
        production.write_text(json.dumps(self.plan))
        result = run(sys.executable, str(ROOT / "scripts/check_project.py"), str(production), "--media")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_speech_beyond_final_cut_is_rejected(self):
        self.words.write_text(json.dumps({"words": [{"start": 0, "end": 2.5, "word": "cut off"}]}))
        errors, _ = checker.validate(self.plan, self.root, media=True)
        self.assertTrue(any("spoken word extends" in e for e in errors), errors)

    def test_short_presenter_timeline_is_rejected(self):
        self.plan["duration_s"] = 3
        self.plan["scenes"][0]["end_s"] = 3
        errors, _ = checker.validate(self.plan, self.root, media=True)
        self.assertTrue(any("take is shorter" in e for e in errors), errors)


@unittest.skipUnless(MEDIA_AVAILABLE, "FFmpeg / ffprobe required for shard integration tests")
class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sticker render with spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copyfile(ROOT / "assets/reference-project/scripts/render_all.sh",
                        self.root / "scripts/render_all.sh")
        (self.root / "scripts/render.py").write_text('''import os, subprocess, sys
a, b = int(sys.argv[1]), int(sys.argv[2])
if os.environ.get("TEST_FAIL_SHARD") and a == 0:
    raise SystemExit(7)
frames = b - a
if os.environ.get("TEST_SHORT_SHARD") and a == 0:
    frames -= 1
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
    "color=c=blue:s=16x16:r=30", "-frames:v", str(frames), "-c:v", "libx264",
    "-threads", "1", "-pix_fmt", "yuv420p", sys.argv[3]], check=True)
''')
        self.env = dict(os.environ, STICKER_RENDER_FRAMES="6", STICKER_RENDER_JOBS="3")

    def test_successful_shards_join_to_exact_frame_count(self):
        result = run("bash", str(self.root / "scripts/render_all.sh"), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = run("ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                    "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
                    str(self.root / "build/picture.mp4"))
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.strip(), "6")
        self.assertEqual(list((self.root / "build").glob("render.*")), [])

    def assert_preserves_existing_picture(self, extra_env):
        (self.root / "build").mkdir()
        picture = self.root / "build/picture.mp4"
        picture.write_bytes(b"previous successful picture")
        result = run("bash", str(self.root / "scripts/render_all.sh"), env=dict(self.env, **extra_env))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(picture.read_bytes(), b"previous successful picture")
        self.assertTrue(list((self.root / "build").glob("render.*/part*.log")))

    def test_failed_shard_preserves_previous_picture(self):
        self.assert_preserves_existing_picture({"TEST_FAIL_SHARD": "1"})

    def test_short_shard_preserves_previous_picture(self):
        self.assert_preserves_existing_picture({"TEST_SHORT_SHARD": "1"})


if __name__ == "__main__":
    unittest.main()
