import threading
from typing import Optional, Set

import bpy
from bpy.types import Context, Event

from ..operations.progress_transport import ProgressServerTransport
from ..operations.publish_operation import (
    _build_source_data,
    add_render_material_proxies_to_base,
    build_collection_hierarchy,
    count_objects_in_collection,
    send_and_create_version,
)
from ..utils.account_manager import _client_cache, can_create_version


class SPECKLE_OT_publish_model_card(bpy.types.Operator):
    """Publish tracked objects to Speckle.

    Conversion runs on the main thread (it must touch ``bpy.data``); the upload
    and version-create run in a background thread so the UI stays responsive,
    while a modal timer polls the transport's object counter to drive an in-panel
    progress bar. Mirrors the connector's existing auth modal-timer pattern.
    """

    bl_idname = "speckle.model_card_publish"
    bl_label = "Publish model"
    bl_description = "Publish tracked objects to Speckle"

    model_card_id: bpy.props.StringProperty(name="Model Card ID", default="")  # type: ignore
    version_message: bpy.props.StringProperty(name="Version Message", default="")  # type: ignore

    def draw(self, context: Context) -> None:
        self.layout.prop(self, "version_message")

    def invoke(self, context: Context, event: Event) -> Set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: Context) -> Set[str]:
        wm = context.window_manager

        model_card = context.scene.speckle_state.get_model_card_by_id(
            self.model_card_id
        )

        authorized, auth_message = can_create_version(
            model_card.account_id, model_card.project_id, model_card.model_id
        )
        if not authorized:
            self.report({"ERROR"}, auth_message)
            return {"CANCELLED"}

        wm.selected_account_id = model_card.account_id
        wm.selected_project_id = model_card.project_id
        wm.selected_model_id = model_card.model_id

        objects_to_convert = []
        for speckle_obj in model_card.objects:
            blender_obj = bpy.data.objects.get(speckle_obj.name)
            if blender_obj:
                objects_to_convert.append(blender_obj)
            else:
                self.report(
                    {"WARNING"}, f"Object '{speckle_obj.name}' not found, skipping"
                )

        if not objects_to_convert:
            self.report({"ERROR"}, "No objects to publish")
            return {"CANCELLED"}

        client = _client_cache.get_client(model_card.account_id)
        if not client:
            self.report({"ERROR"}, "No Speckle client found")
            return {"CANCELLED"}

        # --- conversion: must run on the main thread (touches bpy.data) ---
        print(
            f"[Speckle] Publish: converting {len(objects_to_convert)} "
            "selected object(s)..."
        )
        try:
            root_collection = build_collection_hierarchy(
                context, objects_to_convert, model_card.apply_modifiers
            )
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"Conversion failed: {e}")
            return {"CANCELLED"}

        if not root_collection:
            self.report({"ERROR"}, "No objects could be converted to Speckle format")
            return {"CANCELLED"}

        add_render_material_proxies_to_base(root_collection, objects_to_convert)

        self._total = count_objects_in_collection(root_collection)
        # source_data reads bpy.data -> build it here on the main thread and hand
        # it to the worker (which runs off-thread).
        self._source_data = _build_source_data()
        # wm=None: save_object runs in the upload thread, so it must not touch
        # bpy; the modal timer reads transport.progress_count on the main thread.
        self._transport = ProgressServerTransport(
            stream_id=model_card.project_id,
            client=client,
            wm=None,
            total=self._total,
        )
        self._model_card = model_card
        self._version_id: Optional[str] = None
        self._error: Optional[str] = None
        self._done = False

        model_card.is_publishing = True
        model_card.publish_progress = 0.0
        model_card.publish_status = f"Uploading 0/{self._total}..."

        print(f"[Speckle] Serializing + uploading {self._total} objects to server...")
        self._thread = threading.Thread(
            target=self._upload_worker,
            args=(
                client,
                root_collection,
                model_card.project_id,
                model_card.model_id,
                self.version_message,
            ),
            daemon=True,
        )
        self._thread.start()

        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _upload_worker(
        self, client, root_collection, project_id, model_id, version_message
    ) -> None:
        try:
            self._version_id = send_and_create_version(
                client,
                root_collection,
                project_id,
                model_id,
                version_message,
                self._transport,
                self._source_data,
            )
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self._error = str(e)
        finally:
            self._done = True

    def _redraw(self, context: Context) -> None:
        if context.screen:
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

    def modal(self, context: Context, event: Event) -> Set[str]:
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        model_card = self._model_card
        if self._transport is not None and self._total:
            n = min(self._transport.progress_count, self._total)
            model_card.publish_progress = n / self._total
            model_card.publish_status = f"Uploading {n}/{self._total}..."
            self._redraw(context)

        if not self._done:
            return {"RUNNING_MODAL"}

        return self._finish(context)

    def _finish(self, context: Context) -> Set[str]:
        wm = context.window_manager
        if getattr(self, "_timer", None) is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None

        model_card = self._model_card
        model_card.is_publishing = False
        model_card.publish_progress = 0.0
        model_card.publish_status = ""

        wm.selected_account_id = ""
        wm.selected_project_id = ""
        wm.selected_model_id = ""
        self._redraw(context)

        if self._error:
            _client_cache.clear()
            self.report({"ERROR"}, f"Failed to publish: {self._error}")
            return {"CANCELLED"}

        print(f"[Speckle] ✓ Published version {self._version_id}")
        model_card.version_id = self._version_id
        model_card.is_publish = True
        self.report(
            {"INFO"},
            f"Successfully published {self._total} objects with hierarchy to Speckle",
        )
        return {"FINISHED"}
