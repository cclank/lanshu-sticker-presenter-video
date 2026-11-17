#!/usr/bin/env python3
"""Validate plan continuity, optional source media and an rn EDL's single voice.

This does not evaluate audible reverb, lip sync, transcription accuracy or design.
"""
import argparse
import json
import math
import subprocess
from pathlib import Path


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(data, root, media=False, edl=None):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["production.json must be an object"], warnings
    if not isinstance(data.get("topic"), str) or not data["topic"].strip():
        errors.append("topic is empty")
    if not isinstance(data.get("script"), str) or not data["script"].strip():
        errors.append("script is empty")
    canvas = data.get("canvas") or {}
    for key in ("width", "height", "fps"):
        value = canvas.get(key)
        if not number(value) or value <= 0:
            errors.append(f"canvas.{key} must be positive")
        elif key != "fps" and (int(value) != value or int(value) % 2):
            errors.append(f"canvas.{key} must be an even integer for H.264")
    duration = data.get("duration_s")
    valid_duration = number(duration) and duration > 0
    if not valid_duration:
        errors.append("duration_s must be a positive planned or measured duration")
    fps = canvas.get("fps")
    tolerance = 1 / fps if number(fps) and fps > 0 else 0.04
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("scenes must be a non-empty list")
        scenes = []
    cursor, seen = 0.0, set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"scene {index} must be an object")
            continue
        label = scene.get("id")
        if not isinstance(label, str) or not label or label in seen:
            errors.append(f"scene {index} needs a unique id")
        else:
            seen.add(label)
        if not isinstance(scene.get("summary"), str) or not scene["summary"].strip():
            errors.append(f"scene {label} needs a topic-specific summary")
        start, end = scene.get("start_s"), scene.get("end_s")
        if not number(start) or not number(end) or start < 0 or end <= start:
            errors.append(f"scene {label} has invalid start/end")
            continue
        if abs(start - cursor) > tolerance:
            errors.append(f"scene {label} leaves a gap or overlaps the prior scene")
        cursor = end
        if valid_duration and end > duration + tolerance:
            errors.append(f"scene {label} ends after duration_s")
        anchors = scene.get("anchors", [])
        if not isinstance(anchors, list):
            errors.append(f"scene {label} anchors must be a list")
            continue
        for anchor in anchors:
            time = anchor.get("time_s") if isinstance(anchor, dict) else None
            if not number(time) or not start <= time <= end:
                errors.append(f"scene {label} has an anchor outside its bounds")
    if valid_duration and abs(cursor - duration) > tolerance:
        errors.append("scene coverage does not match duration_s")
    narration = data.get("narration") or {}
    if narration.get("mode") not in ("native", "external"):
        errors.append("narration.mode must be native or external")
    voice = narration.get("audio")
    audio = data.get("audio") or {}
    if audio.get("picture_audio") is not False:
        errors.append("picture_audio must be false; mix one explicit narration track")
    if not isinstance(voice, str) or not voice or audio.get("voice_tracks") != [voice]:
        errors.append("voice_tracks must contain exactly the selected narration.audio")

    def resolved(path):
        p = Path(path).expanduser()
        return (p if p.is_absolute() else root / p).resolve()

    def existing(path, description):
        if not isinstance(path, str) or not path:
            errors.append(f"missing {description} path")
            return None
        p = resolved(path)
        if not p.is_file():
            errors.append(f"missing {description}: {p}")
            return None
        return p

    if media:
        assets = data.get("assets") or {}
        take = existing(assets.get("take"), "presenter take")
        existing(voice, "narration audio")
        if assets.get("portrait"):
            existing(assets["portrait"], "portrait reference")
        items = assets.get("items")
        if not isinstance(items, dict) or not items:
            errors.append("assets.items must map topic items to real files")
        else:
            for name, path in items.items():
                existing(path, f"item {name}")
        if assets.get("music"):
            existing(assets["music"], "music")
        words = existing(narration.get("words"), "word timestamps")
        if words:
            try:
                obj = read_json(words)
                entries = obj.get("words", []) if isinstance(obj, dict) else obj
                if not isinstance(entries, list) or not entries:
                    errors.append("word timestamps contain no words")
                else:
                    previous = -1.0
                    for word in entries:
                        a, b = word.get("start"), word.get("end")
                        if not number(a) or not number(b) or a < 0 or a < previous or b < a:
                            errors.append("word timestamps need ordered, valid start/end")
                            break
                        previous = a
                        if valid_duration and b > duration + tolerance:
                            errors.append("a spoken word extends beyond the final duration")
                            break
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"cannot read word timestamps: {exc}")
        if take:
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,r_frame_rate",
                     "-of", "json", str(take)], capture_output=True, text=True, check=True
                )
                info = json.loads(result.stdout)
                if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
                    errors.append("presenter take has no video stream")
                if valid_duration and float(info["format"]["duration"]) + tolerance < duration:
                    errors.append("presenter take is shorter than duration_s; assemble the intended source timeline first")
            except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
                errors.append(f"cannot probe presenter take: {exc}")
    if edl is not None:
        if not isinstance(edl, dict):
            errors.append("EDL must be an object")
        else:
            entries = edl.get("audio", [])
            actual_voice = [a for a in entries if isinstance(a, dict) and a.get("role") == "voice"]
            if len(actual_voice) != 1:
                errors.append("EDL must contain exactly one role=voice audio entry")
            elif isinstance(voice, str) and actual_voice[0].get("src"):
                if resolved(actual_voice[0]["src"]) != resolved(voice):
                    errors.append("EDL voice source differs from narration.audio")
            else:
                errors.append("EDL voice entry needs a src")
            for track in edl.get("tracks", []):
                for clip in track.get("clips", []):
                    if clip.get("audio") is not False:
                        errors.append("EDL picture clips must explicitly set audio=false")
            if isinstance(voice, str) and voice:
                same = [a for a in entries if isinstance(a, dict) and isinstance(a.get("src"), str)
                        and resolved(a["src"]) == resolved(voice)]
                if len(same) > 1:
                    errors.append("EDL repeats the selected narration source")
            for entry in entries:
                if isinstance(entry, dict) and entry.get("role") not in ("voice", "music", "sfx"):
                    warnings.append("unclassified audio source; listen for duplicate narration")
    warnings.append("Structure check only; listen for reverb and verify lip sync, typography and visual quality separately")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production", type=Path)
    parser.add_argument("--media", action="store_true", help="require files, timestamps and ffprobe duration")
    parser.add_argument("--edl", type=Path, help="check rn assemble edit.json for duplicate narration")
    args = parser.parse_args()
    path = args.production.expanduser().resolve()
    try:
        data = read_json(path)
        edl = read_json(args.edl.expanduser().resolve()) if args.edl else None
        errors, warnings = validate(data, path.parent, args.media, edl)
    except (OSError, ValueError) as exc:
        errors, warnings = [str(exc)], []
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
