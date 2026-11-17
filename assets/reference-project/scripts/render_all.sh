#!/usr/bin/env bash
# Render isolated shards; replace the final picture only after every check passes.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
frames=${STICKER_RENDER_FRAMES:-811}
jobs=${STICKER_RENDER_JOBS:-8}
if ! [[ "$frames" =~ ^[1-9][0-9]*$ && "$jobs" =~ ^[1-9][0-9]*$ ]]; then
  echo "STICKER_RENDER_FRAMES and STICKER_RENDER_JOBS must be positive integers" >&2
  exit 2
fi
if (( jobs > frames )); then jobs=$frames; fi
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
mkdir -p build
run_dir=$(mktemp -d "$repo_root/build/render.XXXXXXXX")
cleanup() {
  status=$?
  if (( status == 0 )); then
    rm -rf "$run_dir"
  else
    echo "Render failed; logs retained in $run_dir" >&2
  fi
}
trap cleanup EXIT
pids=()
for ((i=0; i<jobs; i++)); do
  start=$(( frames*i/jobs ))
  end=$(( frames*(i+1)/jobs ))
  python3 scripts/render.py "$start" "$end" "$run_dir/part$i.mp4" >"$run_dir/part$i.log" 2>&1 &
  pids+=("$!")
done
failed=0
for ((i=0; i<jobs; i++)); do
  if ! wait "${pids[$i]}"; then
    cat "$run_dir/part$i.log" >&2
    failed=1
  fi
done
if (( failed )); then exit 1; fi
for ((i=0; i<jobs; i++)); do
  expected=$(( frames*(i+1)/jobs - frames*i/jobs ))
  actual=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of default=nw=1:nk=1 "$run_dir/part$i.mp4")
  if [[ "$actual" != "$expected" ]]; then
    echo "Shard $i has $actual frames; expected $expected" >&2
    exit 1
  fi
  printf "file 'part%s.mp4'\n" "$i" >> "$run_dir/list.txt"
done
ffmpeg -y -loglevel error -f concat -safe 0 -i "$run_dir/list.txt" -c copy "$run_dir/picture.mp4"
actual=$(ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of default=nw=1:nk=1 "$run_dir/picture.mp4")
if [[ "$actual" != "$frames" ]]; then
  echo "Final picture has $actual frames; expected $frames" >&2
  exit 1
fi
mv -f "$run_dir/picture.mp4" build/picture.mp4
echo "Rendered $frames frames to build/picture.mp4"
