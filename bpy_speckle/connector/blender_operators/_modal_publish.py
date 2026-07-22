"""Shared modal-publish engine.

Both publish entry points (``speckle.publish`` and ``speckle.model_card_publish``)
mix this in so there is a single, correctly-threaded implementation.

Threading model (identical to the connector's auth flow):
- conversion runs on the main thread (it touches ``bpy.data``);
- the upload + version-create run on a background thread via the bpy-free
  ``send_and_create_version`` worker, so the UI stays responsive;
- a modal timer polls ``ProgressServerTransport.progress_count`` on the main
  thread and drives the model card's in-panel progress bar.

Each operator does its own validation/object-gathering in ``execute`` and then
calls ``_run_modal_publish`` with a progress-host model card. Subclasses may
override the ``_clear_wm`` / ``_on_publish_succeeded`` / ``_on_publish_failed``
hooks.
"""

import threading
import traceback
from typing import List, Optional, Set

from bpy.types import Context, Event

from ..operations.progress_transport import ProgressServerTransport
from ..operations.publish_operation import (
    _build_source_data,
    add_render_material_proxies_to_base,
    build_collection_hierarchy,
    count_objects_in_collection,
    send_and_create_version,
)
from ..utils.account_manager import _client_cache


class ModalPublishMixin:
    def _run_modal_publish(
        self,
        context: Context,
        model_card,
        objects_to_convert: List,
        apply_modifiers: bool,
        version_message: str,
    ) -> Set[str]:
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
                context, objects_to_convert, apply_modifiers
            )
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"Conversion failed: {e}")
            return {"CANCELLED"}

        if not root_collection:
            self.report({"ERROR"}, "No objects could be converted to Speckle format")
            return {"CANCELLED"}

        add_render_material_proxies_to_base(root_collection, objects_to_convert)

        self._mc = model_card
        self._total = count_objects_in_collection(root_collection)
        # source_data reads bpy.data -> build it on the main thread for the worker.
        self._source_data = _build_source_data()
        # wm=None: save_object runs in the upload thread and must not touch bpy;
        # the main-thread timer reads transport.progress_count instead.
        self._transport = ProgressServerTransport(
            stream_id=model_card.project_id, client=client, wm=None, total=self._total
        )
        self._version_id: Optional[str] = None
        self._error: Optional[str] = None
        self._done = False

        model_card.is_publishing = True
        model_card.publish_status = "Uploading 0 objects..."

        print(f"[Speckle] Serializing + uploading {self._total} objects to server...")
        self._thread = threading.Thread(
            target=self._upload_worker,
            args=(
                client,
                root_collection,
                model_card.project_id,
                model_card.model_id,
                version_message,
            ),
            daemon=True,
        )
        self._thread.start()

        wm = context.window_manager
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

        mc = self._mc
        if self._transport is not None:
            # The exact object count is unknowable up-front (meshes are chunked
            # into a variable number of detached objects during serialization),
            # so report the live uploaded count rather than a misleading %.
            n = self._transport.progress_count
            mc.publish_status = f"Uploading {n:,} objects..."
            self._redraw(context)

        if not self._done:
            return {"RUNNING_MODAL"}

        return self._finish_publish(context)

    def _finish_publish(self, context: Context) -> Set[str]:
        wm = context.window_manager
        if getattr(self, "_timer", None) is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None

        mc = self._mc
        mc.is_publishing = False
        mc.publish_status = ""
        self._clear_wm(context)
        self._redraw(context)

        if self._error:
            _client_cache.clear()
            self._on_publish_failed(context, mc)
            self.report({"ERROR"}, f"Failed to publish: {self._error}")
            return {"CANCELLED"}

        print(f"[Speckle] ✓ Published version {self._version_id}")
        mc.version_id = self._version_id
        mc.is_publish = True
        self._on_publish_succeeded(context, mc, self._version_id)
        self.report(
            {"INFO"},
            f"Successfully published {self._total} objects with hierarchy to Speckle",
        )
        return {"FINISHED"}

    # --- hooks (optional) ---
    def _clear_wm(self, context: Context) -> None:
        pass

    def _on_publish_succeeded(self, context: Context, model_card, version_id: str) -> None:
        pass

    def _on_publish_failed(self, context: Context, model_card) -> None:
        pass
