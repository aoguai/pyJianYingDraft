import sys

from .audio_segment import AudioSegment
from .draft_codec import DraftContentCodec, JianyingDraftCryptoCodec
from .draft_crypto import DraftCryptoConfig
from .draft_folder import DraftFolder
from .effect_segment import EffectSegment, FilterSegment
from .keyframe import KeyframeProperty
from .local_materials import AudioMaterial, CropSettings, VideoMaterial
from .metadata import AudioSceneEffectType
from .metadata import BeautyType, SkinToneType
from .metadata import ToneEffectType
from .metadata import SpeechToSongType
from .metadata import FilterType, FontType, GroupAnimationType, IntroType, MaskType, MixModeType, OutroType
from .metadata import TextIntro, TextLoopAnim, TextOutro, TransitionType
from .metadata import TextBubbleType, TextEffectType
from .metadata import VideoCharacterEffectType, VideoSceneEffectType
from .script_file import ScriptFile
from .template_mode import ExtendMode, ShrinkMode
from .text_segment import TextBackground, TextBorder, TextSegment, TextShadow, TextStyle
from .time_util import SEC, Timerange, tim, trange
from .track import TrackRef, TrackSpec, TrackType
from .video_segment import ClipSettings, StickerSegment, VideoSegment

ISWIN = sys.platform == "win32"
if ISWIN:
    from .jianying_controller import ExportFramerate, ExportResolution, JianyingController


__all__ = [
    "FontType",
    "MaskType",
    "FilterType",
    "TransitionType",
    "MixModeType",
    "IntroType",
    "OutroType",
    "GroupAnimationType",
    "TextIntro",
    "TextOutro",
    "TextLoopAnim",
    "TextEffectType",
    "TextBubbleType",
    "AudioSceneEffectType",
    "ToneEffectType",
    "SpeechToSongType",
    "VideoSceneEffectType",
    "VideoCharacterEffectType",
    "BeautyType",
    "SkinToneType",
    "CropSettings",
    "VideoMaterial",
    "AudioMaterial",
    "KeyframeProperty",
    "Timerange",
    "AudioSegment",
    "VideoSegment",
    "StickerSegment",
    "ClipSettings",
    "EffectSegment",
    "FilterSegment",
    "TextSegment",
    "TextStyle",
    "TextBorder",
    "TextBackground",
    "TextShadow",
    "TrackType",
    "TrackRef",
    "TrackSpec",
    "ShrinkMode",
    "ExtendMode",
    "ScriptFile",
    "DraftFolder",
    "DraftContentCodec",
    "JianyingDraftCryptoCodec",
    "DraftCryptoConfig",
    "SEC",
    "tim",
    "trange",
]

if ISWIN:
    __all__.extend([
        "JianyingController",
        "ExportResolution",
        "ExportFramerate",
    ])
