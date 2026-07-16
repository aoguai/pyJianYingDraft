"""Private Jianying folder registration and sidecar metadata operations.

This module deliberately owns User Data and sidecar-file effects.  The 0.3
``ScriptFile`` facade stays responsible only for timeline serialization, while
``DraftFolder`` composes this collaborator when private fork features are used.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from . import assets
from .draft_codec import (
    DraftContentCodec,
    load_json_object_with_codec,
    write_json_object_with_codec,
)
from .script_file import ScriptFile


class DraftFolderRegistration:
    """Coordinate private draft registration without coupling it to track code."""

    def __init__(
        self,
        draft_root_path: str,
        *,
        user_data_path: Optional[str] = None,
        content_codec: Optional[DraftContentCodec] = None,
    ) -> None:
        self.draft_root_path = os.path.abspath(draft_root_path)
        self.user_data_path = user_data_path
        self.content_codec = content_codec

    def configure_script_file(
        self,
        script_file: ScriptFile,
        draft_name: str,
        *,
        is_new_draft: bool,
    ) -> ScriptFile:
        draft_path = os.path.abspath(os.path.join(self.draft_root_path, draft_name))
        script_file._draft_registration_context = {
            "allow_generate_draft_id": is_new_draft,
            "draft_name": draft_name,
            "draft_path": draft_path,
            "pending_first_registration": is_new_draft,
            "refresh_project_id_on_first_save": is_new_draft,
        }
        script_file._before_save_hook = self._refresh_project_id_before_save
        script_file._after_save_hook = self._register_after_save
        return script_file

    def remove_draft(self, draft_name: str) -> None:
        """Remove a draft directory and its private registration references."""
        draft_path = os.path.join(self.draft_root_path, draft_name)
        if not os.path.exists(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        registered_draft_id: Optional[str] = None
        if self.has_available_user_data_path():
            try:
                registered_draft_id = self.resolve_draft_id(draft_name)
            except (FileNotFoundError, LookupError, ValueError):
                registered_draft_id = None

        shutil.rmtree(draft_path)
        if registered_draft_id:
            self.remove_root_meta_entries({registered_draft_id})
            self.remove_stale_draft_folder_mappings({registered_draft_id})

    def create_folder(self, logical_folder_path: str) -> None:
        """Create a Jianying logical draft folder under the configured User Data."""
        normalized_path = self.normalize_logical_folder_path(logical_folder_path)
        folder_meta, _ = self.load_local_draft_folder_configs()
        folders_by_parent_name, _, _ = self.build_folder_indexes(folder_meta["folders"], [])

        path_parts = normalized_path.split("/")
        parent_id = ""
        if len(path_parts) > 1:
            parent_folder = self.resolve_folder_by_parts(path_parts[:-1], folders_by_parent_name)
            parent_id = str(parent_folder["id"])

        folder_name = path_parts[-1]
        if (parent_id, folder_name) in folders_by_parent_name:
            raise FileExistsError(f"草稿文件夹 {normalized_path} 已存在")

        now = self.current_timestamp()
        folder_meta["folders"].append({
            "createdTime": now,
            "id": str(uuid.uuid4()),
            "modifiedTime": now,
            "name": folder_name,
            "parentId": parent_id,
        })
        self.write_local_draft_folder_config(self.folder_meta_info_path(), folder_meta, timestamp=now)

    def move_draft_to_folder(self, draft_name: str, logical_folder_path: str) -> None:
        """Associate one registered draft with a logical Jianying folder."""
        draft_path = os.path.join(self.draft_root_path, draft_name)
        if not os.path.isdir(draft_path):
            raise FileNotFoundError(f"草稿文件夹 {draft_name} 不存在")

        normalized_path = self.normalize_logical_folder_path(logical_folder_path)
        folder_meta, mappings_meta = self.load_local_draft_folder_configs()
        draft_id = self.resolve_draft_id(draft_name)
        folders_by_parent_name, _, mappings_by_draft_id = self.build_folder_indexes(
            folder_meta["folders"],
            mappings_meta["mappings"],
        )
        target_folder = self.resolve_folder_by_parts(normalized_path.split("/"), folders_by_parent_name)
        current_mappings = mappings_by_draft_id.get(draft_id, [])
        if len(current_mappings) == 1 and {mapping["folderId"] for mapping in current_mappings} == {target_folder["id"]}:
            return

        now = self.current_timestamp()
        mappings_meta["mappings"] = [
            mapping for mapping in mappings_meta["mappings"] if mapping["draftId"] != draft_id
        ]
        mappings_meta["mappings"].append({
            "draftId": draft_id,
            "folderId": target_folder["id"],
            "mappedTime": now,
        })
        self.write_local_draft_folder_config(self.draft_folder_mappings_path(), mappings_meta, timestamp=now)

    def move_draft_to_root(self, draft_name: str) -> None:
        """Remove a draft's logical-folder association."""
        _, mappings_meta = self.load_local_draft_folder_configs()
        draft_id = self.resolve_draft_id(draft_name)
        remaining_mappings = [mapping for mapping in mappings_meta["mappings"] if mapping["draftId"] != draft_id]
        if len(remaining_mappings) == len(mappings_meta["mappings"]):
            return
        mappings_meta["mappings"] = remaining_mappings
        self.write_local_draft_folder_config(
            self.draft_folder_mappings_path(),
            mappings_meta,
            timestamp=self.current_timestamp(),
        )

    def remove_folder(
        self,
        logical_folder_path: str,
        on_non_empty: Literal["block", "move_drafts_to_root", "delete_drafts"] = "block",
    ) -> None:
        """Remove a logical folder and explicitly resolve all nested drafts."""
        if on_non_empty not in {"block", "move_drafts_to_root", "delete_drafts"}:
            raise ValueError(f"不支持的 on_non_empty 策略: {on_non_empty}")

        normalized_path = self.normalize_logical_folder_path(logical_folder_path)
        folder_meta, mappings_meta = self.load_local_draft_folder_configs()
        folders = folder_meta["folders"]
        mappings = mappings_meta["mappings"]
        folders_by_parent_name, children_by_parent, _ = self.build_folder_indexes(folders, mappings)
        target_folder = self.resolve_folder_by_parts(normalized_path.split("/"), folders_by_parent_name)
        subtree_folder_ids = self.collect_subtree_folder_ids(str(target_folder["id"]), children_by_parent)
        subtree_mappings = [mapping for mapping in mappings if mapping["folderId"] in subtree_folder_ids]
        affected_draft_ids = list(dict.fromkeys(str(mapping["draftId"]) for mapping in subtree_mappings))
        subtree_is_non_empty = len(subtree_folder_ids) > 1 or bool(subtree_mappings)

        if on_non_empty == "block" and subtree_is_non_empty:
            raise OSError(f"草稿文件夹 {normalized_path} 非空，无法删除")

        if on_non_empty == "move_drafts_to_root" and subtree_mappings:
            self.resolve_draft_names_in_current_root(affected_draft_ids)
        if on_non_empty == "delete_drafts" and subtree_mappings:
            draft_names_by_id = self.resolve_draft_names_in_current_root(affected_draft_ids)
            missing_draft_names = [
                draft_name
                for draft_name in draft_names_by_id.values()
                if not os.path.isdir(os.path.join(self.draft_root_path, draft_name))
            ]
            if missing_draft_names:
                raise FileNotFoundError(
                    "删除逻辑文件夹前发现缺失的草稿目录: " + ", ".join(sorted(missing_draft_names))
                )
            for draft_name in draft_names_by_id.values():
                self.remove_draft(draft_name)

        now = self.current_timestamp()
        folder_meta["folders"] = [folder for folder in folders if folder["id"] not in subtree_folder_ids]
        if on_non_empty in {"move_drafts_to_root", "delete_drafts"} and affected_draft_ids:
            mappings_meta["mappings"] = [
                mapping for mapping in mappings if str(mapping["draftId"]) not in affected_draft_ids
            ]
        else:
            mappings_meta["mappings"] = [
                mapping for mapping in mappings if mapping["folderId"] not in subtree_folder_ids
            ]
        self.write_local_draft_folder_config(self.folder_meta_info_path(), folder_meta, timestamp=now)
        self.write_local_draft_folder_config(self.draft_folder_mappings_path(), mappings_meta, timestamp=now)
        if on_non_empty == "delete_drafts" and affected_draft_ids:
            self.remove_root_meta_entries(set(affected_draft_ids))

    def _refresh_project_id_before_save(self, script_file: ScriptFile) -> None:
        context = getattr(script_file, "_draft_registration_context", None)
        if not isinstance(context, dict):
            return

        draft_path = str(context.get("draft_path") or os.path.dirname(script_file.save_path or ""))
        draft_id = self.coerce_str(context.get("draft_id"))
        if context.get("refresh_project_id_on_first_save", False):
            draft_id = self.new_uuid()
            context["allow_generate_draft_id"] = False
            context["refresh_project_id_on_first_save"] = False
        elif not draft_id:
            draft_id = self.coerce_str(script_file.content.get("id"))
            if not draft_id:
                draft_id = self.find_existing_draft_id_for_path(draft_path)
            if not draft_id:
                draft_id = self.new_uuid()
                context["allow_generate_draft_id"] = False
                context["pending_first_registration"] = True

        script_file.content["id"] = draft_id
        context["draft_id"] = draft_id
        self.ensure_draft_id_is_available_for_path(draft_id, draft_path)

    def _register_after_save(self, script_file: ScriptFile) -> None:
        """Synchronize sidecar metadata once after the timeline is safely written."""
        context = getattr(script_file, "_draft_registration_context", None)
        if not isinstance(context, dict) or script_file.save_path is None:
            return

        draft_path = str(context.get("draft_path") or os.path.dirname(script_file.save_path))
        draft_name = str(context.get("draft_name") or os.path.basename(draft_path))
        draft_meta_path = os.path.join(draft_path, "draft_meta_info.json")
        if os.path.exists(draft_meta_path):
            meta_info, sidecar_codec = self.read_draft_meta_info(draft_meta_path)
        else:
            meta_info = self.new_draft_meta_info()
            sidecar_codec = None
        draft_id = self.resolve_registration_draft_id(script_file, context, meta_info, draft_path)
        self.ensure_draft_id_is_available_for_path(draft_id, draft_path)
        draft_new_version = self.resolve_draft_new_version(meta_info, draft_path, draft_id)
        self.sync_draft_meta_info(
            draft_meta_path,
            meta_info=meta_info,
            sidecar_codec=sidecar_codec,
            script_file=script_file,
            context=context,
            draft_name=draft_name,
            draft_path=draft_path,
            draft_id=draft_id,
            draft_new_version=draft_new_version,
        )

        if not self.has_available_user_data_path():
            return

        self.upsert_root_registration(
            script_file,
            context=context,
            draft_name=draft_name,
            draft_path=draft_path,
            meta_info=meta_info,
            draft_id=draft_id,
            draft_new_version=draft_new_version,
        )
        context["pending_first_registration"] = False

    @staticmethod
    def new_draft_meta_info() -> Dict[str, Any]:
        with open(assets.get_asset_path("DRAFT_META_TEMPLATE"), "r", encoding="utf-8") as file_obj:
            meta_info = json.load(file_obj)
        if not isinstance(meta_info, dict):
            raise ValueError("DRAFT_META_TEMPLATE 顶层不是对象")
        return meta_info

    def read_draft_meta_info(self, meta_path: str) -> Tuple[Dict[str, Any], Optional[DraftContentCodec]]:
        meta_info, used_codec = load_json_object_with_codec(meta_path, content_codec=self.content_codec)
        return meta_info, self.content_codec if used_codec else None

    def find_existing_draft_id_for_path(self, draft_path: str) -> str:
        meta_path = os.path.join(draft_path, "draft_meta_info.json")
        if os.path.exists(meta_path):
            meta_info, _ = self.read_draft_meta_info(meta_path)
            draft_id = self.coerce_str(meta_info.get("draft_id"))
            if draft_id:
                return draft_id
        if self.has_available_user_data_path():
            root_entries = self.read_root_meta_payload()["all_draft_store"]
            existing_entry = self.find_root_meta_entry(root_entries, draft_path)
            if existing_entry is not None:
                return self.coerce_str(existing_entry.get("draft_id"))
        return ""

    def ensure_draft_id_is_available_for_path(self, draft_id: str, draft_path: str) -> None:
        if not draft_id or not self.has_available_user_data_path():
            return

        root_entries = self.read_root_meta_payload()["all_draft_store"]
        registered_entry = self.find_root_meta_entry_by_draft_id(root_entries, draft_id)
        if registered_entry is None:
            return

        registered_path = self.coerce_str(registered_entry.get("draft_fold_path"))
        if registered_path and (
            self.normalize_filesystem_path(registered_path)
            != self.normalize_filesystem_path(draft_path)
        ):
            raise ValueError(
                f"draft_id {draft_id} 已注册到其他草稿目录，不能将其重新绑定到 {draft_path}"
            )

    def resolve_draft_new_version(
        self,
        meta_info: Dict[str, Any],
        draft_path: str,
        draft_id: str,
    ) -> str:
        draft_new_version = self.coerce_str(meta_info.get("draft_new_version"))
        if draft_new_version or not self.has_available_user_data_path():
            return draft_new_version

        root_entries = self.read_root_meta_payload()["all_draft_store"]
        existing_entry = self.find_root_meta_entry(root_entries, draft_path, draft_id)
        if existing_entry is None:
            return ""
        return self.coerce_str(existing_entry.get("draft_new_version"))

    def resolve_registration_draft_id(
        self,
        script_file: ScriptFile,
        context: Dict[str, Any],
        meta_info: Dict[str, Any],
        draft_path: str,
    ) -> str:
        draft_id = self.coerce_str(context.get("draft_id"))
        if not draft_id:
            draft_id = self.coerce_str(script_file.content.get("id"))
        if not draft_id:
            draft_id = self.coerce_str(meta_info.get("draft_id"))

        if not draft_id and self.has_available_user_data_path():
            root_entries = self.read_root_meta_payload()["all_draft_store"]
            existing_entry = self.find_root_meta_entry(root_entries, draft_path)
            if existing_entry is not None:
                draft_id = self.coerce_str(existing_entry.get("draft_id"))

        if not draft_id and context.get("allow_generate_draft_id", False):
            draft_id = self.new_uuid()
            script_file.content["id"] = draft_id
            context["allow_generate_draft_id"] = False

        if draft_id:
            context["draft_id"] = draft_id
        return draft_id

    def sync_draft_meta_info(
        self,
        meta_path: str,
        *,
        meta_info: Dict[str, Any],
        sidecar_codec: Optional[DraftContentCodec],
        script_file: ScriptFile,
        context: Dict[str, Any],
        draft_name: str,
        draft_path: str,
        draft_id: str,
        draft_new_version: str,
    ) -> None:
        self.sync_draft_materials(meta_info, script_file)

        if draft_id:
            now_us = self.current_timestamp_us()
            if context.get("pending_first_registration", False):
                tm_draft_create = self.coerce_int(context.get("tm_draft_create"), default=now_us)
            else:
                tm_draft_create = self.coerce_int(
                    context.get("tm_draft_create"),
                    meta_info.get("tm_draft_create"),
                    default=now_us,
                )
            context["tm_draft_create"] = tm_draft_create
            meta_info["draft_id"] = draft_id
            meta_info["draft_name"] = draft_name
            meta_info["draft_root_path"] = self.draft_root_path
            meta_info["draft_fold_path"] = draft_path
            meta_info["draft_cover"] = "draft_cover.jpg"
            meta_info["tm_draft_create"] = tm_draft_create
            meta_info["tm_draft_modified"] = now_us
            meta_info["tm_duration"] = int(script_file.duration or 0)
            meta_info["draft_timeline_materials_size_"] = self.calculate_timeline_materials_size(script_file)
            meta_info["draft_new_version"] = draft_new_version

        write_json_object_with_codec(
            meta_path,
            meta_info,
            content_codec=sidecar_codec,
            trailing_newline=True,
        )

    def sync_draft_materials(self, meta_info: Dict[str, Any], script_file: ScriptFile) -> None:
        draft_materials = meta_info.get("draft_materials")
        if not isinstance(draft_materials, list):
            draft_materials = []
            meta_info["draft_materials"] = draft_materials

        local_bucket = next(
            (item for item in draft_materials if isinstance(item, dict) and item.get("type") == 0),
            None,
        )
        if local_bucket is None:
            local_bucket = {"type": 0, "value": []}
            draft_materials.append(local_bucket)
        if not isinstance(local_bucket.get("value"), list):
            local_bucket["value"] = []

        values: List[Dict[str, Any]] = local_bucket["value"]
        existing_paths = {
            self.normalize_filesystem_path(value["file_Path"])
            for value in values
            if isinstance(value, dict) and isinstance(value.get("file_Path"), str) and value["file_Path"]
        }
        for media in self.iter_script_media_for_meta_info(script_file):
            media_path = media["path"]
            normalized_path = self.normalize_filesystem_path(media_path)
            if normalized_path in existing_paths:
                continue

            duration = int(media["duration"] or 0)
            values.append({
                "duration": duration,
                "height": int(media["height"] or 0),
                "md5": "",
                "metetype": media["metetype"],
                "type": 0,
                "width": int(media["width"] or 0),
                "create_time": 0,
                "extra_info": os.path.basename(media_path),
                "file_Path": media_path,
                "import_time": 0,
                "import_time_ms": 0,
                "item_source": 1,
                "roughcut_time_range": {"duration": duration, "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
            })
            existing_paths.add(normalized_path)

    def iter_script_media_for_meta_info(self, script_file: ScriptFile) -> List[Dict[str, Any]]:
        materials = script_file.content.get("materials", {})
        if not isinstance(materials, dict):
            return []

        media_items: List[Dict[str, Any]] = []
        for video in materials.get("videos", []) or []:
            if not isinstance(video, dict) or not isinstance(video.get("path"), str) or not video["path"]:
                continue
            media_items.append({
                "path": video["path"],
                "metetype": "photo" if video.get("type") == "photo" else "video",
                "duration": int(video.get("duration", 0) or 0),
                "width": int(video.get("width", 0) or 0),
                "height": int(video.get("height", 0) or 0),
            })
        for audio in materials.get("audios", []) or []:
            if not isinstance(audio, dict) or not isinstance(audio.get("path"), str) or not audio["path"]:
                continue
            media_items.append({
                "path": audio["path"],
                "metetype": "music",
                "duration": int(audio.get("duration", 0) or 0),
                "width": 0,
                "height": 0,
            })

        seen_paths: Set[str] = set()
        unique_media: List[Dict[str, Any]] = []
        for item in media_items:
            normalized_path = self.normalize_filesystem_path(item["path"])
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            unique_media.append(item)
        return unique_media

    def upsert_root_registration(
        self,
        script_file: ScriptFile,
        *,
        context: Dict[str, Any],
        draft_name: str,
        draft_path: str,
        meta_info: Dict[str, Any],
        draft_id: str,
        draft_new_version: str,
    ) -> None:
        if not draft_id:
            return

        self.ensure_draft_id_is_available_for_path(draft_id, draft_path)
        root_meta_payload = self.read_root_meta_payload()
        root_entries = root_meta_payload["all_draft_store"]
        existing_entry = self.find_root_meta_entry(root_entries, draft_path, draft_id)
        replaced_draft_ids = self.collect_root_meta_draft_ids_for_path(root_entries, draft_path)
        replaced_draft_ids.discard(draft_id)

        now_us = self.current_timestamp_us()
        is_first_registration = bool(context.get("pending_first_registration", False))
        if is_first_registration:
            tm_draft_create = self.coerce_int(context.get("tm_draft_create"), default=now_us)
        else:
            tm_draft_create = self.coerce_int(
                context.get("tm_draft_create"),
                meta_info.get("tm_draft_create"),
                existing_entry.get("tm_draft_create") if existing_entry else None,
                default=now_us,
            )
        context["tm_draft_create"] = tm_draft_create

        root_entry = self.build_root_meta_entry(
            existing_entry=existing_entry,
            draft_name=draft_name,
            draft_path=draft_path,
            draft_id=draft_id,
            draft_new_version=draft_new_version,
            tm_draft_create=tm_draft_create,
            tm_draft_modified=now_us,
            tm_duration=int(script_file.duration or 0),
            timeline_materials_size=self.calculate_timeline_materials_size(script_file),
        )
        self.upsert_root_meta_entry(root_entries, root_entry, draft_path, draft_id)
        self.write_root_meta_info(root_meta_payload)
        self.remove_stale_draft_folder_mappings(replaced_draft_ids)

    def read_root_meta_payload(self) -> Dict[str, Any]:
        root_meta_info_path = self.root_meta_info_path()
        if not os.path.exists(root_meta_info_path):
            return {"all_draft_store": []}
        payload = self.read_json_object(root_meta_info_path)
        all_draft_store = payload.setdefault("all_draft_store", [])
        if not isinstance(all_draft_store, list):
            raise ValueError(f"root_meta_info.json 中的 all_draft_store 不是数组: {root_meta_info_path}")
        return payload

    def build_root_meta_entry(
        self,
        *,
        existing_entry: Optional[Dict[str, Any]],
        draft_name: str,
        draft_path: str,
        draft_id: str,
        draft_new_version: str,
        tm_draft_create: int,
        tm_draft_modified: int,
        tm_duration: int,
        timeline_materials_size: int,
    ) -> Dict[str, Any]:
        managed_fields: Dict[str, Any] = {
            "draft_cover": os.path.join(draft_path, "draft_cover.jpg"),
            "draft_fold_path": draft_path,
            "draft_id": draft_id,
            "draft_json_file": os.path.join(draft_path, "draft_content.json"),
            "draft_name": draft_name,
            "draft_new_version": draft_new_version,
            "draft_root_path": self.draft_root_path,
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

        entry: Dict[str, Any] = {
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

    def find_root_meta_entry_by_draft_id(
        self,
        root_entries: List[Dict[str, Any]],
        draft_id: str,
    ) -> Optional[Dict[str, Any]]:
        for entry in root_entries:
            if isinstance(entry, dict) and self.coerce_str(entry.get("draft_id")) == draft_id:
                return entry
        return None

    def find_root_meta_entry(
        self,
        root_entries: List[Dict[str, Any]],
        draft_path: str,
        draft_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_draft_path = self.normalize_filesystem_path(draft_path)
        for entry in root_entries:
            if not isinstance(entry, dict):
                continue
            draft_fold_path = self.coerce_str(entry.get("draft_fold_path"))
            if draft_fold_path and self.normalize_filesystem_path(draft_fold_path) == normalized_draft_path:
                return entry

        if not draft_id:
            return None
        for entry in root_entries:
            if not isinstance(entry, dict) or self.coerce_str(entry.get("draft_id")) != draft_id:
                continue
            draft_fold_path = self.coerce_str(entry.get("draft_fold_path"))
            if not draft_fold_path or self.normalize_filesystem_path(draft_fold_path) == normalized_draft_path:
                return entry
        return None

    def collect_root_meta_draft_ids_for_path(
        self,
        root_entries: List[Dict[str, Any]],
        draft_path: str,
    ) -> Set[str]:
        normalized_draft_path = self.normalize_filesystem_path(draft_path)
        return {
            self.coerce_str(entry.get("draft_id"))
            for entry in root_entries
            if isinstance(entry, dict)
            and self.coerce_str(entry.get("draft_fold_path"))
            and self.normalize_filesystem_path(self.coerce_str(entry.get("draft_fold_path"))) == normalized_draft_path
            and self.coerce_str(entry.get("draft_id"))
        }

    def upsert_root_meta_entry(
        self,
        root_entries: List[Dict[str, Any]],
        root_entry: Dict[str, Any],
        draft_path: str,
        draft_id: str,
    ) -> None:
        normalized_draft_path = self.normalize_filesystem_path(draft_path)
        matched_indexes: List[int] = []
        for index, entry in enumerate(root_entries):
            if not isinstance(entry, dict):
                continue
            entry_draft_id = self.coerce_str(entry.get("draft_id"))
            entry_draft_path = self.coerce_str(entry.get("draft_fold_path"))
            has_same_path = (
                entry_draft_path
                and self.normalize_filesystem_path(entry_draft_path) == normalized_draft_path
            )
            has_unbound_same_id = entry_draft_id == draft_id and not entry_draft_path
            if has_same_path or has_unbound_same_id:
                matched_indexes.append(index)

        if not matched_indexes:
            root_entries.insert(0, root_entry)
            return

        root_entries[matched_indexes[0]] = root_entry
        for index in reversed(matched_indexes[1:]):
            del root_entries[index]

    def write_root_meta_info(self, root_meta_payload: Dict[str, Any]) -> None:
        self.write_compact_json_file(self.root_meta_info_path(), root_meta_payload)

    def remove_root_meta_entries(self, draft_ids: Set[str]) -> None:
        if not draft_ids or not self.has_available_user_data_path():
            return
        root_meta_info_path = self.root_meta_info_path()
        if not os.path.exists(root_meta_info_path):
            return

        root_meta_payload = self.read_root_meta_payload()
        root_entries = root_meta_payload["all_draft_store"]
        filtered_entries = [
            entry
            for entry in root_entries
            if not isinstance(entry, dict) or self.coerce_str(entry.get("draft_id")) not in draft_ids
        ]
        if len(filtered_entries) == len(root_entries):
            return
        root_meta_payload["all_draft_store"] = filtered_entries
        self.write_root_meta_info(root_meta_payload)

    def remove_stale_draft_folder_mappings(self, draft_ids: Set[str]) -> None:
        if not draft_ids:
            return
        mappings_path = self.draft_folder_mappings_path()
        if not os.path.exists(mappings_path):
            return

        mappings_meta = self.read_json_object(mappings_path)
        mappings = mappings_meta.get("mappings")
        if not isinstance(mappings, list):
            raise ValueError(f"draft_folder_mappings.json 中的 mappings 不是数组: {mappings_path}")
        remaining_mappings = [mapping for mapping in mappings if str(mapping.get("draftId", "")) not in draft_ids]
        if len(remaining_mappings) == len(mappings):
            return
        mappings_meta["mappings"] = remaining_mappings
        self.write_local_draft_folder_config(mappings_path, mappings_meta, timestamp=self.current_timestamp())

    def load_local_draft_folder_configs(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        self.ensure_local_draft_folder_configs()
        folder_meta = self.read_json_object(self.folder_meta_info_path())
        mappings_meta = self.read_json_object(self.draft_folder_mappings_path())
        folders = folder_meta.setdefault("folders", [])
        mappings = mappings_meta.setdefault("mappings", [])
        if not isinstance(folders, list):
            raise ValueError(f"folder_meta_info.json 中的 folders 不是数组: {self.folder_meta_info_path()}")
        if not isinstance(mappings, list):
            raise ValueError(f"draft_folder_mappings.json 中的 mappings 不是数组: {self.draft_folder_mappings_path()}")
        return folder_meta, mappings_meta

    def ensure_local_draft_folder_configs(self) -> None:
        os.makedirs(self.local_draft_folder_dir(), exist_ok=True)
        config_defaults = (
            (self.folder_meta_info_path(), self.new_folder_meta_info()),
            (self.draft_folder_mappings_path(), self.new_draft_folder_mappings()),
            (self.recycle_bin_path(), self.new_recycle_bin()),
            (self.draft_mapping_recycle_bin_path(), self.new_draft_mapping_recycle_bin()),
        )
        for path, default_value in config_defaults:
            if not os.path.exists(path):
                self.write_local_draft_folder_config(path, default_value)
            elif not os.path.exists(f"{path}.bak"):
                self.write_json_file(f"{path}.bak", self.read_json_object(path))

    def build_folder_indexes(
        self,
        folders: List[Dict[str, Any]],
        mappings: List[Dict[str, Any]],
    ) -> Tuple[
        Dict[Tuple[str, str], Dict[str, Any]],
        Dict[str, List[Dict[str, Any]]],
        Dict[str, List[Dict[str, Any]]],
    ]:
        folders_by_parent_name: Dict[Tuple[str, str], Dict[str, Any]] = {}
        children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        mappings_by_draft_id: Dict[str, List[Dict[str, Any]]] = {}
        for folder in folders:
            parent_id = str(folder.get("parentId", "") or "")
            name = str(folder["name"])
            folders_by_parent_name[(parent_id, name)] = folder
            children_by_parent.setdefault(parent_id, []).append(folder)
        for mapping in mappings:
            draft_id = str(mapping["draftId"])
            mappings_by_draft_id.setdefault(draft_id, []).append(mapping)
        return folders_by_parent_name, children_by_parent, mappings_by_draft_id

    def resolve_folder_by_parts(
        self,
        path_parts: List[str],
        folders_by_parent_name: Dict[Tuple[str, str], Dict[str, Any]],
    ) -> Dict[str, Any]:
        parent_id = ""
        current_folder: Optional[Dict[str, Any]] = None
        for path_part in path_parts:
            current_folder = folders_by_parent_name.get((parent_id, path_part))
            if current_folder is None:
                raise FileNotFoundError(f"草稿文件夹 {'/'.join(path_parts)} 不存在")
            parent_id = str(current_folder["id"])
        assert current_folder is not None
        return current_folder

    def collect_subtree_folder_ids(
        self,
        root_folder_id: str,
        children_by_parent: Dict[str, List[Dict[str, Any]]],
    ) -> Set[str]:
        subtree_folder_ids: Set[str] = set()
        pending_folder_ids = [root_folder_id]
        while pending_folder_ids:
            current_folder_id = pending_folder_ids.pop()
            if current_folder_id in subtree_folder_ids:
                continue
            subtree_folder_ids.add(current_folder_id)
            pending_folder_ids.extend(
                str(child["id"]) for child in children_by_parent.get(current_folder_id, [])
            )
        return subtree_folder_ids

    def resolve_draft_id(self, draft_name: str) -> str:
        expected_draft_path = self.normalize_filesystem_path(
            os.path.abspath(os.path.join(self.draft_root_path, draft_name))
        )
        for draft_info in self.load_root_meta_info():
            if draft_info.get("tm_draft_removed", 0) != 0:
                continue
            draft_fold_path = draft_info.get("draft_fold_path")
            draft_id = draft_info.get("draft_id")
            if not draft_fold_path or not draft_id:
                continue
            if self.normalize_filesystem_path(str(draft_fold_path)) == expected_draft_path:
                return str(draft_id)
        raise LookupError(f"草稿文件夹 {draft_name} 的 draft_id 未解析")

    def resolve_draft_names_in_current_root(self, draft_ids: List[str]) -> Dict[str, str]:
        unresolved_draft_ids = set(dict.fromkeys(draft_ids))
        draft_names_by_id: Dict[str, str] = {}
        current_root_path = self.normalize_filesystem_path(self.draft_root_path)
        for draft_info in self.load_root_meta_info():
            if draft_info.get("tm_draft_removed", 0) != 0:
                continue
            draft_id = draft_info.get("draft_id")
            draft_fold_path = draft_info.get("draft_fold_path")
            if not draft_id or not draft_fold_path or str(draft_id) not in unresolved_draft_ids:
                continue
            raw_draft_path = str(draft_fold_path)
            if self.normalize_filesystem_path(os.path.dirname(raw_draft_path)) != current_root_path:
                continue
            draft_id_text = str(draft_id)
            draft_names_by_id[draft_id_text] = os.path.basename(
                os.path.normpath(raw_draft_path.replace("\\", os.sep).replace("/", os.sep))
            )
            unresolved_draft_ids.remove(draft_id_text)
            if not unresolved_draft_ids:
                break
        if unresolved_draft_ids:
            raise LookupError(f"存在不属于当前根目录的草稿映射: {', '.join(sorted(unresolved_draft_ids))}")
        return draft_names_by_id

    def load_root_meta_info(self) -> List[Dict[str, Any]]:
        root_meta_info_path = self.root_meta_info_path()
        if not os.path.exists(root_meta_info_path):
            raise FileNotFoundError(f"root_meta_info.json 不存在: {root_meta_info_path}")
        all_draft_store = self.read_json_object(root_meta_info_path).get("all_draft_store", [])
        if not isinstance(all_draft_store, list):
            raise ValueError(f"root_meta_info.json 中的 all_draft_store 不是数组: {root_meta_info_path}")
        return all_draft_store

    def has_available_user_data_path(self) -> bool:
        return bool(self.user_data_path) or bool(os.environ.get("LOCALAPPDATA"))

    def get_user_data_path(self) -> str:
        if self.user_data_path:
            return self.user_data_path
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise EnvironmentError("未设置 LOCALAPPDATA，无法自动定位 JianyingPro User Data")
        return os.path.join(local_app_data, "JianyingPro", "User Data")

    def local_draft_folder_dir(self) -> str:
        return os.path.join(self.get_user_data_path(), "Config", "LocalDraftFolder")

    def root_meta_info_path(self) -> str:
        return os.path.join(self.get_user_data_path(), "Projects", "com.lveditor.draft", "root_meta_info.json")

    def folder_meta_info_path(self) -> str:
        return os.path.join(self.local_draft_folder_dir(), "folder_meta_info.json")

    def draft_folder_mappings_path(self) -> str:
        return os.path.join(self.local_draft_folder_dir(), "draft_folder_mappings.json")

    def recycle_bin_path(self) -> str:
        return os.path.join(self.local_draft_folder_dir(), "recycle_bin.json")

    def draft_mapping_recycle_bin_path(self) -> str:
        return os.path.join(self.local_draft_folder_dir(), "draft_mapping_recycle_bin.json")

    @staticmethod
    def normalize_logical_folder_path(logical_folder_path: str) -> str:
        normalized_path = logical_folder_path.replace("\\", "/").strip("/")
        if not normalized_path:
            raise ValueError("logical_folder_path 不能为空")
        path_parts = normalized_path.split("/")
        if any(part in {"", ".", ".."} for part in path_parts):
            raise ValueError(f"非法逻辑路径: {logical_folder_path}")
        return "/".join(path_parts)

    @staticmethod
    def normalize_filesystem_path(path: str) -> str:
        normalized_path = path.replace("\\", os.sep).replace("/", os.sep)
        return os.path.normcase(os.path.normpath(normalized_path))

    @staticmethod
    def current_timestamp() -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    @staticmethod
    def current_timestamp_us() -> int:
        return time.time_ns() // 1_000

    @staticmethod
    def new_uuid() -> str:
        return str(uuid.uuid4()).upper()

    @staticmethod
    def coerce_str(value: object) -> str:
        return str(value).strip() if value is not None and str(value).strip() else ""

    @staticmethod
    def coerce_int(*values: object, default: int = 0) -> int:
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return default

    def calculate_timeline_materials_size(self, script_file: ScriptFile) -> int:
        total_size = 0
        for media in self.iter_script_media_for_meta_info(script_file):
            try:
                total_size += os.path.getsize(media["path"])
            except OSError:
                continue
        return total_size

    def new_folder_meta_info(self) -> Dict[str, Any]:
        return {"folders": [], "timestamp": self.current_timestamp(), "version": "1.0"}

    def new_draft_folder_mappings(self) -> Dict[str, Any]:
        return {"mappings": [], "timestamp": self.current_timestamp(), "version": "1.0"}

    def new_recycle_bin(self) -> Dict[str, Any]:
        return {"recycled_folders": [], "timestamp": self.current_timestamp(), "version": "1.0"}

    def new_draft_mapping_recycle_bin(self) -> Dict[str, Any]:
        return {"recycled_draft_mappings": [], "timestamp": self.current_timestamp(), "version": "1.0"}

    @staticmethod
    def read_json_object(path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        if not isinstance(data, dict):
            raise ValueError(f"JSON 文件顶层不是对象: {path}")
        return data

    @staticmethod
    def serialize_json(data: Dict[str, Any]) -> str:
        return f"{json.dumps(data, ensure_ascii=False, indent=4)}\n"

    @staticmethod
    def serialize_compact_json(data: Dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def replace_text_file(path: str, content: str, *, newline: str = "\n") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8", newline=newline) as file_obj:
                file_obj.write(content)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def write_json_file(self, path: str, data: Dict[str, Any]) -> None:
        self.replace_text_file(path, self.serialize_json(data))

    def write_compact_json_file(self, path: str, data: Dict[str, Any]) -> None:
        self.replace_text_file(path, self.serialize_compact_json(data))

    def write_local_draft_folder_config(
        self,
        path: str,
        data: Dict[str, Any],
        *,
        timestamp: Optional[str] = None,
    ) -> None:
        payload = dict(data)
        if timestamp is not None:
            payload["timestamp"] = timestamp
        serialized = self.serialize_json(payload)
        self.replace_text_file(path, serialized)
        self.replace_text_file(f"{path}.bak", serialized)
