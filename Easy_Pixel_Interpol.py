bl_info = {
    "name": "Easy Pixel Interpol",
    "author": "Zafio",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "Image Editor header, Texture Slots panel, Ctrl+Alt+L (Image Editor / Texture Paint / Shader Editor)",
    "description": "New Image Texture nodes default to Closest, plus a quick interpolation switch "
                   "for the active image without opening the Shader Editor",
    "category": "Paint",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty

# Custom property stamped on every Image Texture node we've already seen,
# so only genuinely new nodes get the default (survives undo and saving).
MARK = "_pixint_seen"

INTERP_ITEMS = [
    ('Closest', "Closest", "No filtering, crisp pixels"),
    ('Linear', "Linear", "Linear filtering (Blender's default)"),
    ('Cubic', "Cubic", "Cubic filtering"),
    ('Smart', "Smart", "Bicubic when magnifying, otherwise linear"),
]

INTERP_ICONS = {
    'Closest': 'ALIASED',
    'Linear': 'ANTIALIASED',
    'Cubic': 'ANTIALIASED',
    'Smart': 'ANTIALIASED',
}


# ---------------------------------------------------------------- helpers

def _prefs():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def _iter_image_nodes():
    """All Image Texture nodes in editable (non-linked) materials and shader node groups."""
    for mat in bpy.data.materials:
        if mat.library or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                yield node
    for ng in bpy.data.node_groups:
        if ng.library or ng.bl_idname != 'ShaderNodeTree':
            continue
        for node in ng.nodes:
            if node.type == 'TEX_IMAGE':
                yield node


def _nodes_using(image):
    if image is None:
        return []
    return [n for n in _iter_image_nodes() if n.image == image]


def _target_image(context):
    """Image Editor: the displayed image. 3D View: the active texture paint image."""
    space = context.space_data
    if space and space.type == 'IMAGE_EDITOR':
        return space.image
    if space and space.type == 'NODE_EDITOR':
        node = context.active_node
        return node.image if node and node.type == 'TEX_IMAGE' else None

    ip = context.scene.tool_settings.image_paint
    if ip.mode == 'IMAGE':
        return ip.canvas

    ob = context.active_object
    mat = ob.active_material if ob else None
    if mat and mat.texture_paint_images:
        idx = mat.paint_active_slot
        if 0 <= idx < len(mat.texture_paint_images):
            return mat.texture_paint_images[idx]
    return None


# ------------------------------------------------- auto-apply on new nodes

_pending = False


def _process_new_nodes():
    global _pending
    _pending = False
    prefs = _prefs()
    apply = prefs.auto_apply if prefs else False
    default = prefs.default_interpolation if prefs else 'Closest'
    for node in _iter_image_nodes():
        if node.get(MARK):
            continue
        if apply:
            node.interpolation = default
        node[MARK] = True
    return None  # one-shot timer


def _mark_all_existing():
    """Stamp nodes already in the file so opening old files changes nothing."""
    for node in _iter_image_nodes():
        if not node.get(MARK):
            node[MARK] = True
    return None


@persistent
def _on_depsgraph_update(scene, depsgraph):
    global _pending
    if _pending:
        return
    for upd in depsgraph.updates:
        if isinstance(upd.id, (bpy.types.Material, bpy.types.NodeTree)):
            _pending = True
            # Defer: editing data from inside a depsgraph handler is unsafe.
            bpy.app.timers.register(_process_new_nodes, first_interval=0.0)
            return


@persistent
def _on_load_post(*_args):
    _mark_all_existing()


# ---------------------------------------------------------------- operator

class PIXINT_OT_set_interpolation(bpy.types.Operator):
    """Set the interpolation of every Image Texture node that uses this image"""
    bl_idname = "image.pixint_set_interpolation"
    bl_label = "Set Image Interpolation"
    bl_options = {'REGISTER', 'UNDO'}

    interpolation: EnumProperty(name="Interpolation", items=INTERP_ITEMS, default='Closest')

    @classmethod
    def poll(cls, context):
        return _target_image(context) is not None

    def execute(self, context):
        image = _target_image(context)
        nodes = _nodes_using(image)
        if not nodes:
            self.report({'WARNING'}, f"'{image.name}' isn't used by any Image Texture node yet")
            return {'CANCELLED'}
        for node in nodes:
            node.interpolation = self.interpolation
            node[MARK] = True
        self.report({'INFO'}, f"{image.name}: {self.interpolation} on {len(nodes)} node(s)")
        return {'FINISHED'}


class PIXINT_OT_toggle_interpolation(bpy.types.Operator):
    """Toggle the active image between Closest and Linear (Ctrl+Alt+L)"""
    bl_idname = "image.pixint_toggle_interpolation"
    bl_label = "Toggle Closest / Linear"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _target_image(context) is not None

    def execute(self, context):
        image = _target_image(context)
        nodes = _nodes_using(image)
        if not nodes:
            self.report({'WARNING'}, f"'{image.name}' isn't used by any Image Texture node yet")
            return {'CANCELLED'}
        all_closest = all(n.interpolation == 'Closest' for n in nodes)
        new = 'Linear' if all_closest else 'Closest'
        for node in nodes:
            node.interpolation = new
            node[MARK] = True
        self.report({'INFO'}, f"{image.name}: {new}")
        # Header/panel dropdowns show the state; make sure they refresh.
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


# ---------------------------------------------------------------------- UI

def _draw_dropdown(layout, context):
    image = _target_image(context)
    if image is None:
        return
    nodes = _nodes_using(image)
    if nodes:
        modes = {n.interpolation for n in nodes}
        current = modes.pop() if len(modes) == 1 else None
        text = current or "Mixed"
        icon = INTERP_ICONS.get(current, 'ANTIALIASED')
    else:
        text, icon = "No Node", 'ERROR'
    layout.operator_menu_enum(PIXINT_OT_set_interpolation.bl_idname, "interpolation",
                              text=text, icon=icon)


def _draw_image_header(self, context):
    if context.space_data.image is None:
        return
    row = self.layout.row(align=True)
    _draw_dropdown(row, context)


def _draw_slots_panel(self, context):
    col = self.layout.column()
    col.separator()
    row = col.row(align=True)
    row.label(text="Interpolation")
    _draw_dropdown(row, context)


class PIXINT_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    auto_apply: BoolProperty(
        name="Apply to New Image Texture Nodes",
        description="Set the default interpolation on every Image Texture node created from now on "
                    "(Shader Editor, texture paint slots, appended materials)",
        default=True,
    )
    default_interpolation: EnumProperty(
        name="Default Interpolation", items=INTERP_ITEMS, default='Closest',
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "auto_apply")
        sub = col.row()
        sub.active = self.auto_apply
        sub.prop(self, "default_interpolation")


# ---------------------------------------------------------------- register

classes = (PIXINT_Preferences, PIXINT_OT_set_interpolation, PIXINT_OT_toggle_interpolation)

# (keymap name, space type) — Image Editor, Texture Paint in the 3D View,
# and the Shader Editor (acts on the active Image Texture node).
KEYMAPS = (
    ("Image", 'IMAGE_EDITOR'),
    ("Image Paint", 'EMPTY'),
    ("Node Editor", 'NODE_EDITOR'),
)
_addon_keymaps = []


def _register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    for name, space in KEYMAPS:
        km = kc.keymaps.new(name=name, space_type=space)
        kmi = km.keymap_items.new(PIXINT_OT_toggle_interpolation.bl_idname,
                                  'L', 'PRESS', ctrl=True, alt=True)
        _addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    _register_keymaps()
    bpy.types.IMAGE_HT_header.append(_draw_image_header)
    slots = getattr(bpy.types, "VIEW3D_PT_slots_projectpaint", None)
    if slots:
        slots.append(_draw_slots_panel)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bpy.app.handlers.load_post.append(_on_load_post)
    # bpy.data is restricted during register; mark the current file's nodes right after.
    bpy.app.timers.register(_mark_all_existing, first_interval=0.0)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    slots = getattr(bpy.types, "VIEW3D_PT_slots_projectpaint", None)
    if slots:
        slots.remove(_draw_slots_panel)
    bpy.types.IMAGE_HT_header.remove(_draw_image_header)
    _unregister_keymaps()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
