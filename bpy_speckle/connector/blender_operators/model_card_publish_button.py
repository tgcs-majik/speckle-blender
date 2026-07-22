from typing import Set

import bpy
from bpy.types import Context, Event

from ..utils.account_manager import can_create_version
from ._modal_publish import ModalPublishMixin


class SPECKLE_OT_publish_model_card(ModalPublishMixin, bpy.types.Operator):
    """Re-publish an existing model card's tracked objects (modal + threaded)."""

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

        return self._run_modal_publish(
            context,
            model_card,
            objects_to_convert,
            model_card.apply_modifiers,
            self.version_message,
        )

    def _clear_wm(self, context: Context) -> None:
        wm = context.window_manager
        wm.selected_account_id = ""
        wm.selected_project_id = ""
        wm.selected_model_id = ""
