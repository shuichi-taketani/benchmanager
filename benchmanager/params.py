"""ツール固有パラメータ定義(``params.toml``)の読み込みと動的検証(§7)。

ドライバごとに同梱された ``drivers/<tool>/params.toml`` を読み、

* CLI / GUI 共通の検証ロジック(``pydantic.create_model`` による動的モデル生成)
* ``matrix`` に指定できるパラメータ(``sweepable = true``)の判定
* ``benchman params <tool>`` のヘルプ表示

に用いる。仕様書 §7 の例は YAML 風の記法で書かれているが、本文の
「設定ファイルと形式を統一し TOML とする」に従い TOML 配列テーブルで表現する。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .i18n import get_lang

#: params.toml で使用できる型
PARAM_TYPES = ("int", "float", "bool", "string", "choice", "size", "duration", "list")

_SIZE_RE = re.compile(r"^\d+(\.\d+)?\s*[kmgtpKMGTP]?[bB]?$")
_DURATION_RE = re.compile(r"^\d+(\.\d+)?\s*(ms|s|m|h)?$", re.IGNORECASE)


class LocalizedText(BaseModel):
    """``{en = "...", ja = "..."}`` 形式の多言語テキスト。"""

    model_config = ConfigDict(extra="allow")

    en: str = ""
    ja: str = ""

    def get(self, lang: str | None = None) -> str:
        lang = lang or get_lang()
        value = getattr(self, lang, "") or self.en or self.ja
        return value


class ParamGroup(BaseModel):
    """GUI のフォームグループ。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: LocalizedText = Field(default_factory=LocalizedText)


class ShownIf(BaseModel):
    """条件付き表示(Phase 3 の GUI 用。Phase 1 では保持のみ)。"""

    model_config = ConfigDict(extra="forbid")

    param: str
    equals: Any = None
    not_equals: Any = None


class ParamDef(BaseModel):
    """1 パラメータの定義。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["int", "float", "bool", "string", "choice", "size", "duration", "list"]
    group: str = "general"
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: list[Any] = Field(default_factory=list)
    sweepable: bool = False
    required: bool = False
    label: LocalizedText = Field(default_factory=LocalizedText)
    help: LocalizedText = Field(default_factory=LocalizedText)
    shown_if: ShownIf | None = None

    def range_text(self) -> str:
        if self.type == "choice":
            return "/".join(str(c) for c in self.choices)
        if self.min is not None or self.max is not None:
            low = "" if self.min is None else _num(self.min)
            high = "" if self.max is None else _num(self.max)
            return f"{low}..{high}"
        return "-"


class ParamsMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    version: str = "1.0"


class ParamsSpec(BaseModel):
    """params.toml 全体。"""

    model_config = ConfigDict(extra="forbid")

    meta: ParamsMeta
    groups: list[ParamGroup] = Field(default_factory=list)
    params: list[ParamDef] = Field(default_factory=list)

    # -- 参照 -------------------------------------------------------------
    def by_name(self) -> dict[str, ParamDef]:
        return {p.name: p for p in self.params}

    def names(self) -> list[str]:
        return [p.name for p in self.params]

    def sweepable_names(self) -> list[str]:
        return [p.name for p in self.params if p.sweepable]

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params if p.default is not None}

    # -- 検証 -------------------------------------------------------------
    def build_model(self) -> type[BaseModel]:
        """パラメータ定義から pydantic モデルを動的生成する。

        CLI と GUI(Phase 3)で同一の検証ロジックを共有するための入口。
        """
        fields: dict[str, tuple[Any, Any]] = {}
        for param in self.params:
            annotation = _annotation_for(param)
            default = ... if param.required else param.default
            fields[param.name] = (annotation | None if default is None else annotation, default)
        model = create_model(  # type: ignore[call-overload]
            f"{self.meta.tool.capitalize()}ToolParams",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        return model

    def validate_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """値を検証し、既定値で補完した dict を返す。

        :raises pydantic.ValidationError: 検証に失敗した場合
        """
        model = self.build_model()
        instance = model(**values)
        return instance.model_dump(exclude_none=True)


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _annotation_for(param: ParamDef) -> Any:
    """パラメータ型に対応する型注釈を返す。"""
    if param.type == "int":
        return Annotated[int, Field(ge=param.min, le=param.max)]
    if param.type == "float":
        return Annotated[float, Field(ge=param.min, le=param.max)]
    if param.type == "bool":
        return bool
    if param.type == "choice":
        if param.choices:
            return Literal[tuple(param.choices)]  # type: ignore[valid-type]
        return str
    if param.type == "size":
        return Annotated[str, Field(pattern=_SIZE_RE.pattern)]
    if param.type == "duration":
        return Annotated[str, Field(pattern=_DURATION_RE.pattern)]
    if param.type == "list":
        return list
    return str


def load_params_spec(path: str | Path) -> ParamsSpec:
    """params.toml を読み込む。"""
    path = Path(path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return ParamsSpec(**data)
