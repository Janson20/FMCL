"""Skill 系统 - 可加载技能文件注入 AI prompt

参考 opencode Skill 设计：
- 技能文件为 .md 格式，放在 ./data/agent/skills/ 目录
- 每个技能包含 SKILL.md 文件描述其用途和指令
- AI 通过 skill 工具加载技能内容注入对话

用法:
1. 创建 ./data/agent/skills/{skill_name}/SKILL.md
2. AI 在 system context 中看到可用技能列表
3. AI 调用 skill 工具加载特定技能
"""

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from logzero import logger


@dataclass
class SkillInfo:
    """技能元数据"""

    name: str  # 技能名称（目录名）
    description: str  # 简短描述（SKILL.md 第一行）
    directory: str  # 技能目录路径
    content: str  # SKILL.md 完整内容
    files: List[str] = field(default_factory=list)  # 技能目录下其他文件

    def to_prompt_text(self) -> str:
        """生成注入 prompt 的技能文本"""
        lines = [
            f'<skill_content name="{self.name}">',
            f"# Skill: {self.name}",
            "",
            self.content.strip(),
            "",
            f"技能目录: {self.directory}",
            "技能目录下的文件是此技能的上下文资源。",
        ]
        if self.files:
            lines.append("")
            lines.append("<skill_files>")
            for f in self.files:
                lines.append(f"<file>{f}</file>")
            lines.append("</skill_files>")
        lines.append("</skill_content>")
        return "\n".join(lines)


def _get_skills_dir() -> str:
    """获取技能存储目录"""
    base = os.path.join(os.getcwd(), "data", "agent", "skills")
    os.makedirs(base, exist_ok=True)
    return base


def load_all_skills() -> List[SkillInfo]:
    """加载所有可用技能"""
    skills = []
    skills_dir = _get_skills_dir()

    if not os.path.isdir(skills_dir):
        return skills

    for entry in os.listdir(skills_dir):
        skill_dir = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_dir):
            continue

        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue

        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()

            # 第一行作为描述
            first_line = content.strip().split("\n")[0].lstrip("#").strip()
            description = first_line or entry

            # 列出其他文件
            files = []
            for root, dirs, filenames in os.walk(skill_dir):
                for fn in filenames:
                    if fn == "SKILL.md":
                        continue
                    rel_path = os.path.relpath(os.path.join(root, fn), skill_dir)
                    files.append(rel_path)

            skills.append(
                SkillInfo(name=entry, description=description, directory=skill_dir, content=content, files=files)
            )
        except Exception as e:
            logger.error(f"[Skill] 加载技能 '{entry}' 失败: {e}")

    return skills


def get_skill_by_name(name: str) -> Optional[SkillInfo]:
    """按名称获取技能"""
    skills_dir = _get_skills_dir()
    skill_dir = os.path.join(skills_dir, name)

    if not os.path.isdir(skill_dir):
        return None

    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None

    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()

        first_line = content.strip().split("\n")[0].lstrip("#").strip()
        description = first_line or name

        files = []
        for root, dirs, filenames in os.walk(skill_dir):
            for fn in filenames:
                if fn == "SKILL.md":
                    continue
                rel_path = os.path.relpath(os.path.join(root, fn), skill_dir)
                files.append(rel_path)

        return SkillInfo(name=name, description=description, directory=skill_dir, content=content, files=files)
    except Exception as e:
        logger.error(f"[Skill] 加载技能 '{name}' 失败: {e}")
        return None


def get_skills_context_text() -> str:
    """生成技能上下文文本（注入系统提示词）"""
    skills = load_all_skills()
    if not skills:
        return ""

    lines = ["\n## 可用技能\n"]
    lines.append("以下技能可通过 skill 工具加载，用于特定任务：\n")
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")

    return "\n".join(lines)
