bl_info = {
    'name': 'Project Paint Toggle',
    'author': 'Todd McIntosh, Diego Quevedo, Zafio',
    'version': (1, 5),
    'blender': (4, 5, 0),
    'location': 'Q / W keys in Texture Paint mode',
    'warning': '',
    'description': 'Q toggles Occlude, Backface Culling and Normal falloff together; '
                   'W toggles Bleed between 0 and its previous value. The brush cursor color shows the state',
    'wiki_url': '',
    'tracker_url': '',
    'category': 'Paint'}

import bpy

# Cursor colors (checked in this order):
#   green = paint through (Occlude, Culling and Normal all off)
#   red   = bleed is 0
#   blue  = all three on and bleed on
#   white = anything else (mixed checkboxes)
CURSOR_THROUGH = (0.0, 1.0, 0.0)
CURSOR_NO_BLEED = (1.0, 0.0, 0.0)
CURSOR_FULL = (0.1, 0.1, 1.0)
CURSOR_NORMAL = (1.0, 1.0, 1.0)

# Scene custom property that remembers the bleed value while it is set to 0
SAVED_BLEED_KEY = "project_paint_toggle_saved_bleed"
DEFAULT_BLEED = 2  # Blender's default, used if there is nothing to restore


def checkers_state(paint):
    """Return 'on', 'off' or 'mixed' for Occlude / Backface Culling / Normal."""
    flags = (paint.use_occlude, paint.use_backface_culling, paint.use_normal_falloff)
    if all(flags):
        return 'on'
    if not any(flags):
        return 'off'
    return 'mixed'


def update_cursor_color(context):
    paint = context.tool_settings.image_paint
    checkers = checkers_state(paint)
    if checkers == 'off':
        rgb = CURSOR_THROUGH
    elif paint.seam_bleed == 0:
        rgb = CURSOR_NO_BLEED
    elif checkers == 'on':
        rgb = CURSOR_FULL
    else:
        rgb = CURSOR_NORMAL

    # Brushes are assets since Blender 4.3, so several brushes can share a name.
    # Use the active brush directly instead of looking it up by name in bpy.data.
    brush = paint.brush
    if brush is None:
        return
    try:
        # cursor_color_add is RGBA in current Blender: keep the brush's alpha
        alpha = brush.cursor_color_add[3] if len(brush.cursor_color_add) > 3 else None
        brush.cursor_color_add = (*rgb, alpha) if alpha is not None else rgb
    except Exception as exc:
        print("Project Paint Toggle: could not set cursor color:", exc)


def toggle_project_paint(context):
    """Flip Occlude, Backface Culling and Normal falloff as a group.

    Returns True when painting through (all three off), False otherwise.
    """
    paint = context.tool_settings.image_paint
    state = checkers_state(paint) != 'on'
    paint.use_occlude = state
    paint.use_backface_culling = state
    paint.use_normal_falloff = state
    update_cursor_color(context)
    return not state


def toggle_bleed(context):
    """Set bleed to 0, or restore the value it had before. Returns the new bleed."""
    paint = context.tool_settings.image_paint
    scene = context.scene
    if paint.seam_bleed != 0:
        scene[SAVED_BLEED_KEY] = paint.seam_bleed
        paint.seam_bleed = 0
    else:
        paint.seam_bleed = int(scene.get(SAVED_BLEED_KEY, DEFAULT_BLEED)) or DEFAULT_BLEED
    update_cursor_color(context)
    return paint.seam_bleed


def texture_paint_in_3d_view(context):
    return (context.mode == 'PAINT_TEXTURE'
            and context.area is not None
            and context.area.type == 'VIEW_3D')


class PAINT_OT_toggle_project_paint(bpy.types.Operator):
    """Toggle Occlude, Backface Culling and Normal falloff together"""
    bl_idname = "object.toggle_checkboxes"
    bl_label = "Toggle Project Paint Checkboxes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return texture_paint_in_3d_view(context)

    def execute(self, context):
        through = toggle_project_paint(context)
        self.report({'INFO'}, "Paint through: ON" if through else "Paint through: OFF")
        return {'FINISHED'}


class PAINT_OT_toggle_bleed(bpy.types.Operator):
    """Toggle Bleed between 0 and its previous value"""
    bl_idname = "paint.toggle_project_bleed"
    bl_label = "Toggle Project Paint Bleed"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return texture_paint_in_3d_view(context)

    def execute(self, context):
        bleed = toggle_bleed(context)
        self.report({'INFO'}, f"Bleed: {bleed} px" if bleed else "Bleed: OFF")
        return {'FINISHED'}


classes = (PAINT_OT_toggle_project_paint, PAINT_OT_toggle_bleed)
addon_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Register the hotkeys in the add-on keyconfig (not the default one), so they
    # can be removed cleanly and show up under the add-on in Preferences > Keymap.
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Image Paint', space_type='EMPTY')
        for idname, key in ((PAINT_OT_toggle_project_paint.bl_idname, 'Q'),
                            (PAINT_OT_toggle_bleed.bl_idname, 'W')):
            kmi = km.keymap_items.new(idname, key, 'PRESS')
            addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
