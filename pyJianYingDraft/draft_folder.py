"""草稿文件夹管理器"""

import json
import os
import shutil
import time
import uuid

from datetime import datetime
from typing import Dict, List, Literal, Optional, Set, Tuple

from . import assets
from .draft_codec import (
    DraftJsonState,
    load_draft_json_object,
    plain_json_state,
    write_draft_json_object,
)
from .draft_crypto import DraftCryptoConfig
from .script_file import ScriptFile


class DraftFolder:
    """管理一个文件夹及其内的一系列草稿"""

    folder_path: str
    """根路径"""

    user_data_path: Optional[str]
    """剪映 User Data 路径"""

    crypto_config: DraftCryptoConfig
    """加密草稿 JSON 的本地读写配置"""

    def __init__(
        self,
        folder_path: str,
        user_data_path: Optional[str] = None,
        *,
        crypto_config: Optional[DraftCryptoConfig] = None,
    ):
        """初始化草稿文件夹管理器

        Args:
            folder_path (`str`): 包含若干草稿的文件夹, 一般取剪映保存草稿的位置即可
            user_data_path (`Optional[str]`, optional): 剪映 `User Data` 根目录. 不传则在需要时自动定位.
            crypto_config (`DraftCryptoConfig`, optional): 高版本加密草稿 JSON 的本地读写配置.

        Raises:
            `FileNotFoundError`: 路径不存在
        """
        self.folder_path = folder_path
        self.user_data_path = user_data_path
        self.crypto_config = crypto_config if crypto_config is not None else DraftCryptoConfig()

        if not os.path.exists(self.folder_path):
            raise FileNotFoundError(f"根文件夹 {self.folder_path} 不存在")

    def _resolve_crypto_config(self, crypto_config: Optional[DraftCryptoConfig]) -> DraftCryptoConfig:
        return crypto_config if crypto_config is not None else self.crypto_config

    def list_drafts(self) -> List[str]:
        """列出文件夹中所有草稿的名称

        注意: 本函数只是如实地列出子文件夹的名称, 并不检查它们是否符合草稿的格式
        """
        return [f for f in os.listdir(self.folder_path) if os.path.isdir(os.path.join(self.folder_path, f))]

    def has_draft(self, draft_name: str) -> bool:
        """检查文件夹中是否存在指定名称的草稿

        注意: 本函数只检查文件夹是否存在, 并不检查草稿是否符合剪映的格式

        Args:
            draft_name (`str`): 草稿名称, 即相应文件夹名称
        """
        return draft_name in self.list_drafts()

    def remove(self, draft_name: str) -> None:
        """删除指定名称的草稿

        Args:
            draft_name (`str`): 草稿名称, 即相应文件夹名称

        Raises:
            `FileNotFoundError`: 对应的草稿不存在
        """
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        registered_draft_id: Optional[str] = None
        if self._has_available_user_data_path():
            try:
                registered_draft_id = self._resolve_draft_id(draft_name)
            except (FileNotFoundError, LookupError, ValueError):
                registered_draft_id = None

        shutil.rmtree(draft_path)
        if registered_draft_id:
            self._remove_root_meta_entries({registered_draft_id})
            self._remove_stale_draft_folder_mappings({registered_draft_id})

    def create_folder(self, logical_folder_path: str) -> None:
        """创建逻辑草稿文件夹."""
        normalized_path = self._normalize_logical_folder_path(logical_folder_path)
        folder_meta, _ = self._load_local_draft_folder_configs()
        folders_by_parent_name, _, _ = self._build_folder_indexes(folder_meta["folders"], [])

        path_parts = normalized_path.split("/")
        parent_id = ""
        if len(path_parts) > 1:
            parent_folder = self._resolve_folder_by_parts(path_parts[:-1], folders_by_parent_name)
            parent_id = parent_folder["id"]

        folder_name = path_parts[-1]
        if (parent_id, folder_name) in folders_by_parent_name:
            raise FileExistsError(f"草稿文件夹 {normalized_path} 已存在")

        now = self._current_timestamp()
        folder_meta["folders"].append({
            "createdTime": now,
            "id": str(uuid.uuid4()),
            "modifiedTime": now,
            "name": folder_name,
            "parentId": parent_id,
        })
        self._write_local_draft_folder_config(self._get_folder_meta_info_path(), folder_meta, timestamp=now)

    def remove_folder(
        self,
        logical_folder_path: str,
        on_non_empty: Literal["block", "move_drafts_to_root", "delete_drafts"] = "block",
    ) -> None:
        """删除逻辑草稿文件夹."""
        if on_non_empty not in {"block", "move_drafts_to_root", "delete_drafts"}:
            raise ValueError(f"不支持的 on_non_empty 策略: {on_non_empty}")

        normalized_path = self._normalize_logical_folder_path(logical_folder_path)
        folder_meta, mappings_meta = self._load_local_draft_folder_configs()
        folders = folder_meta["folders"]
        mappings = mappings_meta["mappings"]
        folders_by_parent_name, children_by_parent, _ = self._build_folder_indexes(folders, mappings)
        target_folder = self._resolve_folder_by_parts(normalized_path.split("/"), folders_by_parent_name)
        subtree_folder_ids = self._collect_subtree_folder_ids(target_folder["id"], children_by_parent)
        subtree_mappings = [mapping for mapping in mappings if mapping["folderId"] in subtree_folder_ids]
        affected_draft_ids = list(dict.fromkeys(str(mapping["draftId"]) for mapping in subtree_mappings))
        subtree_is_non_empty = len(subtree_folder_ids) > 1 or bool(subtree_mappings)

        if on_non_empty == "block" and subtree_is_non_empty:
            raise OSError(f"草稿文件夹 {normalized_path} 非空，无法删除")

        if on_non_empty == "move_drafts_to_root" and subtree_mappings:
            self._resolve_draft_names_in_current_root(affected_draft_ids)

        if on_non_empty == "delete_drafts" and subtree_mappings:
            draft_names_by_id = self._resolve_draft_names_in_current_root(affected_draft_ids)
            for draft_name in draft_names_by_id.values():
                if not os.path.isdir(os.path.join(self.folder_path, draft_name)):
                    raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")
            for draft_name in draft_names_by_id.values():
                self.remove(draft_name)

        now = self._current_timestamp()
        folder_meta["folders"] = [folder for folder in folders if folder["id"] not in subtree_folder_ids]
        if on_non_empty in {"move_drafts_to_root", "delete_drafts"} and affected_draft_ids:
            mappings_meta["mappings"] = [
                mapping for mapping in mappings if str(mapping["draftId"]) not in affected_draft_ids
            ]
        else:
            mappings_meta["mappings"] = [
                mapping for mapping in mappings if mapping["folderId"] not in subtree_folder_ids
            ]
        self._write_local_draft_folder_config(self._get_folder_meta_info_path(), folder_meta, timestamp=now)
        self._write_local_draft_folder_config(self._get_draft_folder_mappings_path(), mappings_meta, timestamp=now)
        if on_non_empty == "delete_drafts" and affected_draft_ids:
            self._remove_root_meta_entries(set(affected_draft_ids))

    def move_draft_to_folder(self, draft_name: str, logical_folder_path: str) -> None:
        """把一个草稿移入逻辑草稿文件夹."""
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.isdir(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        normalized_path = self._normalize_logical_folder_path(logical_folder_path)
        folder_meta, mappings_meta = self._load_local_draft_folder_configs()
        draft_id = self._resolve_draft_id(draft_name)
        folders_by_parent_name, _, mappings_by_draft_id = self._build_folder_indexes(
            folder_meta["folders"],
            mappings_meta["mappings"],
        )
        target_folder = self._resolve_folder_by_parts(normalized_path.split("/"), folders_by_parent_name)
        current_mappings = mappings_by_draft_id.get(draft_id, [])
        current_folder_ids = {mapping["folderId"] for mapping in current_mappings}
        if len(current_mappings) == 1 and current_folder_ids == {target_folder["id"]}:
            return

        now = self._current_timestamp()
        mappings_meta["mappings"] = [
            mapping for mapping in mappings_meta["mappings"] if mapping["draftId"] != draft_id
        ]
        mappings_meta["mappings"].append({
            "draftId": draft_id,
            "folderId": target_folder["id"],
            "mappedTime": now,
        })
        self._write_local_draft_folder_config(self._get_draft_folder_mappings_path(), mappings_meta, timestamp=now)

    def move_draft_to_root(self, draft_name: str) -> None:
        """把一个草稿移回顶层视图."""
        _, mappings_meta = self._load_local_draft_folder_configs()
        draft_id = self._resolve_draft_id(draft_name)
        remaining_mappings = [mapping for mapping in mappings_meta["mappings"] if mapping["draftId"] != draft_id]
        if len(remaining_mappings) == len(mappings_meta["mappings"]):
            return

        mappings_meta["mappings"] = remaining_mappings
        self._write_local_draft_folder_config(
            self._get_draft_folder_mappings_path(),
            mappings_meta,
            timestamp=self._current_timestamp(),
        )

    def create_draft(self, draft_name: str, width: int, height: int, fps: int = 30, *,
                     maintrack_adsorb: bool = True,
                     allow_replace: bool = False,
                     crypto_config: Optional[DraftCryptoConfig] = None) -> ScriptFile:
        """创建一个新草稿并开始编辑, 编辑完成后使用`ScriptFile.save()`保存即可

        Args:
            draft_name (`str`): 草稿名称, 即相应文件夹名称
            width (`int`): 视频宽度, 单位为像素
            height (`int`): 视频高度, 单位为像素
            fps (`int`, optional): 视频帧率. 默认为30.
            maintrack_adsorb (`bool`, optional): 是否启用主轨道吸附（主轨磁吸）. 默认启用.
            allow_replace (`bool`, optional): 是否允许覆盖与`draft_name`重名的草稿. 默认为否.
            crypto_config (`DraftCryptoConfig`, optional): 保存加密草稿 JSON 时使用的本地配置.

        Raises:
            `FileExistsError`: 已存在与`draft_name`重名的草稿, 但不允许覆盖.
        """
        draft_path = os.path.join(self.folder_path, draft_name)
        if os.path.exists(draft_path):
            if not allow_replace:
                raise FileExistsError(f"草稿文件夹 {draft_name} 已存在且不允许覆盖")
            self.remove(draft_name)

        # 创建草稿文件夹
        os.makedirs(draft_path)
        shutil.copy(assets.get_asset_path("DRAFT_META_TEMPLATE"), os.path.join(draft_path, "draft_meta_info.json"))

        # 创建草稿文件
        script_file = ScriptFile(width, height, fps, maintrack_adsorb)
        script_file.save_path = os.path.join(draft_path, "draft_content.json")
        script_file._draft_crypto_config = self._resolve_crypto_config(crypto_config)

        return self._configure_script_file_registration(script_file, draft_name, is_new_draft=True)

    def inspect_material(self, draft_name: str) -> None:
        """输出指定名称草稿中的贴纸素材元数据

        Args:
            draft_name (`str`): 草稿名称, 即相应文件夹名称

        Raises:
            `FileNotFoundError`: 对应的草稿不存在
        """
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        script_file = self.load_template(draft_name)
        script_file.inspect_material()

    def load_template(
        self,
        draft_name: str,
        *,
        crypto_config: Optional[DraftCryptoConfig] = None,
    ) -> ScriptFile:
        """在文件夹中打开一个草稿作为模板, 并在其上进行编辑

        Args:
            draft_name (`str`): 草稿名称, 即相应文件夹名称
            crypto_config (`DraftCryptoConfig`, optional): 加密草稿 JSON 的本地读写配置.

        Returns:
            `ScriptFile`: 以模板模式打开的草稿对象

        Raises:
            `FileNotFoundError`: 对应的草稿不存在
        """
        draft_path = os.path.join(self.folder_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        script_file = ScriptFile.load_template(
            os.path.join(draft_path, "draft_content.json"),
            crypto_config=self._resolve_crypto_config(crypto_config),
        )
        return self._configure_script_file_registration(script_file, draft_name, is_new_draft=False)

    def duplicate_as_template(
        self,
        template_name: str,
        new_draft_name: str,
        allow_replace: bool = False,
        *,
        crypto_config: Optional[DraftCryptoConfig] = None,
    ) -> ScriptFile:
        """复制一份给定的草稿, 并在复制出的新草稿上进行编辑

        Args:
            template_name (`str`): 原草稿名称
            new_draft_name (`str`): 新草稿名称
            allow_replace (`bool`, optional): 是否允许覆盖与`new_draft_name`重名的草稿. 默认为否.
            crypto_config (`DraftCryptoConfig`, optional): 加密草稿 JSON 的本地读写配置.

        Returns:
            `ScriptFile`: 以模板模式打开的**复制后的**草稿对象

        Raises:
            `FileNotFoundError`: 原始草稿不存在
            `FileExistsError`: 已存在与`new_draft_name`重名的草稿, 但不允许覆盖.
        """
        template_path = os.path.join(self.folder_path, template_name)
        new_draft_path = os.path.join(self.folder_path, new_draft_name)
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板草稿 {template_name} 不存在")
        if self._normalize_filesystem_path(template_path) == self._normalize_filesystem_path(new_draft_path):
            raise ValueError("复制后的新草稿不能与模板草稿同名")
        if os.path.exists(new_draft_path):
            if not allow_replace:
                raise FileExistsError(f"新草稿 {new_draft_name} 已存在且不允许覆盖")
            self.remove(new_draft_name)

        # 复制草稿文件夹
        shutil.copytree(template_path, new_draft_path, dirs_exist_ok=allow_replace)

        # 打开草稿
        script_file = ScriptFile.load_template(
            os.path.join(new_draft_path, "draft_content.json"),
            crypto_config=self._resolve_crypto_config(crypto_config),
        )
        return self._configure_script_file_registration(script_file, new_draft_name, is_new_draft=True)

    def _configure_script_file_registration(
        self,
        script_file: ScriptFile,
        draft_name: str,
        *,
        is_new_draft: bool,
    ) -> ScriptFile:
        draft_root_path = os.path.abspath(self.folder_path)
        draft_path = os.path.abspath(os.path.join(self.folder_path, draft_name))
        script_file._draft_registration_context = {
            "allow_generate_draft_id": is_new_draft,
            "draft_name": draft_name,
            "draft_path": draft_path,
            "draft_root_path": draft_root_path,
            "pending_first_registration": is_new_draft,
            "refresh_project_id_on_first_save": is_new_draft,
        }
        script_file._post_save_hook = self._register_saved_draft
        return script_file

    def _register_saved_draft(self, script_file: ScriptFile) -> None:
        context = getattr(script_file, "_draft_registration_context", None)
        if not isinstance(context, dict):
            return
        if script_file.save_path is None:
            return
        if not self._has_available_user_data_path():
            return

        draft_path = str(context.get("draft_path") or os.path.dirname(script_file.save_path))
        draft_name = str(context.get("draft_name") or os.path.basename(draft_path))
        draft_root_path = str(context.get("draft_root_path") or os.path.abspath(self.folder_path))
        draft_meta_path = os.path.join(draft_path, "draft_meta_info.json")
        crypto_config = getattr(script_file, "_draft_crypto_config", self.crypto_config)
        now_us = self._current_timestamp_us()

        root_meta_payload = self._read_root_meta_payload()
        root_entries = root_meta_payload["all_draft_store"]
        existing_entry_for_path = self._find_root_meta_entry(root_entries, draft_path)

        meta_info, meta_state = self._read_draft_meta_info(draft_meta_path, crypto_config=crypto_config)
        draft_id = self._coerce_str(context.get("draft_id"))
        if not draft_id:
            if context.get("allow_generate_draft_id", False):
                draft_id = self._new_uuid()
                context["draft_id"] = draft_id
                context["allow_generate_draft_id"] = False
            else:
                if existing_entry_for_path is not None:
                    draft_id = self._coerce_str(existing_entry_for_path.get("draft_id"))
                if not draft_id:
                    candidate_draft_id = self._coerce_str(meta_info.get("draft_id"))
                    if candidate_draft_id:
                        existing_entry_for_id = self._find_root_meta_entry_by_draft_id(root_entries, candidate_draft_id)
                        if existing_entry_for_id is not None:
                            existing_entry_path = self._coerce_str(existing_entry_for_id.get("draft_fold_path"))
                            if existing_entry_path and (
                                self._normalize_filesystem_path(existing_entry_path)
                                != self._normalize_filesystem_path(draft_path)
                            ):
                                return
                        draft_id = candidate_draft_id
                if not draft_id:
                    return
                context["draft_id"] = draft_id

        replaced_draft_ids = self._collect_root_meta_draft_ids_for_path(root_entries, draft_path)
        replaced_draft_ids.discard(draft_id)
        existing_entry = self._find_root_meta_entry(root_entries, draft_path, draft_id)
        is_first_registration = bool(context.get("pending_first_registration", False))
        if is_first_registration:
            tm_draft_create = self._coerce_int(context.get("tm_draft_create"), default=now_us)
        else:
            tm_draft_create = self._coerce_int(
                context.get("tm_draft_create"),
                meta_info.get("tm_draft_create"),
                existing_entry.get("tm_draft_create") if existing_entry else None,
                default=now_us,
            )
        context["tm_draft_create"] = tm_draft_create

        tm_draft_modified = now_us
        tm_duration = int(script_file.duration or 0)
        timeline_materials_size = self._calculate_timeline_materials_size(script_file)
        draft_new_version = self._coerce_str(meta_info.get("draft_new_version"))
        if not draft_new_version and existing_entry is not None:
            draft_new_version = self._coerce_str(existing_entry.get("draft_new_version"))

        self._sync_registered_draft_meta_info(
            draft_meta_path,
            meta_info=meta_info,
            meta_state=meta_state,
            draft_name=draft_name,
            draft_path=draft_path,
            draft_root_path=draft_root_path,
            draft_id=draft_id,
            draft_new_version=draft_new_version,
            tm_draft_create=tm_draft_create,
            tm_draft_modified=tm_draft_modified,
            tm_duration=tm_duration,
            timeline_materials_size=timeline_materials_size,
            crypto_config=crypto_config,
        )

        root_entry = self._build_root_meta_entry(
            existing_entry=existing_entry,
            draft_name=draft_name,
            draft_path=draft_path,
            draft_root_path=draft_root_path,
            draft_id=draft_id,
            draft_new_version=draft_new_version,
            tm_draft_create=tm_draft_create,
            tm_draft_modified=tm_draft_modified,
            tm_duration=tm_duration,
            timeline_materials_size=timeline_materials_size,
        )
        self._upsert_root_meta_entry(root_entries, root_entry, draft_path, draft_id)
        self._write_root_meta_info(root_meta_payload)
        self._remove_stale_draft_folder_mappings(replaced_draft_ids)

        context["pending_first_registration"] = False

    def _read_root_meta_payload(self) -> Dict[str, object]:
        root_meta_info_path = self._get_root_meta_info_path()
        if os.path.exists(root_meta_info_path):
            payload = self._read_json_object(root_meta_info_path)
            all_draft_store = payload.setdefault("all_draft_store", [])
            if not isinstance(all_draft_store, list):
                raise ValueError(f"root_meta_info.json 中的 all_draft_store 不是数组: {root_meta_info_path}")
            return payload
        return {"all_draft_store": []}

    def _read_draft_meta_info(
        self,
        meta_path: str,
        *,
        crypto_config: Optional[DraftCryptoConfig] = None,
    ) -> Tuple[Dict[str, object], DraftJsonState]:
        config = self._resolve_crypto_config(crypto_config)
        if os.path.exists(meta_path):
            return load_draft_json_object(meta_path, crypto_config=config)
        meta_info = self._read_json_object(str(assets.get_asset_path("DRAFT_META_TEMPLATE")))
        return meta_info, plain_json_state(meta_path, crypto_config=config)

    def _sync_registered_draft_meta_info(
        self,
        meta_path: str,
        *,
        meta_info: Dict[str, object],
        meta_state: DraftJsonState,
        draft_name: str,
        draft_path: str,
        draft_root_path: str,
        draft_id: str,
        draft_new_version: str,
        tm_draft_create: int,
        tm_draft_modified: int,
        tm_duration: int,
        timeline_materials_size: int,
        crypto_config: Optional[DraftCryptoConfig] = None,
    ) -> None:
        meta_info["draft_id"] = draft_id
        meta_info["draft_name"] = draft_name
        meta_info["draft_root_path"] = draft_root_path
        meta_info["draft_fold_path"] = draft_path
        meta_info["draft_cover"] = "draft_cover.jpg"
        meta_info["tm_draft_create"] = tm_draft_create
        meta_info["tm_draft_modified"] = tm_draft_modified
        meta_info["tm_duration"] = tm_duration
        meta_info["draft_timeline_materials_size_"] = timeline_materials_size
        if draft_new_version:
            meta_info["draft_new_version"] = draft_new_version
        elif "draft_new_version" not in meta_info:
            meta_info["draft_new_version"] = ""
        write_draft_json_object(
            meta_path,
            meta_info,
            state=meta_state,
            crypto_config=self._resolve_crypto_config(crypto_config),
            trailing_newline=True,
        )

    def _build_root_meta_entry(
        self,
        *,
        existing_entry: Optional[Dict[str, object]],
        draft_name: str,
        draft_path: str,
        draft_root_path: str,
        draft_id: str,
        draft_new_version: str,
        tm_draft_create: int,
        tm_draft_modified: int,
        tm_duration: int,
        timeline_materials_size: int,
    ) -> Dict[str, object]:
        managed_fields: Dict[str, object] = {
            "draft_cover": os.path.join(draft_path, "draft_cover.jpg"),
            "draft_fold_path": draft_path,
            "draft_id": draft_id,
            "draft_json_file": os.path.join(draft_path, "draft_content.json"),
            "draft_name": draft_name,
            "draft_new_version": draft_new_version,
            "draft_root_path": draft_root_path,
            "draft_timeline_materials_size": timeline_materials_size,
            "streaming_edit_draft_ready": True,
            "tm_draft_create": tm_draft_create,
            "tm_draft_modified": tm_draft_modified,
            "tm_draft_removed": 0,
            "tm_duration": tm_duration,
        }
        if existing_entry is not None:
            entry = dict(existing_entry)
            entry.update(managed_fields)
            return entry

        entry: Dict[str, object] = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_invisible": False,
            "draft_is_web_article_video": False,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
        }
        entry.update(managed_fields)
        return entry

    def _find_root_meta_entry_by_draft_id(
        self,
        root_entries: List[Dict[str, object]],
        draft_id: str,
    ) -> Optional[Dict[str, object]]:
        for entry in root_entries:
            if not isinstance(entry, dict):
                continue
            if self._coerce_str(entry.get("draft_id")) == draft_id:
                return entry
        return None

    def _find_root_meta_entry(
        self,
        root_entries: List[Dict[str, object]],
        draft_path: str,
        draft_id: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        normalized_draft_path = self._normalize_filesystem_path(draft_path)
        for entry in root_entries:
            if not isinstance(entry, dict):
                continue
            if draft_id and self._coerce_str(entry.get("draft_id")) == draft_id:
                return entry
            draft_fold_path = self._coerce_str(entry.get("draft_fold_path"))
            if draft_fold_path and self._normalize_filesystem_path(draft_fold_path) == normalized_draft_path:
                return entry
        return None

    def _collect_root_meta_draft_ids_for_path(
        self,
        root_entries: List[Dict[str, object]],
        draft_path: str,
    ) -> Set[str]:
        normalized_draft_path = self._normalize_filesystem_path(draft_path)
        matched_draft_ids: Set[str] = set()
        for entry in root_entries:
            if not isinstance(entry, dict):
                continue
            draft_fold_path = self._coerce_str(entry.get("draft_fold_path"))
            if not draft_fold_path:
                continue
            if self._normalize_filesystem_path(draft_fold_path) != normalized_draft_path:
                continue
            draft_id = self._coerce_str(entry.get("draft_id"))
            if draft_id:
                matched_draft_ids.add(draft_id)
        return matched_draft_ids

    def _upsert_root_meta_entry(
        self,
        root_entries: List[Dict[str, object]],
        root_entry: Dict[str, object],
        draft_path: str,
        draft_id: str,
    ) -> None:
        normalized_draft_path = self._normalize_filesystem_path(draft_path)
        matched_indexes: List[int] = []
        for index, entry in enumerate(root_entries):
            if not isinstance(entry, dict):
                continue
            entry_draft_id = self._coerce_str(entry.get("draft_id"))
            entry_draft_path = self._coerce_str(entry.get("draft_fold_path"))
            if entry_draft_id == draft_id:
                matched_indexes.append(index)
                continue
            if entry_draft_path and self._normalize_filesystem_path(entry_draft_path) == normalized_draft_path:
                matched_indexes.append(index)

        if not matched_indexes:
            root_entries.insert(0, root_entry)
            return

        first_index = matched_indexes[0]
        root_entries[first_index] = root_entry
        for index in reversed(matched_indexes[1:]):
            del root_entries[index]

    def _write_root_meta_info(self, root_meta_payload: Dict[str, object]) -> None:
        self._write_compact_json_file(self._get_root_meta_info_path(), root_meta_payload)

    def _remove_root_meta_entries(self, draft_ids: Set[str]) -> None:
        if not draft_ids:
            return
        if not self._has_available_user_data_path():
            return

        root_meta_info_path = self._get_root_meta_info_path()
        if not os.path.exists(root_meta_info_path):
            return

        root_meta_payload = self._read_root_meta_payload()
        root_entries = root_meta_payload["all_draft_store"]
        filtered_entries: List[Dict[str, object]] = []
        changed = False
        for entry in root_entries:
            if not isinstance(entry, dict):
                filtered_entries.append(entry)
                continue
            entry_draft_id = self._coerce_str(entry.get("draft_id"))
            if entry_draft_id and entry_draft_id in draft_ids:
                changed = True
                continue
            filtered_entries.append(entry)

        if not changed:
            return

        root_meta_payload["all_draft_store"] = filtered_entries
        self._write_root_meta_info(root_meta_payload)

    def _remove_stale_draft_folder_mappings(self, draft_ids: Set[str]) -> None:
        if not draft_ids:
            return

        mappings_path = self._get_draft_folder_mappings_path()
        if not os.path.exists(mappings_path):
            return

        mappings_meta = self._read_json_object(mappings_path)
        mappings = mappings_meta.get("mappings")
        if not isinstance(mappings, list):
            raise ValueError(f"draft_folder_mappings.json 中的 mappings 不是数组: {mappings_path}")

        remaining_mappings = [
            mapping
            for mapping in mappings
            if str(mapping.get("draftId", "")) not in draft_ids
        ]
        if len(remaining_mappings) == len(mappings):
            return

        mappings_meta["mappings"] = remaining_mappings
        self._write_local_draft_folder_config(mappings_path, mappings_meta, timestamp=self._current_timestamp())

    def _load_local_draft_folder_configs(self) -> Tuple[Dict[str, object], Dict[str, object]]:
        self._ensure_local_draft_folder_configs()
        folder_meta = self._read_json_object(self._get_folder_meta_info_path())
        mappings_meta = self._read_json_object(self._get_draft_folder_mappings_path())
        folders = folder_meta.setdefault("folders", [])
        mappings = mappings_meta.setdefault("mappings", [])
        if not isinstance(folders, list):
            raise ValueError(f"folder_meta_info.json 中的 folders 不是数组: {self._get_folder_meta_info_path()}")
        if not isinstance(mappings, list):
            raise ValueError(
                f"draft_folder_mappings.json 中的 mappings 不是数组: {self._get_draft_folder_mappings_path()}"
            )
        return folder_meta, mappings_meta

    def _ensure_local_draft_folder_configs(self) -> None:
        os.makedirs(self._get_local_draft_folder_dir(), exist_ok=True)

        folder_meta_path = self._get_folder_meta_info_path()
        mappings_path = self._get_draft_folder_mappings_path()
        recycle_bin_path = self._get_recycle_bin_path()
        mapping_recycle_path = self._get_draft_mapping_recycle_bin_path()

        if not os.path.exists(folder_meta_path):
            self._write_local_draft_folder_config(folder_meta_path, self._new_folder_meta_info())
        elif not os.path.exists(f"{folder_meta_path}.bak"):
            self._write_json_file(f"{folder_meta_path}.bak", self._read_json_object(folder_meta_path))

        if not os.path.exists(mappings_path):
            self._write_local_draft_folder_config(mappings_path, self._new_draft_folder_mappings())
        elif not os.path.exists(f"{mappings_path}.bak"):
            self._write_json_file(f"{mappings_path}.bak", self._read_json_object(mappings_path))

        if not os.path.exists(recycle_bin_path):
            self._write_local_draft_folder_config(recycle_bin_path, self._new_recycle_bin())
        elif not os.path.exists(f"{recycle_bin_path}.bak"):
            self._write_json_file(f"{recycle_bin_path}.bak", self._read_json_object(recycle_bin_path))

        if not os.path.exists(mapping_recycle_path):
            self._write_local_draft_folder_config(mapping_recycle_path, self._new_draft_mapping_recycle_bin())
        elif not os.path.exists(f"{mapping_recycle_path}.bak"):
            self._write_json_file(f"{mapping_recycle_path}.bak", self._read_json_object(mapping_recycle_path))

    def _build_folder_indexes(
        self,
        folders: List[Dict[str, object]],
        mappings: List[Dict[str, object]],
    ) -> Tuple[
        Dict[Tuple[str, str], Dict[str, object]],
        Dict[str, List[Dict[str, object]]],
        Dict[str, List[Dict[str, object]]],
    ]:
        folders_by_parent_name: Dict[Tuple[str, str], Dict[str, object]] = {}
        children_by_parent: Dict[str, List[Dict[str, object]]] = {}
        mappings_by_draft_id: Dict[str, List[Dict[str, object]]] = {}

        for folder in folders:
            parent_id = str(folder.get("parentId", "") or "")
            name = str(folder["name"])
            folders_by_parent_name[(parent_id, name)] = folder
            children_by_parent.setdefault(parent_id, []).append(folder)

        for mapping in mappings:
            draft_id = str(mapping["draftId"])
            mappings_by_draft_id.setdefault(draft_id, []).append(mapping)

        return folders_by_parent_name, children_by_parent, mappings_by_draft_id

    def _resolve_folder_by_parts(
        self,
        path_parts: List[str],
        folders_by_parent_name: Dict[Tuple[str, str], Dict[str, object]],
    ) -> Dict[str, object]:
        parent_id = ""
        current_folder: Optional[Dict[str, object]] = None

        for path_part in path_parts:
            current_folder = folders_by_parent_name.get((parent_id, path_part))
            if current_folder is None:
                raise FileNotFoundError(f"草稿文件夹 {'/'.join(path_parts)} 不存在")
            parent_id = str(current_folder["id"])

        return current_folder

    def _collect_subtree_folder_ids(
        self,
        root_folder_id: str,
        children_by_parent: Dict[str, List[Dict[str, object]]],
    ) -> Set[str]:
        subtree_folder_ids: Set[str] = set()
        pending_folder_ids = [root_folder_id]

        while pending_folder_ids:
            current_folder_id = pending_folder_ids.pop()
            if current_folder_id in subtree_folder_ids:
                continue

            subtree_folder_ids.add(current_folder_id)
            for child_folder in children_by_parent.get(current_folder_id, []):
                pending_folder_ids.append(str(child_folder["id"]))

        return subtree_folder_ids

    def _resolve_draft_id(self, draft_name: str) -> str:
        expected_draft_path = self._normalize_filesystem_path(
            os.path.abspath(os.path.join(self.folder_path, draft_name))
        )
        for draft_info in self._load_root_meta_info():
            if draft_info.get("tm_draft_removed", 0) != 0:
                continue
            draft_fold_path = draft_info.get("draft_fold_path")
            draft_id = draft_info.get("draft_id")
            if not draft_fold_path or not draft_id:
                continue
            if self._normalize_filesystem_path(str(draft_fold_path)) == expected_draft_path:
                return str(draft_id)

        raise LookupError(f"草稿文件夹 {draft_name} 的 draft_id 未解析")

    def _resolve_draft_names_in_current_root(self, draft_ids: List[str]) -> Dict[str, str]:
        unique_draft_ids = list(dict.fromkeys(draft_ids))
        unresolved_draft_ids = set(unique_draft_ids)
        draft_names_by_id: Dict[str, str] = {}
        current_root_path = self._normalize_filesystem_path(os.path.abspath(self.folder_path))

        for draft_info in self._load_root_meta_info():
            if draft_info.get("tm_draft_removed", 0) != 0:
                continue

            draft_id = draft_info.get("draft_id")
            draft_fold_path = draft_info.get("draft_fold_path")
            if not draft_id or not draft_fold_path:
                continue

            draft_id = str(draft_id)
            if draft_id not in unresolved_draft_ids:
                continue

            raw_draft_path = str(draft_fold_path)
            if self._normalize_filesystem_path(os.path.dirname(raw_draft_path)) != current_root_path:
                continue

            draft_names_by_id[draft_id] = os.path.basename(
                os.path.normpath(raw_draft_path.replace("\\", os.sep).replace("/", os.sep))
            )
            unresolved_draft_ids.remove(draft_id)
            if not unresolved_draft_ids:
                break

        if unresolved_draft_ids:
            unresolved_text = ", ".join(sorted(unresolved_draft_ids))
            raise LookupError(f"存在不属于当前根目录的草稿映射: {unresolved_text}")

        return draft_names_by_id

    def _load_root_meta_info(self) -> List[Dict[str, object]]:
        root_meta_info_path = self._get_root_meta_info_path()
        if not os.path.exists(root_meta_info_path):
            raise FileNotFoundError(f"root_meta_info.json 不存在: {root_meta_info_path}")

        root_meta_info = self._read_json_object(root_meta_info_path)
        all_draft_store = root_meta_info.get("all_draft_store", [])
        if not isinstance(all_draft_store, list):
            raise ValueError(f"root_meta_info.json 中的 all_draft_store 不是数组: {root_meta_info_path}")
        return all_draft_store

    def _normalize_logical_folder_path(self, logical_folder_path: str) -> str:
        normalized_path = logical_folder_path.replace("\\", "/").strip("/")
        if not normalized_path:
            raise ValueError("logical_folder_path 不能为空")

        path_parts = normalized_path.split("/")
        if any(part in {"", ".", ".."} for part in path_parts):
            raise ValueError(f"非法逻辑路径: {logical_folder_path}")

        return "/".join(path_parts)

    def _normalize_filesystem_path(self, path: str) -> str:
        normalized_path = path.replace("\\", os.sep).replace("/", os.sep)
        return os.path.normcase(os.path.normpath(normalized_path))

    def _has_available_user_data_path(self) -> bool:
        return bool(self.user_data_path) or bool(os.environ.get("LOCALAPPDATA"))

    def _get_user_data_path(self) -> str:
        if self.user_data_path:
            return self.user_data_path

        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise EnvironmentError("未设置 LOCALAPPDATA，无法自动定位 JianyingPro User Data")

        return os.path.join(local_app_data, "JianyingPro", "User Data")

    def _get_local_draft_folder_dir(self) -> str:
        return os.path.join(self._get_user_data_path(), "Config", "LocalDraftFolder")

    def _get_root_meta_info_path(self) -> str:
        return os.path.join(self._get_user_data_path(), "Projects", "com.lveditor.draft", "root_meta_info.json")

    def _get_folder_meta_info_path(self) -> str:
        return os.path.join(self._get_local_draft_folder_dir(), "folder_meta_info.json")

    def _get_draft_folder_mappings_path(self) -> str:
        return os.path.join(self._get_local_draft_folder_dir(), "draft_folder_mappings.json")

    def _get_recycle_bin_path(self) -> str:
        return os.path.join(self._get_local_draft_folder_dir(), "recycle_bin.json")

    def _get_draft_mapping_recycle_bin_path(self) -> str:
        return os.path.join(self._get_local_draft_folder_dir(), "draft_mapping_recycle_bin.json")

    def _current_timestamp(self) -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    def _current_timestamp_us(self) -> int:
        return time.time_ns() // 1_000

    def _new_uuid(self) -> str:
        return str(uuid.uuid4()).upper()

    def _coerce_str(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text if text else ""

    def _coerce_int(self, *values: object, default: int = 0) -> int:
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return default

    def _calculate_timeline_materials_size(self, script_file: ScriptFile) -> int:
        total_size = 0
        for media in script_file._iter_media_for_meta_info():
            media_path = self._coerce_str(media.get("path"))
            if not media_path:
                continue
            try:
                total_size += os.path.getsize(media_path)
            except OSError:
                continue
        return total_size

    def _new_folder_meta_info(self) -> Dict[str, object]:
        return {
            "folders": [],
            "timestamp": self._current_timestamp(),
            "version": "1.0",
        }

    def _new_draft_folder_mappings(self) -> Dict[str, object]:
        return {
            "mappings": [],
            "timestamp": self._current_timestamp(),
            "version": "1.0",
        }

    def _new_recycle_bin(self) -> Dict[str, object]:
        return {
            "recycled_folders": [],
            "timestamp": self._current_timestamp(),
            "version": "1.0",
        }

    def _new_draft_mapping_recycle_bin(self) -> Dict[str, object]:
        return {
            "recycled_draft_mappings": [],
            "timestamp": self._current_timestamp(),
            "version": "1.0",
        }

    def _read_json(self, path: str) -> object:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _read_json_object(self, path: str) -> Dict[str, object]:
        data = self._read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"JSON 文件顶层不是对象: {path}")
        return data

    def _serialize_json(self, data: Dict[str, object]) -> str:
        return f"{json.dumps(data, ensure_ascii=False, indent=4)}\n"

    def _serialize_compact_json(self, data: Dict[str, object]) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def _replace_text_file(self, path: str, content: str, *, newline: str = "\n") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8", newline=newline) as file_obj:
                file_obj.write(content)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _write_json_file(self, path: str, data: Dict[str, object]) -> None:
        self._replace_text_file(path, self._serialize_json(data))

    def _write_compact_json_file(self, path: str, data: Dict[str, object]) -> None:
        self._replace_text_file(path, self._serialize_compact_json(data))

    def _write_local_draft_folder_config(
        self,
        path: str,
        data: Dict[str, object],
        *,
        timestamp: Optional[str] = None,
    ) -> None:
        payload = dict(data)
        if timestamp is not None:
            payload["timestamp"] = timestamp
        serialized = self._serialize_json(payload)
        self._replace_text_file(path, serialized)
        self._replace_text_file(f"{path}.bak", serialized)
