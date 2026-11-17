#!/usr/bin/env python3
"""Create a topic brief and copy the reference implementation into a new folder."""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--topic", required=True)
    args = parser.parse_args()
    topic = args.topic.strip()
    if not topic:
        parser.error("--topic must contain a topic")
    destination = args.project.expanduser().resolve()
    if destination.exists():
        parser.error(f"project exists; choose a new directory: {destination}")
    skill = Path(__file__).resolve().parents[1]
    template = json.loads((skill / "assets/production-template.json").read_text())
    template["topic"] = topic
    source = skill / "assets/reference-project"
    if not (source / "scripts/render.py").is_file():
        parser.error("skill reference renderer is missing")
    destination.mkdir(parents=True)
    for name in ("assets", "build", "fonts", "scripts", "out"):
        (destination / name).mkdir()
    shutil.copytree(source, destination / "reference-project")
    (destination / "production.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "BRIEF.md").write_text(f"""# {topic}

视觉：贴纸拼贴人物口播。参照 reference-project 的构图和动效，围绕本主题重写。

## 台词与目标

填写逐字台词、观众与目标。准确文案在后期绘制。

## 四段分镜

结果与钩子 → 要素海报 → 组合/操作演示 → 人物近景收尾。
按实际声音填写 production.json 的场景时间和锚点。

## 素材与声音

记录人物、主题元素、口播和音乐的真实路径与来源 ID，明确沿用与新增项。
记录原声或替代配音的选择，画面轨静音，只保留一份口播。

## 制作交接

填写实际帧率/时长、布局、字体、音乐来源、当次授权范围与预算。
reference-project 是原片参考，改造后入口放 scripts/，避免直接输出旧主题。

## 验收

核对台词、口型、回音、遮脸、字形/越界、转场残留、规格与完整解码。
保存关键帧、混音检查与 QA 记录。
""", encoding="utf-8")
    print(json.dumps({"project": str(destination), "topic": topic,
                      "status": "brief created; script, timing and assets still required"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
