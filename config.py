"""zzz-wiki 插件配置模型。"""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    """插件基础信息"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    config_version: str = Field(
        default="0.1.0",
        description="配置版本，请勿手动修改。",
        json_schema_extra={"disabled": True, "hidden": True, "label": "配置版本", "order": 99},
    )


class NetworkSection(PluginConfigBase):
    """网络访问配置"""

    __ui_label__ = "网络"
    __ui_icon__ = "globe"
    __ui_order__ = 1

    base_url: str = Field(
        default="https://wiki.biligame.com/zzz",
        description="维基站点根地址（MediaWiki）",
        json_schema_extra={"label": "维基站点地址", "order": 0},
    )
    timeout_seconds: int = Field(
        default=15,
        ge=3,
        le=60,
        description="单次请求超时时间（秒）",
        json_schema_extra={"label": "请求超时（秒）", "order": 1},
    )


class ResultSection(PluginConfigBase):
    """返回内容配置"""

    __ui_label__ = "返回"
    __ui_icon__ = "list"
    __ui_order__ = 2

    max_chars: int = Field(
        default=1800,
        ge=200,
        le=4000,
        description="返回给模型的正文最大字符数",
        json_schema_extra={"label": "正文最大字数", "order": 0},
    )
    max_candidates: int = Field(
        default=5,
        ge=1,
        le=10,
        description="搜索时返回的候选词条上限",
        json_schema_extra={"label": "候选词条上限", "order": 1},
    )
    cache_ttl_seconds: int = Field(
        default=600,
        ge=0,
        le=86400,
        description="内存缓存时间（秒），0 表示不缓存",
        json_schema_extra={"label": "缓存时间（秒）", "order": 2},
    )


class ZzzWikiPluginConfig(PluginConfigBase):
    """zzz-wiki 顶层配置"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
    result: ResultSection = Field(default_factory=ResultSection)
