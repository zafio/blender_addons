bl_info = {
    'name': 'Frame hotkeys',
    'author': 'Julio Iglesias, Adrian Novoa',
    'version': (0, 4),
    'blender': (4, 5, 0),
    'location':  'Timeline, Graph, Dopesheet: W/E (prev/next frame), 2/3 (framerate), D/F (prev/next action), Q (toggle preview range)',
    'warning': '',
    'description': 'Adds hotkeys to Increase/Decrease Framerate, Previous/Next Frame, Action and toggle Preview Range',
    'doc_url': '',
    'tracker_url': '',
    'category': 'Render'}

import math

import bpy


class AreaPollMixin:
    '''Operators are meant for animation editors only, never the 3D viewport'''

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type != 'VIEW_3D'


def set_action(context, action):
    '''Assigns the action to the active object and fits the preview range to its keyframes'''
    ob = context.object
    if ob.animation_data is None:
        ob.animation_data_create()
    ob.animation_data.action = action

    scene = context.scene
    start, end = action.frame_range
    scene.frame_preview_start = math.floor(start)
    scene.frame_preview_end = math.ceil(end)


def cycle_action(context, step):
    '''Moves through bpy.data.actions by step (+1/-1), wrapping around at both ends'''
    ob = context.object
    actions = list(bpy.data.actions)
    if ob is None or not actions:
        return

    current = ob.animation_data.action if ob.animation_data else None
    if current is None or current not in actions:
        set_action(context, actions[0])
    else:
        set_action(context, actions[(actions.index(current) + step) % len(actions)])


# Jumps to next action in list, sets preview range based on its first and last keyframes (so it works with no bones selected)
class DOPESHEET_OT_next_action(AreaPollMixin, bpy.types.Operator):
    '''Dopesheet Next Action'''
    bl_idname = "dopesheet.next_action"
    bl_label = "Dopesheet Next Action"

    def execute(self, context):
        cycle_action(context, 1)
        return {'FINISHED'}


# Jumps to previous action in list, sets preview range based on its first and last keyframes (so it works with no bones selected)
class DOPESHEET_OT_previous_action(AreaPollMixin, bpy.types.Operator):
    '''Dopesheet Previous Action'''
    bl_idname = "dopesheet.previous_action"
    bl_label = "Dopesheet previous Action"

    def execute(self, context):
        cycle_action(context, -1)
        return {'FINISHED'}


# Increases framerate by 5 or the closest value to make it multiple of 5
class RENDER_OT_set_fps_increase(AreaPollMixin, bpy.types.Operator):
    '''FPS Increase'''
    bl_idname = "render.set_fps_increase"
    bl_label = "FPS increase"

    def execute(self, context):
        render = context.scene.render
        render.fps += 5 - render.fps % 5
        return {'FINISHED'}


# Decreases framerate by 5 or the closest value to make it multiple of 5
class RENDER_OT_set_fps_decrease(AreaPollMixin, bpy.types.Operator):
    '''FPS decrease'''
    bl_idname = "render.set_fps_decrease"
    bl_label = "FPS decrease"

    def execute(self, context):
        render = context.scene.render
        render.fps = max(1, render.fps - (render.fps % 5 or 5))
        return {'FINISHED'}


# Toggles preview range at timeline ON or OFF
class TIMELINE_OT_toggle_preview_range(AreaPollMixin, bpy.types.Operator):
    '''Toggle Preview Range'''
    bl_idname = "timeline.toggle_preview_range"
    bl_label = "Toggle Preview Range"

    def execute(self, context):
        scene = context.scene
        scene.use_preview_range = not scene.use_preview_range
        return {'FINISHED'}


# Jumps to next frame, or first frame when current frame equals to end frame
class TIMELINE_OT_next_frame(AreaPollMixin, bpy.types.Operator):
    '''Next Frame'''
    bl_idname = "timeline.next_frame"
    bl_label = "Next Frame"

    def execute(self, context):
        scene = context.scene
        if scene.use_preview_range:
            start, end = scene.frame_preview_start, scene.frame_preview_end
        else:
            start, end = scene.frame_start, scene.frame_end

        if scene.frame_current == end:
            scene.frame_current = start
        elif scene.frame_current < end:
            scene.frame_current += 1

        return {'FINISHED'}


# Jumps to previous frame, or last frame when current frame equals to start frame
class TIMELINE_OT_previous_frame(AreaPollMixin, bpy.types.Operator):
    '''Previous Frame'''
    bl_idname = "timeline.previous_frame"
    bl_label = "Previous Frame"

    def execute(self, context):
        scene = context.scene
        if scene.use_preview_range:
            start, end = scene.frame_preview_start, scene.frame_preview_end
        else:
            start, end = scene.frame_start, scene.frame_end

        if scene.frame_current == start:
            scene.frame_current = end
        elif scene.frame_current > start:
            scene.frame_current -= 1

        return {'FINISHED'}


classes = (
    DOPESHEET_OT_next_action,
    DOPESHEET_OT_previous_action,
    RENDER_OT_set_fps_increase,
    RENDER_OT_set_fps_decrease,
    TIMELINE_OT_toggle_preview_range,
    TIMELINE_OT_next_frame,
    TIMELINE_OT_previous_frame,
)

# The Timeline is a mode of the Dopesheet editor since 2.80, so it shares the "Dopesheet" keymap
keymap_spaces = (
    ("Dopesheet", 'DOPESHEET_EDITOR'),
    ("Graph Editor", 'GRAPH_EDITOR'),
)

keymap_items = (
    ('dopesheet.next_action', 'F'),
    ('dopesheet.previous_action', 'D'),
    ('render.set_fps_increase', 'THREE'),
    ('render.set_fps_decrease', 'TWO'),
    ('timeline.toggle_preview_range', 'Q'),
    ('timeline.next_frame', 'E'),
    ('timeline.previous_frame', 'W'),
)

addon_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        for name, space_type in keymap_spaces:
            km = kc.keymaps.new(name=name, space_type=space_type)
            for idname, key in keymap_items:
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
