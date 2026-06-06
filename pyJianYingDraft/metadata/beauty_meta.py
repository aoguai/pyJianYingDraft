"""剪映美颜美体相关元数据"""

from .effect_meta import EffectEnum
from .effect_meta import EffectMeta, EffectParam


class BeautyType(EffectEnum):
    """剪映“美颜-皮肤管理”效果类型"""

    # is_vip 暂不参与 figure 导出；待本地缓存索引确认后再校准。
    磨皮 = EffectMeta("磨皮", False, "7408076339699322112", "", "994f60413c341bcd67e8ff5d71f70313", [
        EffectParam.extra("category_id", "skin_management"),
        EffectParam.extra("sub_type", "auto_beauty"),
        EffectParam.extra("third_resource_id", "6976822940608238093"),
        EffectParam.extra("value_mode", "value"),
    ])
    美白 = EffectMeta("美白", False, "7408076966164778255", "", "b3d8ae209229d6f3f16cbdc3d749b6f3", [
        EffectParam.extra("category_id", "skin_management"),
        EffectParam.extra("sub_type", "none"),
        EffectParam.extra("third_resource_id", "6998408303826965006"),
        EffectParam.extra("value_mode", "value"),
    ])
    匀肤 = EffectMeta("匀肤", False, "7408077497142693135", "", "a38a9366972ca48f463a408b24214dee", [
        EffectParam.extra("category_id", "skin_management"),
        EffectParam.extra("sub_type", "auto_beauty"),
        EffectParam.extra("third_resource_id", "7106322605304451614"),
        EffectParam.extra("value_mode", "adjust_params"),
        EffectParam.extra("intensity_key", "face_adjust_yunfu"),
        EffectParam.extra("adjust_param_name", "0"),
    ])
    清晰 = EffectMeta("清晰", False, "7598460431144963366", "", "d8d3201fa6c77f369501cf4baae130ab", [
        EffectParam.extra("category_id", "skin_management"),
        EffectParam.extra("sub_type", "none"),
        EffectParam.extra("third_resource_id", "0"),
        EffectParam.extra("value_mode", "value"),
    ])


class SkinToneType(EffectEnum):
    """剪映“美颜-肤色”效果类型"""

    粉白 = EffectMeta("粉白", False, "7408757645705760000", "", "cfcae38577406c6a76af57f0d85be645", [
        EffectParam.extra("category_id", "auto-beauty2"),
        EffectParam.extra("sub_type", "exclusion"),
        EffectParam.extra("third_resource_id", "7148721123838923295"),
        EffectParam.extra("value_mode", "face_adjust_params"),
        EffectParam.extra("cold_warm_param_name", "face_adjust_skin_ColdWarm"),
        EffectParam.extra("intensity_param_name", "face_adjust_skin_Intensity"),
    ])
