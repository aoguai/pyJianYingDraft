"""定义文本片段及其相关类"""

import json
import uuid
from copy import deepcopy

from typing import Dict, Tuple, Any, List
from typing import Union, Optional, Literal

from .time_util import Timerange, tim
from .segment import ClipSettings, VisualSegment
from .animation import SegmentAnimations, Text_animation

from .metadata import FontType, EffectMeta, TextBubbleType, TextEffectType
from .metadata import TextIntro, TextOutro, TextLoopAnim

class TextStyle:
    """字体样式类"""

    size: float
    """字体大小"""

    bold: bool
    """是否加粗"""
    italic: bool
    """是否斜体"""
    underline: bool
    """是否加下划线"""

    color: Tuple[float, float, float]
    """字体颜色, RGB三元组, 取值范围为[0, 1]"""
    alpha: float
    """字体不透明度"""

    align: Literal[0, 1, 2]
    """对齐方式"""
    vertical: bool
    """是否为竖排文本"""

    letter_spacing: int
    """字符间距"""
    line_spacing: int
    """行间距"""

    auto_wrapping: bool
    """是否自动换行"""
    max_line_width: float
    """最大行宽, 取值范围为[0, 1]"""

    def __init__(self, *, size: float = 8.0, bold: bool = False, italic: bool = False, underline: bool = False,
                 color: Tuple[float, float, float] = (1.0, 1.0, 1.0), alpha: float = 1.0,
                 align: Literal[0, 1, 2] = 0, vertical: bool = False,
                 letter_spacing: int = 0, line_spacing: int = 0,
                 auto_wrapping: bool = False, max_line_width: float = 0.82):
        """
        Args:
            size (`float`, optional): 字体大小, 默认为8.0
            bold (`bool`, optional): 是否加粗, 默认为否
            italic (`bool`, optional): 是否斜体, 默认为否
            underline (`bool`, optional): 是否加下划线, 默认为否
            color (`Tuple[float, float, float]`, optional): 字体颜色, RGB三元组, 取值范围为[0, 1], 默认为白色
            alpha (`float`, optional): 字体不透明度, 取值范围[0, 1], 默认不透明
            align (`int`, optional): 对齐方式, 0: 左对齐, 1: 居中, 2: 右对齐, 默认为左对齐
            vertical (`bool`, optional): 是否为竖排文本, 默认为否
            letter_spacing (`int`, optional): 字符间距, 定义与剪映中一致, 默认为0
            line_spacing (`int`, optional): 行间距, 定义与剪映中一致, 默认为0
            auto_wrapping (`bool`, optional): 是否自动换行, 默认关闭
            max_line_width (`float`, optional): 每行最大行宽占屏幕宽度比例, 取值范围为[0, 1], 默认为0.82
        """
        self.size = size
        self.bold = bold
        self.italic = italic
        self.underline = underline

        self.color = color
        self.alpha = alpha

        self.align = align
        self.vertical = vertical

        self.letter_spacing = letter_spacing
        self.line_spacing = line_spacing

        self.auto_wrapping = auto_wrapping
        self.max_line_width = max_line_width

    def export_style_range(self, start: int, end: int, *,
                           font: Optional[Union["FontType", EffectMeta]] = None,
                           effect: Optional[TextEffectType] = None,
                           shadow: Optional["TextShadow"] = None,
                           border: Optional["TextBorder"] = None,
                           use_letter_color: Optional[bool] = None) -> Dict[str, Any]:
        """为富文本构建内容样式"""
        if isinstance(font, FontType):
            font = font.value

        style = {
            "fill": {
                "alpha": 1.0,
                "content": {
                    "render_type": "solid",
                    "solid": {
                        "alpha": 1.0,
                        "color": list(self.color)
                    }
                }
            },
            "range": [start, end],
            "size": self.size,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "strokes": [border.export_json()] if border else []
        }
        if font:
            style["font"] = {
                "id": font.resource_id,
                "path": "D:"  # 占位 path
            }
        if effect is not None:
            if not isinstance(effect, TextEffectType):
                raise TypeError("Invalid text effect type %s" % type(effect))
            style["effectStyle"] = {
                "id": effect.value.resource_id,
                "path": "C:"  # 占位 path
            }
        if shadow:
            style["shadows"] = [shadow.export_json()]
        if use_letter_color is not None:
            style["useLetterColor"] = use_letter_color
        return style

class TextBorder:
    """文本描边的参数"""

    alpha: float
    """描边不透明度"""
    color: Tuple[float, float, float]
    """描边颜色, RGB三元组, 取值范围为[0, 1]"""
    width: float
    """描边宽度"""

    def __init__(self, *, alpha: float = 1.0, color: Tuple[float, float, float] = (0.0, 0.0, 0.0), width: float = 40.0):
        """
        Args:
            alpha (`float`, optional): 描边不透明度, 取值范围[0, 1], 默认为1.0
            color (`Tuple[float, float, float]`, optional): 描边颜色, RGB三元组, 取值范围为[0, 1], 默认为黑色
            width (`float`, optional): 描边宽度, 与剪映中一致, 取值范围为[0, 100], 默认为40.0
        """
        self.alpha = alpha
        self.color = color
        self.width = width / 100.0 * 0.2  # 此映射可能不完全正确

    def export_json(self) -> Dict[str, Any]:
        """导出JSON数据, 放置在素材content的styles中"""
        return {
            "content": {
                "solid": {
                    "alpha": self.alpha,
                    "color": list(self.color),
                }
            },
            "width": self.width
        }

class TextBackground:
    """文本背景参数"""

    style: Literal[1, 2]
    """背景样式"""

    alpha: float
    """背景不透明度"""
    color: str
    """背景颜色, 格式为'#RRGGBB'"""
    round_radius: float
    """背景圆角半径"""
    height: float
    """背景高度"""
    width: float
    """背景宽度"""
    horizontal_offset: float
    """背景水平偏移"""
    vertical_offset: float
    """背景竖直偏移"""

    def __init__(self, *, color: str, style: Literal[1, 2] = 1, alpha: float = 1.0, round_radius: float = 0.0,
                 height: float = 0.14, width: float = 0.14,
                 horizontal_offset: float = 0.5, vertical_offset: float = 0.5):
        """
        Args:
            color (`str`): 背景颜色, 格式为'#RRGGBB'
            style (`int`, optional): 背景样式, 1和2分别对应剪映中的两种样式, 默认为1
            alpha (`float`, optional): 背景不透明度, 与剪映中一致, 取值范围[0, 1], 默认为1.0
            round_radius (`float`, optional): 背景圆角半径, 与剪映中一致, 取值范围[0, 1], 默认为0.0
            height (`float`, optional): 背景高度, 与剪映中一致, 取值范围为[0, 1], 默认为0.14
            width (`float`, optional): 背景宽度, 与剪映中一致, 取值范围为[0, 1], 默认为0.14
            horizontal_offset (`float`, optional): 背景水平偏移, 与剪映中一致, 取值范围为[0, 1], 默认为0.5
            vertical_offset (`float`, optional): 背景竖直偏移, 与剪映中一致, 取值范围为[0, 1], 默认为0.5
        """
        self.style = style

        self.alpha = alpha
        self.color = color
        self.round_radius = round_radius
        self.height = height
        self.width = width
        self.horizontal_offset = horizontal_offset * 2 - 1
        self.vertical_offset = vertical_offset * 2 - 1

    def export_json(self) -> Dict[str, Any]:
        """生成子JSON数据, 在TextSegment导出时合并到其中"""
        return {
            "background_style": self.style,
            "background_color": self.color,
            "background_alpha": self.alpha,
            "background_round_radius": self.round_radius,
            "background_height": self.height,
            "background_width": self.width,
            "background_horizontal_offset": self.horizontal_offset,
            "background_vertical_offset": self.vertical_offset,
        }

class TextBubble:
    """文本气泡素材, 与滤镜素材本质上一致"""

    global_id: str
    """气泡全局id, 由程序自动生成"""

    effect_id: str
    resource_id: str

    def __init__(self, effect_id: str, resource_id: str):
        self.global_id = uuid.uuid4().hex
        self.effect_id = effect_id
        self.resource_id = resource_id

    def export_json(self) -> Dict[str, Any]:
        return {
            "apply_target_type": 0,
            "effect_id": self.effect_id,
            "id": self.global_id,
            "resource_id": self.resource_id,
            "type": "text_shape",
            "value": 1.0,
            # 不导出path和request_id
        }

class TextEffect(TextBubble):
    """文本花字素材, 与滤镜素材本质上也一致"""

    def export_json(self) -> Dict[str, Any]:
        ret = super().export_json()
        ret["type"] = "text_effect"
        ret["source_platform"] = 1
        return ret

class TextShadow:
    """文本阴影参数"""

    alpha: float
    """阴影不透明度, 取值范围为[0, 1]"""
    color: Tuple[float, float, float]
    """阴影颜色, RGB三元组, 取值范围为[0, 1]"""
    diffuse: float
    """阴影扩散程度, 此处定义与剪映中一致, 取值范围为[0, 100]"""
    distance: float
    """阴影距离, 取值范围为[0, 100]"""
    angle: float
    """阴影角度, 取值范围为[-180, 180]"""

    def __init__(self, *, alpha: float = 1.0, color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                 diffuse: float = 15.0, distance: float = 5.0, angle: float = -45.0):
        """
        Args:
            alpha (`float`, optional): 阴影不透明度, 取值范围为[0, 1], 默认为1.0
            color (`Tuple[float, float, float]`, optional): 阴影颜色, RGB三元组, 取值范围为[0, 1], 默认为黑色
            diffuse (`float`, optional): 阴影扩散程度, 此处定义与剪映中一致, 取值范围为[0, 100], 默认为15.0
            distance (`float`, optional): 阴影距离, 取值范围为[0, 100], 默认为5.0
            angle (`float`, optional): 阴影角度, 取值范围为[-180, 180], 默认为-45.0
        """
        self.alpha = alpha
        self.color = color
        self.diffuse = diffuse
        self.distance = distance
        self.angle = angle

    def export_json(self) -> Dict[str, Any]:
        return {
            "diffuse": self.diffuse / 100.0 / 6,  # /6是剪映自带的映射
            "alpha": self.alpha,
            "distance": self.distance,
            "content": {
                "solid": {
                    "color": list(self.color),
                }
            },
            "angle": self.angle
        }

class TextSegment(VisualSegment):
    """文本片段类, 目前仅支持设置基本的字体样式"""

    text: str
    """文本内容"""
    font: Optional[EffectMeta]
    """字体类型"""
    style: TextStyle
    """字体样式"""
    style_ranges: Optional[List[Dict[str, Any]]]
    """富文本样式, None表示无富文本样式"""

    border: Optional[TextBorder]
    """文本描边参数, None表示无描边"""
    background: Optional[TextBackground]
    """文本背景参数, None表示无背景"""
    shadow: Optional[TextShadow]
    """文本阴影参数, None表示无阴影"""

    bubble: Optional[TextBubble]
    """文本气泡效果, 在放入轨道时加入素材列表中"""
    effect: Optional[TextEffect]
    """文本花字效果, 在放入轨道时加入素材列表中, 目前仅支持一部分花字效果"""

    def __init__(self, text: str, timerange: Timerange, *,
                 font: Optional[FontType] = None,
                 style: Optional[TextStyle] = None, clip_settings: Optional[ClipSettings] = None,
                 border: Optional[TextBorder] = None, background: Optional[TextBackground] = None,
                 shadow: Optional[TextShadow] = None,
                 style_ranges: Optional[List[Dict[str, Any]]] = None):
        """创建文本片段, 并指定其时间信息、字体样式及图像调节设置

        片段创建完成后, 可通过`ScriptFile.add_segment`方法将其添加到轨道中

        Args:
            text (`str`): 文本内容
            timerange (`Timerange`): 片段在轨道上的时间范围
            font (`Font_type`, optional): 字体类型, 默认为系统字体
            style (`TextStyle`, optional): 字体样式, 包含大小/颜色/对齐/透明度等.
            clip_settings (`ClipSettings`, optional): 图像调节设置, 默认不做任何变换
            border (`TextBorder`, optional): 文本描边参数, 默认无描边
            background (`TextBackground`, optional): 文本背景参数, 默认无背景
            shadow (`TextShadow`, optional): 文本阴影参数, 默认无阴影
            style_ranges (`List[Dict[str, Any]]`, optional): 逐字/富文本样式, 默认无样式
        """
        super().__init__(uuid.uuid4().hex, None, timerange, 1.0, 1.0, False, clip_settings=clip_settings)

        self.text = text
        self.font = font.value if font else None
        self.style = style or TextStyle()
        self.border = border
        self.background = background
        self.shadow = shadow
        self.style_ranges = style_ranges

        self.bubble = None
        self.effect = None

    @classmethod
    def create_from_template(cls, text: str, timerange: Timerange, template: "TextSegment") -> "TextSegment":
        """根据模板创建新的文本片段, 并指定其文本内容"""
        new_segment = cls(text, timerange, style=deepcopy(template.style), clip_settings=deepcopy(template.clip_settings),
                          border=deepcopy(template.border), background=deepcopy(template.background),
                          shadow=deepcopy(template.shadow))
        new_segment.font = deepcopy(template.font)
        if template.style_ranges is not None:
            new_segment.style_ranges = deepcopy(template.style_ranges)

        # 处理动画等
        if template.animations_instance:
            new_segment.animations_instance = deepcopy(template.animations_instance)
            new_segment.animations_instance.animation_id = uuid.uuid4().hex
            new_segment.extra_material_refs.append(new_segment.animations_instance.animation_id)
        if template.bubble:
            new_segment.bubble = TextBubble(template.bubble.effect_id, template.bubble.resource_id)
            new_segment.extra_material_refs.append(new_segment.bubble.global_id)
        if template.effect:
            new_segment.effect = TextEffect(template.effect.effect_id, template.effect.resource_id)
            new_segment.extra_material_refs.append(new_segment.effect.global_id)

        return new_segment

    def set_style_ranges(self, style_ranges: List[Dict[str, Any]]) -> "TextSegment":
        """设置富文本样式"""
        self.style_ranges = style_ranges
        return self

    def add_style_range(self, start: int, end: int, *,
                        style: Optional[TextStyle] = None,
                        font: Optional[Union[FontType, EffectMeta]] = None,
                        effect: Optional[TextEffectType] = None,
                        shadow: Optional[TextShadow] = None,
                        border: Optional[TextBorder] = None,
                        use_letter_color: Optional[bool] = None) -> "TextSegment":
        """添加富文本样式"""
        if self.style_ranges is None:
            self.style_ranges = []

        if font is None:
            font = self.font

        style_obj = (style or self.style).export_style_range(
            start, end,
            font=font,
            effect=effect,
            shadow=shadow if shadow is not None else self.shadow,
            border=border if border is not None else self.border,
            use_letter_color=use_letter_color
        )
        if effect is None and self.effect and "effectStyle" not in style_obj:
            style_obj["effectStyle"] = {
                "id": self.effect.resource_id,
                "path": "C:"  # 占位 path
            }
        self.style_ranges.append(style_obj)
        return self

    def set_style_ranges_by_chars(self, *,
                                  styles: Optional[List[Optional[TextStyle]]] = None,
                                  fonts: Optional[List[Optional[Union[FontType, EffectMeta]]]] = None,
                                  effects: Optional[List[Optional[TextEffectType]]] = None,
                                  shadows: Optional[List[Optional[TextShadow]]] = None,
                                  borders: Optional[List[Optional[TextBorder]]] = None,
                                  use_letter_colors: Optional[List[Optional[bool]]] = None) -> "TextSegment":
        """为逐字样式批量生成range，列表长度需与文本长度一致。"""
        length = len(self.text)
        for lst in [styles, fonts, effects, shadows, borders, use_letter_colors]:
            if lst is not None and len(lst) != length:
                raise ValueError("逐字样式列表长度必须与文本长度一致")

        self.style_ranges = []
        for i in range(length):
            self.add_style_range(
                i, i + 1,
                style=styles[i] if styles else None,
                font=fonts[i] if fonts else None,
                effect=effects[i] if effects else None,
                shadow=shadows[i] if shadows else None,
                border=borders[i] if borders else None,
                use_letter_color=use_letter_colors[i] if use_letter_colors else None
            )
        return self

    def _merge_style_range(self, style_range: Dict[str, Any]) -> Dict[str, Any]:
        if "range" not in style_range:
            raise ValueError("style_range missing 'range'")
        start, end = style_range["range"]
        base = self.style.export_style_range(
            start, end,
            font=self.font,
            effect=None,
            shadow=self.shadow,
            border=self.border
        )
        if self.effect and "effectStyle" not in base:
            base["effectStyle"] = {
                "id": self.effect.resource_id,
                "path": "C:"  # 占位 path
            }
        merged = deepcopy(base)
        merged.update(style_range)
        return merged

    def add_animation(self, animation_type: Union[TextIntro, TextOutro, TextLoopAnim],
                      duration: Union[str, float, None] = None) -> "TextSegment":
        """将给定的入场/出场/循环动画添加到此片段的动画列表中, 出入场动画的持续时间可以自行设置, 循环动画则会自动填满其余无动画部分

        注意: 若希望同时使用循环动画和入出场动画, 请**先添加出入场动画再添加循环动画**

        Args:
            animation_type (`TextIntro`, `TextOutro` or `TextLoopAnim`): 文本动画类型.
            duration (`str` or `float`, optional): 动画持续时间, 单位为微秒, 仅对入场/出场动画有效.
                若传入字符串则会调用`tim()`函数进行解析. 默认使用动画的时长
        """
        if duration is None:
            duration = animation_type.value.duration
        duration = min(tim(duration), self.target_timerange.duration)

        if isinstance(animation_type, TextIntro):
            start = 0
        elif isinstance(animation_type, TextOutro):
            start = self.target_timerange.duration - duration
        elif isinstance(animation_type, TextLoopAnim):
            intro_trange = self.animations_instance and self.animations_instance.get_animation_trange("in")
            outro_trange = self.animations_instance and self.animations_instance.get_animation_trange("out")
            start = intro_trange.start if intro_trange else 0
            duration = self.target_timerange.duration - start - (outro_trange.duration if outro_trange else 0)
        else:
            raise TypeError("Invalid animation type %s" % type(animation_type))

        if self.animations_instance is None:
            self.animations_instance = SegmentAnimations()
            self.extra_material_refs.append(self.animations_instance.animation_id)

        self.animations_instance.add_animation(Text_animation(animation_type, start, duration))

        return self

    def add_bubble(self, effect: TextBubbleType) -> "TextSegment":
        """根据素材信息添加气泡效果, 相应素材信息可通过`ScriptFile.inspect_material`从模板中获取

        Args:
            effect (`TextBubbleType`): 文本气泡类型
        """
        if not isinstance(effect, TextBubbleType):
            raise TypeError("Invalid bubble effect type %s" % type(effect))
        self.bubble = TextBubble(effect.value.effect_id, effect.value.resource_id)
        self.extra_material_refs.append(self.bubble.global_id)
        return self

    def add_effect(self, effect: TextEffectType) -> "TextSegment":
        """根据素材信息添加花字效果, 相应素材信息可通过`ScriptFile.inspect_material`从模板中获取

        Args:
            effect (`TextEffectType`): 文本花字类型
        """
        if not isinstance(effect, TextEffectType):
            raise TypeError("Invalid text effect type %s" % type(effect))
        self.effect = TextEffect(effect.value.effect_id, effect.value.resource_id)
        self.extra_material_refs.append(self.effect.global_id)
        return self

    def export_material(self) -> Dict[str, Any]:
        """与此文本片段联系的素材, 以此不再单独定义Text_material类"""
        if self.style_ranges:
            styles = [self._merge_style_range(style) for style in self.style_ranges]
        else:
            styles = [
                self.style.export_style_range(
                    0, len(self.text),
                    font=self.font,
                    effect=None,
                    shadow=self.shadow,
                    border=self.border
                )
            ]
            if self.effect and "effectStyle" not in styles[0]:
                styles[0]["effectStyle"] = {
                    "id": self.effect.resource_id,
                    "path": "C:"  # 占位 path
                }

        content_json = {
            "styles": styles,
            "text": self.text
        }

        has_border = self.border is not None or any(style.get("strokes") for style in styles)
        has_shadow = self.shadow is not None or any(style.get("shadows") for style in styles)

        # 叠加各类效果的flag
        check_flag: int = 7
        if has_border:
            check_flag |= 8
        if self.background:
            check_flag |= 16
        if has_shadow:
            check_flag |= 32

        ret = {
            "id": self.material_id,
            "content": json.dumps(content_json, ensure_ascii=False),

            "typesetting": int(self.style.vertical),
            "alignment": self.style.align,
            "letter_spacing": self.style.letter_spacing * 0.05,
            "line_spacing": 0.02 + self.style.line_spacing * 0.05,

            "line_feed": 1,
            "line_max_width": self.style.max_line_width,
            "force_apply_line_max_width": False,

            "check_flag": check_flag,

            "type": "subtitle" if self.style.auto_wrapping else "text",

            # 混合 (+4)
            "global_alpha": self.style.alpha,

            # 发光 (+64)，属性由extra_material_refs记录
        }

        if self.style_ranges:
            ret["is_rich_text"] = True

        if self.background:
            ret.update(self.background.export_json())

        return ret
