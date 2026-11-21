"""MediaWiki 文本清洗、模板参数提取与词条摘要生成。"""

from __future__ import annotations

import re

SECTION_PRIORITY = (
    "角色搭配推荐",
    "配装推荐",
    "阵容搭配",
    "驱动盘套装",
    "音擎推荐",
    "属性数据",
    "技能",
    "意象影画",
    "角色晋阶材料",
)

INFO_KEYS = (
    ("名称", "名称"),
    ("全名", "全名"),
    ("阵营", "阵营"),
    ("属性名称", "属性"),
    ("特性", "特性"),
    ("稀有度", "稀有度"),
    ("性别", "性别"),
    ("生日", "生日"),
    ("身高", "身高"),
    ("实装日期", "实装"),
    ("实装版本", "版本"),
    ("TAG", "定位标签"),
    ("昵称/外号", "外号"),
)

STAT_KEYS = (
    ("生命值", "生命"),
    ("攻击力", "攻击"),
    ("防御力", "防御"),
    ("冲击力", "冲击力"),
    ("异常掌控", "异常掌控"),
    ("异常精通", "异常精通"),
    ("能量自动回复", "能量回复"),
)

_QUERY_SUFFIXES = (
    "配装推荐",
    "阵容搭配",
    "攻略",
    "怎么打",
    "打法",
    "配装",
    "装备",
    "驱动盘",
    "音擎",
    "阵容",
    "配队",
    "技能",
    "属性",
    "材料",
    "图鉴",
    "推荐",
)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_COLOR_RE = re.compile(r"\{\{颜色\|[^|{}]*\|([^{}]*)\}\}")
_ICON_RE = re.compile(r"\{\{图标\|[^{}]*\}\}")
_LINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_HEADING_RE = re.compile(r"\n(={2,4})\s*([^=\n]+?)\s*={2,4}\s*\n")
_NOISE_KEY_RE = re.compile(r"(故事\d|GIF|图标|图片|文件|导航|立绘|动画|底部)")


def clean_wikitext(text: str) -> str:
    """把 MediaWiki 标记洗成人能读的纯文本。"""
    if not text:
        return ""
    text = _COMMENT_RE.sub("", text)
    text = _BR_RE.sub("\n", text)
    for _ in range(4):  # 颜色/图标模板可能嵌套
        text = _COLOR_RE.sub(r"\1", text)
    text = _ICON_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = text.replace("{{!}}", "|").replace("'''", "").replace("''", "")
    for _ in range(4):  # 剩下的模板整体丢掉
        updated = _TEMPLATE_RE.sub("", text)
        if updated == text:
            break
        text = updated

    lines: list[str] = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t\u3000]+", " ", raw).strip()
        if not line or line.startswith("|") or line in ("}}", "{|", "|}"):
            continue
        if re.fullmatch(r"[|\-+{}]+", line):
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _find_template_body(wikitext: str, name: str) -> str | None:
    marker = "{{" + name
    start = wikitext.find(marker)
    if start < 0:
        return None
    depth = 0
    index = start
    while index < len(wikitext) - 1:
        pair = wikitext[index : index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                return wikitext[start + 2 : index - 2]
            continue
        index += 1
    return None


def _split_top_level_pipes(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    index = 0
    while index < len(body):
        pair = body[index : index + 2]
        if pair in ("{{", "[["):
            depth += 1
            current.append(pair)
            index += 2
            continue
        if pair in ("}}", "]]"):
            depth -= 1
            current.append(pair)
            index += 2
            continue
        char = body[index]
        if char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def parse_template_params(wikitext: str, name: str) -> dict[str, str]:
    """取出 {{name|k=v|...}} 顶层的扁平参数。"""
    body = _find_template_body(wikitext, name)
    if body is None:
        return {}
    params: dict[str, str] = {}
    for part in _split_top_level_pipes(body)[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip()] = value.strip()
    return params


def split_sections(wikitext: str) -> list[tuple[int, str, str]]:
    """按二~四级标题切分，返回 (level, title, body)。"""
    matches = list(_HEADING_RE.finditer(wikitext))
    if not matches:
        return []
    out: list[tuple[int, str, str]] = []
    for position, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        body_start = match.end()
        body_end = len(wikitext)
        for later in matches[position + 1 :]:
            if len(later.group(1)) <= level:
                body_end = later.start()
                break
        out.append((level, title, wikitext[body_start:body_end]))
    return out


def strip_query_suffix(query: str) -> str:
    """把「妮可配装」这类自然问法还原成词条名。"""
    for suffix in _QUERY_SUFFIXES:
        if query.endswith(suffix) and len(query) > len(suffix) + 1:
            return query[: -len(suffix)].strip()
    return query


def generic_entries(wikitext: str) -> list[str]:
    """非角色页兜底：把第一个信息模板的键值摊平成文本。"""
    match = re.search(r"\{\{([^{}|]+)\|", wikitext)
    if not match:
        return []
    params = parse_template_params(wikitext, match.group(1).strip())
    if not params:
        return []
    out: list[str] = []
    for key, value in params.items():
        if _NOISE_KEY_RE.search(key):
            continue
        text = clean_wikitext(value).replace("\n", " ")
        if not text:
            continue
        out.append(f"{key}：{text[:260]}")
        if len(out) >= 14:
            break
    return out


def _format_character_info(wikitext: str) -> list[str]:
    info = parse_template_params(wikitext, "角色图鉴")
    if not info:
        return []
    lines: list[str] = []
    facts = [f"{label}：{val}" for key, label in INFO_KEYS if (val := clean_wikitext(info.get(key, "")))]
    if facts:
        lines.append("｜".join(facts))
    cvs = [
        f"{label} {val}"
        for key, label in (("中文CV", "中"), ("日文CV", "日"), ("韩文CV", "韩"), ("英文CV", "英"))
        if (val := clean_wikitext(info.get(key, "")))
    ]
    if cvs:
        lines.append("CV：" + " / ".join(cvs))
    stats = [f"{label} {val}" for key, label in STAT_KEYS if (val := clean_wikitext(info.get(key, "")))]
    if stats:
        lines.append("基础数值（Lv.1）：" + "、".join(stats))
    return lines


def _format_talents(wikitext: str) -> list[str]:
    talents = parse_template_params(wikitext, "天赋")
    if not talents:
        return []
    entries = []
    for index in range(1, 7):
        name = clean_wikitext(talents.get(f"天赋{index}", ""))
        effect = clean_wikitext(talents.get(f"天赋{index}效果", ""))
        if name and effect:
            entries.append(f"{index}.{name}：{effect}")
    return ["天赋/影画：", *entries] if entries else []


def _format_priority_sections(wikitext: str) -> list[str]:
    sections = split_sections(wikitext)
    picked: list[str] = []
    lines: list[str] = []
    for wanted in SECTION_PRIORITY:
        for _level, section_title, body in sections:
            if wanted not in section_title or section_title in picked:
                continue
            text = clean_wikitext(body)
            if len(text) < 40:
                continue
            picked.append(section_title)
            lines.append(f"【{section_title}】")
            lines.append(text[:700])
            break
        if len(picked) >= 3:
            break
    return lines


def build_summary(title: str, wikitext: str, url: str, max_chars: int) -> str:
    """将 MediaWiki 正文整理为受长度上限约束的纯文本摘要。"""
    lines: list[str] = [f"绝区零WIKI·{title}"]
    lines.extend(_format_character_info(wikitext))
    lines.extend(_format_talents(wikitext))
    lines.extend(_format_priority_sections(wikitext))

    if len(lines) <= 1:
        generic = generic_entries(wikitext)
        if generic:
            lines.extend(generic)
        else:
            fallback = clean_wikitext(wikitext)
            lines.append(fallback[:max_chars] if fallback else "这个词条没有可读正文。")

    lines.append(f"来源：{url}")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n…（已截断）"
    return text
