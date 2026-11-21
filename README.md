# MaiBot 绝区零中文维基查询插件（maibot-zzz-wiki）

适用于 MaiBot SDK v2 的《绝区零》（Zenless Zone Zero）维基查询工具插件。插件向模型注册 `zzz_wiki_lookup` 工具，默认实时请求 B 站绝区零中文维基（`wiki.biligame.com/zzz`）的 MediaWiki API，清洗模板与语法并提炼结构化摘要，帮助机器人在聊到角色配装、音擎、驱动盘、怪物或世界观设定时给出更准确的回答。

## 功能特性

- **开箱即用**：默认无需额外数据文件或 API 密钥，安装启用后即可实时查询 `wiki.biligame.com/zzz`。
- **智能词条解析**：自动处理查询词后缀（如「妮可配装」自动尝试解析「妮可」）、重定向标题与模糊搜索回退。
- **结构化摘要清洗**：解析 MediaWiki 模板与章节结构，按核心属性、配装推荐、阵容搭配等高价值区块优先截断，避免长文塞爆模型上下文。
- **内存 TTL 缓存**：对远端维基查询结果做带过期时间的内存缓存，减少重复请求。
- **可选本地离线语料加速**：若用户在插件数据目录下自备 `aka/` 结构化数据集或 `aka/lore/` 语料库，插件将自动优先命中本地数据，缺失时无缝回落在线维基接口。

## 运行环境

- **MaiBot**：`1.3.0` ~ `1.4.99`（`maibot-plugin-sdk >= 2.20.0`）
- **Python 依赖**：`aiohttp >= 3.8.0`（MaiBot 宿主已内置）
- **能力权限**：`[]`（零高危宿主权限，仅发起只读 HTTP GET 请求）

## 安装与使用

1. 在 MaiBot WebUI 插件市场中搜索安装「绝区零维基」，或将本仓库放入 MaiBot 插件目录并重启/重载插件。
2. 无需额外配置即可使用；如需修改超时时间、返回字数上限或缓存时长，可在 WebUI 插件配置页中调整（也可参考 `config.example.toml`）。
3. 聊天中提及绝区零角色、驱动盘、音擎或配装问题时，模型会自动调用 `zzz_wiki_lookup` 工具。

### 工具参数：`zzz_wiki_lookup`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | `string` | 是 | 要查询的词条名或关键词，例如 `安比`、`妮可配装`、`山大王驱动盘` |
| `mode` | `string` | 否 | `auto`（默认，精确解析失败后自动搜索）、`page`（仅读精确词条）、`search`（仅列候选列表） |
| `max_chars` | `integer` | 否 | 单次返回正文最大字符数（200～4000），留空使用配置默认值 |

## 配置项说明

| 分组 | 字段 | 默认值 | 说明 |
|---|---|---|---|
| `network` | `base_url` | `https://wiki.biligame.com/zzz` | MediaWiki 站点根地址 |
| `network` | `timeout_seconds` | `15` | 单次请求超时时间（秒，3～60） |
| `result` | `max_chars` | `1800` | 返回给模型的正文最大字符数（200～4000） |
| `result` | `max_candidates` | `5` | 搜索模式下返回的候选词条上限（1～10） |
| `result` | `cache_ttl_seconds` | `600` | 远端查询结果的内存缓存时长（秒，0 表示禁用缓存） |

## 可选本地离线语料目录结构

本插件不包含任何第三方离线数据包。如用户希望自行准备离线数据以降低在线查询延迟，可按以下结构放置到插件数据目录（`ctx.paths.data_dir`）：

- `<data_dir>/aka/`：可选的角色/音擎/配装 JSON 索引（`meta.json`、`zh2id.json`、`characters.json`、`weapons.json`、`glossary.json`、`char/<id>.json`、`weapon/<id>.json`、`build/<id>.json`）
- `<data_dir>/aka/lore/`：可选的词条标题索引与正文缓存（`index.json` 与 `<urlencoded_title>.json`）

目录不存在时自动跳过本地层，直接请求在线 MediaWiki 接口。

## 运行单元测试

```bash
python -m unittest discover -p "test_*.py"
```

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
