bl_info = {
    "name": "Delete Selection",
    "author": "Kaluura",
    "version": (0,2),
    "blender": (2, 80, 0),
    "location": "Edit mode -> CTRL + X ",
    "description": "Deletes selected elements right away",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Mesh"
}

import bpy

class Delete_Selection(bpy.types.Operator):
    bl_label = "Delete Selection"
    bl_idname = "mesh.delete_selection"

    def execute(self, context):
        item_type = bpy.context.tool_settings.mesh_select_mode[0] + bpy.context.tool_settings.mesh_select_mode[1]*2 + bpy.context.tool_settings.mesh_select_mode[2]*4
        try:
            i = [1, 2, 4].index(item_type)
            # Fails with mixed mode
            bpy.ops.mesh.delete(type = ["0", "VERT", "EDGE", "3", "FACE", "5", "6", "7"][item_type])
        except:
            self.report({"WARNING"}, "Mixed selection mode not supported.")

        return {'FINISHED'}


addon_keymaps = []

def register():
    bpy.utils.register_class(Delete_Selection)

    wm = bpy.context.window_manager

    km = wm.keyconfigs.addon.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new('mesh.delete_selection', 'X', 'PRESS', ctrl=True)

    addon_keymaps.append(km)


def unregister():
    bpy.utils.unregister_class(Delete_Selection)

    wm = bpy.context.window_manager
    for km in addon_keymaps:
        wm.keyconfigs.addon.keymaps.remove(km)
    del addon_keymaps[:]


if __name__ == "__main__":
    register()
