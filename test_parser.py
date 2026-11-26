"""Unit tests for zzz-wiki wikitext parser, AkaDataset, and LoreStore."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse

import akads
import lore
import wikitext


class WikitextParserTests(unittest.TestCase):
    def test_clean_template_and_summary(self):
        fixtures = {
            "安比": "{{角色图鉴|名称=安比|阵营=狡兔屋|属性名称=电|特性=击破|稀有度=A}}\n==配装推荐==\n推荐震星迪斯科4件套，搭配高冲击力驱动盘与电属性辅助角色形成稳定失衡循环。",
            "山大王": "{{驱动盘图鉴|名称=山大王|二件套=护盾强效提升|四件套=击破效果触发}}\n==适用角色==\n击破位角色可用。",
        }
        for title, wt in fixtures.items():
            text = wikitext.build_summary(title, wt, f"https://wiki.biligame.com/zzz/{title}", 1200)
            self.assertIn(title, text)
            self.assertLessEqual(len(text), 1200)

        self.assertEqual(wikitext.strip_query_suffix("妮可配装"), "妮可")
        self.assertEqual(wikitext.strip_query_suffix("安比"), "安比")

    def test_init_has_no_bom(self):
        raw = (Path(__file__).resolve().parent / "__init__.py").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_long_article_keeps_complete_source(self):
        url = "https://example.com/guide"
        summary = wikitext.build_summary("词条", "正文" * 400, url, 200)
        self.assertLessEqual(len(summary), 200)
        self.assertTrue(summary.endswith("来源：" + url))
        self.assertIn("已截断", summary)


class AkaDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        aka = self.root / "aka"
        (aka / "char").mkdir(parents=True)
        (aka / "weapon").mkdir(parents=True)
        (aka / "build").mkdir(parents=True)
        (aka / "meta.json").write_text(json.dumps({"built_at": "2026-11-20"}), encoding="utf-8")
        (aka / "zh2id.json").write_text(
            json.dumps({"安比": "1011", "零号·安比": "1381"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (aka / "characters.json").write_text(
            json.dumps([{"id": "1011", "name": "Anby"}, {"id": "1381", "name": "Soldier 0 - Anby"}]),
            encoding="utf-8",
        )
        (aka / "weapons.json").write_text("[]", encoding="utf-8")
        (aka / "glossary.json").write_text("{}", encoding="utf-8")

    def test_faction_typo_and_empty_ascension_levels_safe(self):
        self.assertEqual(akads.FACTION_ZH["Belobog Heavy Industries"], "白祇重工")
        ds = akads.AkaDataset(self.root)
        # Empty ascension levels must NOT raise IndexError.
        summary = ds.build_char_summary(
            {
                "name": "Koleda",
                "rarity": "S",
                "element": {"text": "Fire"},
                "camp": {"text": "Belobog Heavy Industries"},
                "ascension": {"baseStats": {"HP": {"value": 500}}, "levels": []},
            },
            1200,
        )
        self.assertIn("白祇重工", summary)
        self.assertIn("生命值 500", summary)

    def test_resolve_character_prefers_longest_and_rejects_one_char_substring(self):
        ds = akads.AkaDataset(self.root)
        self.assertEqual(ds.resolve_character("零号·安比配装"), "1381")
        self.assertEqual(ds.resolve_character("安比"), "1011")
        # Single random character contained inside a 2+ char name must not falsely match.
        self.assertIsNone(ds.resolve_character("比"))

    def test_safe_id_rejects_path_traversal(self):
        ds = akads.AkaDataset(self.root)
        self.assertIsNone(ds._load_item("char", "../meta"))

    def test_lore_store_caches_built_at_and_resolves(self):
        lore_dir = self.root / "aka" / "lore"
        lore_dir.mkdir(parents=True)
        (lore_dir / "index.json").write_text(
            json.dumps({"built_at": "2026-11-20", "titles": ["狡兔屋", "白祇重工"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        fname = urllib.parse.quote("狡兔屋", safe="") + ".json"
        (lore_dir / fname).write_text(
            json.dumps({"title": "狡兔屋", "wikitext": "狡兔屋是由妮可创立的万能事务所。"}, ensure_ascii=False),
            encoding="utf-8",
        )
        store = lore.LoreStore(self.root)
        self.assertEqual(store.built_at(), "2026-11-20")
        self.assertEqual(store.resolve("狡兔屋的背景"), "狡兔屋")
        self.assertIn("万能事务所", store.fetch("狡兔屋"))


if __name__ == "__main__":
    unittest.main()
