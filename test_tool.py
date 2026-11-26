"""Offline plugin tests for ZzzWikiPlugin (works both locally and inside MaiBot container)."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
import urllib.parse
from unittest.mock import AsyncMock, patch

PKG = Path(__file__).resolve().parent


def _load_plugin_package():
    injected: dict[str, types.ModuleType] = {}
    if "maibot_sdk" not in sys.modules:
        sdk = types.ModuleType("maibot_sdk")
        sdk_types = types.ModuleType("maibot_sdk.types")

        class _FieldDefault:
            def __init__(self, default=None, default_factory=None):
                self.default = default
                self.default_factory = default_factory

        def Field(default=None, *, default_factory=None, **kwargs):
            del kwargs
            return _FieldDefault(default=default, default_factory=default_factory)

        class PluginConfigBase:
            def __init__(self):
                for name, val in vars(type(self)).items():
                    if name.startswith("_"):
                        continue
                    if isinstance(val, _FieldDefault):
                        setattr(self, name, val.default_factory() if val.default_factory is not None else val.default)

        class MaiBotPlugin:
            @property
            def config(self):
                return self._plugin_config_instance

            @property
            def ctx(self):
                return self._ctx

        sdk.Field = Field
        sdk.PluginConfigBase = PluginConfigBase
        sdk.MaiBotPlugin = MaiBotPlugin
        sdk.Tool = lambda *a, **kw: (lambda fn: fn)
        sdk_types.ToolParameterInfo = lambda **kw: kw
        sdk_types.ToolParamType = types.SimpleNamespace(STRING="string", INTEGER="integer")
        injected["maibot_sdk"] = sdk
        injected["maibot_sdk.types"] = sdk_types

    if "aiohttp" not in sys.modules:
        aiohttp_stub = types.ModuleType("aiohttp")
        aiohttp_stub.ClientTimeout = lambda **kw: None
        aiohttp_stub.ClientSession = object
        injected["aiohttp"] = aiohttp_stub

    with patch.dict(sys.modules, injected):
        pkg_name = "zzz_wiki_pkg_test"
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            PKG / "__init__.py",
            submodule_search_locations=[str(PKG)],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = mod
        spec.loader.exec_module(mod)
        return sys.modules[pkg_name + ".plugin"], sys.modules[pkg_name + ".config"]


plugin_mod, config_mod = _load_plugin_package()


class _OfflineWikiClient:
    def __init__(self) -> None:
        self.closed = False
        self.fetch_calls = 0

    async def resolve_title(self, query: str):
        return "山大王" if query == "山大王" else None

    async def search(self, query: str, limit: int):
        del limit
        if query == "驱动盘":
            return [{"title": "山大王", "snippet": "二件套：护盾强效提升。"}]
        return []

    async def fetch_wikitext(self, title: str):
        self.fetch_calls += 1
        if title == "山大王":
            return "{{驱动盘图鉴|名称=山大王|二件套=护盾强效+15%|四件套=击破角色触发效果}}"
        return ""

    async def close(self):
        self.closed = True


class ZzzWikiPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        aka = root / "aka"
        (aka / "char").mkdir(parents=True)
        (aka / "weapon").mkdir(parents=True)
        (aka / "build").mkdir(parents=True)
        (aka / "meta.json").write_text(json.dumps({"built_at": "offline-test"}), encoding="utf-8")
        (aka / "zh2id.json").write_text(json.dumps({"安比": "1011"}, ensure_ascii=False), encoding="utf-8")
        (aka / "characters.json").write_text(json.dumps([{"id": "1011", "name": "Anby"}]), encoding="utf-8")
        (aka / "weapons.json").write_text("[]", encoding="utf-8")
        (aka / "glossary.json").write_text("{}", encoding="utf-8")
        (aka / "char" / "1011.json").write_text(
            json.dumps(
                {
                    "characterId": "1011",
                    "name": "Anby",
                    "rarity": "A",
                    "element": {"text": "Electric"},
                    "camp": {"text": "Cunning Hares"},
                    "zh": {"name": "安比", "element": "电属性", "camp": "狡兔屋"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.client = _OfflineWikiClient()
        self.plugin = plugin_mod.ZzzWikiPlugin()
        self.plugin._client = self.client
        if hasattr(self.plugin, "set_plugin_config"):
            self.plugin.set_plugin_config(plugin_mod.ZzzWikiPlugin.build_default_config())
            self.plugin._set_context(
                types.SimpleNamespace(
                    logger=logging.getLogger("zzz-wiki-test"),
                    paths=types.SimpleNamespace(data_dir=str(root)),
                )
            )
        else:
            self.plugin._plugin_config_instance = config_mod.ZzzWikiPluginConfig()
            self.plugin._ctx = types.SimpleNamespace(
                logger=logging.getLogger("zzz-wiki-test"),
                paths=types.SimpleNamespace(data_dir=str(root)),
            )

    async def test_local_remote_search_cache_and_url_encoding(self):
        # Empty query rejected.
        empty = await self.plugin.handle_zzz_wiki_lookup(query="  ")
        self.assertFalse(empty["success"])

        # Local aka hit does not pollute remote cache or call remote client.
        local = await self.plugin.handle_zzz_wiki_lookup(query="安比")
        self.assertTrue(local["success"])
        self.assertIn("安比", local["content"])
        self.assertEqual(self.client.fetch_calls, 0)
        self.assertEqual(self.plugin._cache, {})

        # Remote hit quotes Chinese title in source URL and caches second lookup.
        remote1 = await self.plugin.handle_zzz_wiki_lookup(query="山大王")
        remote2 = await self.plugin.handle_zzz_wiki_lookup(query="山大王")
        self.assertTrue(remote1["success"])
        self.assertIn("%E5%B1%B1%E5%A4%A7%E7%8E%8B", remote1["content"])
        self.assertEqual(remote1["content"], remote2["content"])
        self.assertEqual(self.client.fetch_calls, 1)

        # Search mode returns formatted candidates.
        search = await self.plugin.handle_zzz_wiki_lookup(query="驱动盘", mode="search")
        self.assertTrue(search["success"])
        self.assertIn("山大王", search["content"])

    async def test_exact_lore_title_precedes_fuzzy_character(self):
        title = "安比的故事"
        lore = Path(self.temp.name) / "aka" / "lore"
        lore.mkdir()
        (lore / "index.json").write_text(json.dumps({"titles": [title]}), encoding="utf-8")
        page = lore / (urllib.parse.quote(title, safe="") + ".json")
        page.write_text(json.dumps({"wikitext": "这是对应的剧情正文。"}), encoding="utf-8")
        for mode in ("auto", "page"):
            result = await self.plugin.handle_zzz_wiki_lookup(query=title, mode=mode)
            self.assertIn("剧情正文", result["content"])
        self.assertEqual(self.client.fetch_calls, 0)

    async def test_on_unload_and_config_update_close_http_client(self):
        await self.plugin.on_config_update("self", {}, "0.1.0")
        self.assertTrue(self.client.closed)
        self.assertIsNone(self.plugin._client)

        second_client = AsyncMock()
        self.plugin._client = second_client
        await self.plugin.on_unload()
        second_client.close.assert_awaited_once()
        self.assertIsNone(self.plugin._client)


if __name__ == "__main__":
    unittest.main()
