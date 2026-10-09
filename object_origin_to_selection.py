bl_info = {
    "name": "Set Origin to Selected",
    "author": "Ghislain Jeanneau",
    "version": (1,1),
    "blender": (2, 80, 0),
    "location": "Edit Mode -> Alt + C",
    "description": "Set origin directly with edit mode selection",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Object"
}



import bpy

def main(context):
    cursorPositionX = bpy.context.scene.cursor.location[0]
    cursorPositionY = bpy.context.scene.cursor.location[1]
    cursorPositionZ = bpy.context.scene.cursor.location[2]
    bpy.ops.view3d.snap_cursor_to_selected()
    bpy.ops.object.mode_set()
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.scene.cursor.location[0] = cursorPositionX
    bpy.context.scene.cursor.location[1] = cursorPositionY
    bpy.context.scene.cursor.location[2] = cursorPositionZ

class SetOriginToSelected(bpy.types.Operator):
    '''Tooltip'''
    bl_idname = "object.setorigintoselected"
    bl_label = "Set Origin to Selected"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        main(context)


        return {'FINISHED'}

addon_keymaps = []

def register():
    bpy.utils.register_class(SetOriginToSelected)

    wm = bpy.context.window_manager

    km = wm.keyconfigs.addon.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new('object.setorigintoselected', 'C', 'PRESS', alt=True)

    addon_keymaps.append(km)

def unregister():
    bpy.utils.unregister_class(SetOriginToSelected)

    wm = bpy.context.window_manager
    for km in addon_keymaps:
        wm.keyconfigs.addon.keymaps.remove(km)
    del addon_keymaps[:]


if __name__ == "__main__":
    register()
