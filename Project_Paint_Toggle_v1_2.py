bl_info = {
    'name': 'Project Paint Toggle',
    'author': 'Todd McIntosh, Diego Quevedo, Zafio',
    'version': (1, 3),
    'blender': (4, 5, 0),
    'location': 'Q key in Texture Paint mode',
    'warning': '',
    'description': 'Toggles Occlude, Backface Culling and Normal falloff together, and changes the brush cursor color',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Paint'}

import bpy

# Cursor colors: green = "paint through" (all three off), white = normal projection painting
CURSOR_THROUGH = (0.0, 1.0, 0.0)
CURSOR_NORMAL = (1.0, 1.0, 1.0)


def toggle_project_paint(context):
    """Flip Occlude, Backface Culling and Normal falloff as a group.

    Returns True when painting through (all three off), False otherwise.
    """
    paint = context.tool_settings.image_paint
    all_on = paint.use_occlude and paint.use_backface_culling and paint.use_normal_falloff
    state = not all_on

    paint.use_occlude = state
    paint.use_backface_culling = state
    paint.use_normal_falloff = state

    # Brushes are assets since Blender 4.3, so several brushes can share a name.
    # Use the active brush directly instead of looking it up by name in bpy.data.
    brush = paint.brush
    if brush is not None:
        try:
            brush.cursor_color_add = CURSOR_NORMAL if state else CURSOR_THROUGH
        except Exception as exc:
            print("Project Paint Toggle: could not set cursor color:", exc)

    return not state


class PAINT_OT_toggle_project_paint(bpy.types.Operator):
    """Toggle Occlude, Backface Culling and Normal falloff together"""
    bl_idname = "object.toggle_checkboxes"
    bl_label = "Toggle Project Paint Checkboxes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'PAINT_TEXTURE'
                and context.area is not None
                and context.area.type == 'VIEW_3D')

    def execute(self, context):
        through = toggle_project_paint(context)
        self.report({'INFO'}, "Paint through: ON" if through else "Paint through: OFF")
        return {'FINISHED'}


addon_keymaps = []


def register():
    bpy.utils.register_class(PAINT_OT_toggle_project_paint)

    # Register the hotkey in the add-on keyconfig (not the default one), so it
    # can be removed cleanly and shows up under the add-on in Preferences > Keymap.
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Image Paint', space_type='EMPTY')
        kmi = km.keymap_items.new(PAINT_OT_toggle_project_paint.bl_idname, 'Q', 'PRESS')
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.utils.unregister_class(PAINT_OT_toggle_project_paint)


if __name__ == "__main__":
    register()
