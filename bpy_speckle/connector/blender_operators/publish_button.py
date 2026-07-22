from typing import Set

import bpy
from bpy.types import Context, Event

from ..utils.account_manager import can_create_version, get_server_url_by_account_id
from ..utils.model_card_utils import model_card_exists, update_model_card_objects
from ._modal_publish import ModalPublishMixin


class SPECKLE_OT_publish(ModalPublishMixin, bpy.types.Operator):
    bl_idname = "speckle.publish"
    bl_label = "Publish to Speckle"
    bl_description = "Publish selected objects to Speckle"

    version_message: bpy.props.StringProperty(name="Version Message")  # type: ignore
    apply_modifiers: bpy.props.BoolProperty(  # type: ignore
        name="Apply Modifiers",
        description="Apply all modifiers to objects before conversion",
        default=True,
    )

    def draw(self, context: Context) -> None:
        layout = self.layout
        layout.prop(self, "version_message")
        layout.prop(self, "apply_modifiers")

    def invoke(self, context: Context, event: Event) -> Set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: Context) -> Set[str]:
        wm = context.window_manager

        if not wm.speckle_objects:
            self.report(
                {"ERROR"},
                "No objects selected to publish. Please use 'Select Objects' first.",
            )
            return {"CANCELLED"}

        account_id = getattr(wm, "selected_account_id", "")
        project_id = getattr(wm, "selected_project_id", "")
        model_id = getattr(wm, "selected_model_id", "")

        if not account_id:
            self.report({"ERROR"}, "No account selected")
            return {"CANCELLED"}
        if not project_id:
            self.report({"ERROR"}, "No project selected")
            return {"CANCELLED"}
        if not model_id:
            self.report({"ERROR"}, "No model selected")
            return {"CANCELLED"}

        authorized, auth_message = can_create_version(account_id, project_id, model_id)
        if not authorized:
            self.report({"ERROR"}, auth_message)
            return {"CANCELLED"}

        objects_to_convert = []
        for speckle_obj in wm.speckle_objects:
            blender_obj = bpy.data.objects.get(speckle_obj.name)
            if blender_obj:
                objects_to_convert.append(blender_obj)
            else:
                self.report(
                    {"WARNING"}, f"Object '{speckle_obj.name}' not found, skipping"
                )

        if not objects_to_convert:
            self.report({"ERROR"}, "None of the selected objects could be found")
            return {"CANCELLED"}

        # Create (or reuse) the model card up-front so it can host the in-panel
        # progress bar while the upload streams. Version id is set on completion.
        state = context.scene.speckle_state
        self._created_new_card = False
        if model_card_exists(project_id, model_id, True, context):
            model_card = state.get_model_card_by_id(
                f"{wm.ui_mode}-{project_id}-{model_id}"
            )
        else:
            model_card = state.model_cards.add()
            self._created_new_card = True

        model_card.account_id = account_id
        model_card.server_url = get_server_url_by_account_id(account_id)
        model_card.project_id = project_id
        model_card.project_name = getattr(wm, "selected_project_name", "")
        model_card.model_id = model_id
        model_card.model_name = getattr(wm, "selected_model_name", "")
        model_card.is_publish = True
        model_card.load_option = "SPECIFIC"  # published versions are specific
        model_card.apply_modifiers = self.apply_modifiers
        update_model_card_objects(model_card, objects_to_convert)

        return self._run_modal_publish(
            context,
            model_card,
            objects_to_convert,
            self.apply_modifiers,
            self.version_message,
        )

    def _clear_wm(self, context: Context) -> None:
        wm = context.window_manager
        wm.selected_account_id = ""
        wm.selected_project_id = ""
        wm.selected_project_name = ""
        wm.selected_model_id = ""
        wm.selected_model_name = ""
        wm.selected_version_load_option = ""
        wm.selected_version_id = ""
        wm.speckle_objects.clear()

    def _on_publish_failed(self, context: Context, model_card) -> None:
        # roll back a card we created just for this (failed) publish
        if not getattr(self, "_created_new_card", False) or model_card.version_id:
            return
        cards = context.scene.speckle_state.model_cards
        try:
            target = model_card.get_model_card_id()
        except Exception:  # noqa: BLE001
            return
        for i, card in enumerate(cards):
            try:
                if card.get_model_card_id() == target:
                    cards.remove(i)
                    return
            except Exception:  # noqa: BLE001
                continue
