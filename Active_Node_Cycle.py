bl_info = {
    "name": "Active Node Cycle",
    "author": "Zafio",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "Shader Editor > Tab",
    "description": "Press Tab to cycle the active node through the currently selected nodes.",
    "category": "Node",
}

import bpy


class NODE_OT_cycle_active_node(bpy.types.Operator):
    """Cycle the active node through the currently selected nodes"""
    bl_idname = "node.cycle_active_node"
    bl_label = "Cycle Active Node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        space = context.space_data

        # Only operate in a Shader Editor
        if space is None or space.type != 'NODE_EDITOR':
            return False

        # Only operate on shader node trees
        if space.tree_type != 'ShaderNodeTree':
            return False

        node_tree = space.edit_tree
        if node_tree is None:
            return False

        # Need at least two selected nodes to cycle
        return len([n for n in node_tree.nodes if n.select]) >= 2

    def execute(self, context):
        space = context.space_data
        node_tree = space.edit_tree

        # Get selected nodes in a deterministic order.
        # Sorting by name means cycling remains predictable.
        selected = sorted(
            [node for node in node_tree.nodes if node.select],
            key=lambda node: node.name
        )

        if len(selected) < 2:
            return {'CANCELLED'}

        active = node_tree.nodes.active

        if active not in selected:
            # If the active node isn't selected, start with the first one.
            next_node = selected[0]
        else:
            current_index = selected.index(active)
            next_node = selected[(current_index + 1) % len(selected)]

        node_tree.nodes.active = next_node

        return {'FINISHED'}


# ------------------------------------------------------------------------
# Keymap
# ------------------------------------------------------------------------

addon_keymaps = []


def register_keymap():
    wm = bpy.context.window_manager

    # Shader Editor keymap
    kc = wm.keyconfigs.addon

    if kc is None:
        return

    km = kc.keymaps.new(
        name='Node Editor',
        space_type='NODE_EDITOR',
        region_type='WINDOW',
    )

    kmi = km.keymap_items.new(
        NODE_OT_cycle_active_node.bl_idname,
        type='TAB',
        value='PRESS',
    )

    addon_keymaps.append((km, kmi))


def unregister_keymap():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)

    addon_keymaps.clear()


classes = (
    NODE_OT_cycle_active_node,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    register_keymap()


def unregister():
    unregister_keymap()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()