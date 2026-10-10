bl_info = {
    "name": "UV Vertex Pivot Transform",
    "author": "Zafio",
    "version": (1, 2, 0),
    "blender": (4, 5, 0),
    "location": "UV Editor > Ctrl+R (rotate), Ctrl+X (mirror)",
    "description": "Allows to rotate and mirror the selection using the closest vertex "
                   "below the cursor as pivot. CTRL+R & CTRL+X (twice for Y)",
    "category": "UV",
}

import math

import bpy
import bmesh
import gpu
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty
from gpu_extras.batch import batch_for_shader

ADDON_ID = __package__ or __name__

NUM_KEYS = {
    'ZERO': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4',
    'FIVE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9',
    'NUMPAD_0': '0', 'NUMPAD_1': '1', 'NUMPAD_2': '2', 'NUMPAD_3': '3',
    'NUMPAD_4': '4', 'NUMPAD_5': '5', 'NUMPAD_6': '6', 'NUMPAD_7': '7',
    'NUMPAD_8': '8', 'NUMPAD_9': '9',
    'PERIOD': '.', 'NUMPAD_PERIOD': '.',
}

# Events that must reach Blender so the user can pan / zoom the UV view mid-transform.
PASS_THROUGH_TYPES = {
    'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE',
    'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION',
}


# ----------------------------------------------------------------------------
# Preferences
# ----------------------------------------------------------------------------

class UVPivotPrefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    pivot_source: EnumProperty(
        name="Pivot Candidates",
        description="Which UV vertices can become the pivot (closest one to the mouse wins)",
        items=[
            ('SELECTED', "Selected UVs", "Pivot is the closest selected UV vertex"),
            ('VISIBLE', "All Visible UVs", "Pivot is the closest visible UV vertex, selected or not"),
        ],
        default='SELECTED',
    )
    angle_direction: EnumProperty(
        name="Positive Angle",
        description="Direction of positive rotation values (typed and displayed)",
        items=[
            ('CW', "Clockwise", "Positive values rotate clockwise"),
            ('CCW', "Counter-clockwise", "Positive values rotate counter-clockwise"),
        ],
        default='CW',
    )
    mirror_cycle_off: BoolProperty(
        name="Mirror cycle includes 'off'",
        description="Pressing X repeatedly cycles X > Y > no mirror > X. "
                    "When disabled it cycles X > Y > X",
        default=True,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "pivot_source")
        col.prop(self, "angle_direction")
        col.prop(self, "mirror_cycle_off")


def _pref(name, default):
    try:
        return getattr(bpy.context.preferences.addons[ADDON_ID].preferences, name)
    except Exception:
        return default


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _image_aspect(space):
    img = getattr(space, "image", None)
    if img is not None and img.size[0] > 0 and img.size[1] > 0:
        return img.size[0] / img.size[1]
    return 1.0


def _gather(context):
    """Collect selected UV loops per mesh.

    Returns (objs, selected_pts, visible_pts) where objs is a list of
    (mesh, bmesh, [(BMLoopUV, u0, v0), ...]). The bmesh must stay referenced
    for as long as the BMLoopUV items are used, otherwise Python frees the
    wrapper and the UV references stop working. Selection rules follow Blender's
    UV transform: in sync mode with face select, only loops of selected
    faces count; in vertex/edge sync mode, loops of selected vertices.
    """
    ts = context.scene.tool_settings
    sync = ts.use_uv_select_sync
    face_mode = sync and ts.mesh_select_mode[2] and not ts.mesh_select_mode[0]

    objs, selected_pts, visible_pts = [], [], []

    for obj in context.objects_in_mode_unique_data:
        if obj.type != 'MESH':
            continue
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            continue
        items = []
        for face in bm.faces:
            if face.hide:
                continue
            if not sync and not face.select:
                continue
            for loop in face.loops:
                luv = loop[uv_layer]
                u, v = luv.uv
                visible_pts.append((u, v))
                if sync:
                    is_sel = face.select if face_mode else loop.vert.select
                else:
                    is_sel = luv.select
                if is_sel:
                    items.append((luv, u, v))
                    selected_pts.append((u, v))
        if items:
            objs.append((me, bm, items))

    return objs, selected_pts, visible_pts


def _update_meshes(objs):
    for me, _bm, _items in objs:
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)


def _rotate_uvs(objs, pivot, ccw, aspect):
    c, s = math.cos(ccw), math.sin(ccw)
    pu, pv = pivot
    for _me, _bm, items in objs:
        for luv, u0, v0 in items:
            x = (u0 - pu) * aspect
            y = v0 - pv
            luv.uv = (pu + (x * c - y * s) / aspect, pv + (x * s + y * c))
    _update_meshes(objs)


def _mirror_uvs(objs, pivot, axis):
    pu, pv = pivot
    for _me, _bm, items in objs:
        for luv, u0, v0 in items:
            if axis == 'X':
                luv.uv = (2.0 * pu - u0, v0)
            elif axis == 'Y':
                luv.uv = (u0, 2.0 * pv - v0)
            else:
                luv.uv = (u0, v0)
    _update_meshes(objs)


def _point_shader():
    # POINT_UNIFORM_COLOR draws sized points on every GPU backend (Metal/Vulkan too).
    try:
        return gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    except Exception:
        return gpu.shader.from_builtin('UNIFORM_COLOR')


# ----------------------------------------------------------------------------
# Shared modal base
# ----------------------------------------------------------------------------

class _PivotTransform:
    """Common logic: pick pivot, modal loop, drawing, cancel/restore, redo."""

    @classmethod
    def poll(cls, context):
        sd = context.space_data
        return (
            context.mode == 'EDIT_MESH'
            and sd is not None
            and sd.type == 'IMAGE_EDITOR'
            and getattr(sd, "show_uvedit", False)
        )

    def _pick_pivot(self, context, event, pts):
        v2d = context.region.view2d
        mx, my = event.mouse_region_x, event.mouse_region_y
        best, best_d = pts[0], float("inf")
        for u, v in pts:
            rx, ry = v2d.view_to_region(u, v, clip=False)
            d = (rx - mx) ** 2 + (ry - my) ** 2
            if d < best_d:
                best, best_d = (u, v), d
        return best

    def _pivot_region(self):
        x, y = self.region.view2d.view_to_region(self.pivot[0], self.pivot[1], clip=False)
        return float(x), float(y)

    def _restore(self):
        for _me, _bm, items in self.objs:
            for luv, u0, v0 in items:
                luv.uv = (u0, v0)
        _update_meshes(self.objs)

    # -- redo / repeat (Adjust Last Operation panel, Shift+R) ---------------

    def execute(self, context):
        objs, _sel, _vis = _gather(context)
        if not objs:
            self.report({'WARNING'}, "No UVs selected (or no active UV map)")
            return {'CANCELLED'}
        self.apply_props(context, objs)
        return {'FINISHED'}

    # -- lifecycle ----------------------------------------------------------

    def invoke(self, context, event):
        if context.region is None or context.region.type != 'WINDOW':
            return {'CANCELLED'}
        self.objs, selected_pts, visible_pts = _gather(context)
        if not self.objs:
            self.report({'WARNING'}, "No UVs selected (or no active UV map)")
            return {'CANCELLED'}

        pts = visible_pts if _pref("pivot_source", 'SELECTED') == 'VISIBLE' else selected_pts
        self.region = context.region
        self.area = context.area
        self.pivot = self._pick_pivot(context, event, pts)
        self.mouse = (event.mouse_region_x, event.mouse_region_y)
        self.ctrl = event.ctrl
        self.shift = event.shift
        self.alt = event.alt
        self._applied = None
        self._resync = False

        try:
            self.setup(context, event)
            self.refresh(context)
        except Exception as exc:
            self._restore()
            try:
                self.area.header_text_set(None)
            except Exception:
                pass
            self.report({'ERROR'}, f"{self.bl_label} failed: {exc}")
            return {'CANCELLED'}

        # Overlay is only added once the first update succeeded, so a failure
        # can never leave a stuck dot/axis on screen.
        self._handler = bpy.types.SpaceImageEditor.draw_handler_add(
            self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.workspace.status_text_set(self.status_text)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if getattr(self, "_handler", None) is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self._handler, 'WINDOW')
            self._handler = None
        try:
            self.area.header_text_set(None)
            self.area.tag_redraw()
        except Exception:
            pass
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:
            # Never leave a dangling draw handler or half-transformed UVs behind.
            self._end(context)
            self._restore()
            self.report({'ERROR'}, f"{self.bl_label} failed: {exc}")
            return {'CANCELLED'}

    def _modal(self, context, event):
        et = event.type

        if et in PASS_THROUGH_TYPES:
            self._resync = True
            return {'PASS_THROUGH'}

        self.ctrl = event.ctrl
        self.shift = event.shift
        self.alt = event.alt

        if event.value == 'PRESS' and et in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'}:
            self._end(context)
            self.store_props(context)
            _update_meshes(self.objs)
            return {'FINISHED'}

        if et == 'ESC' and event.value == 'PRESS':
            self._end(context)
            self._restore()
            return {'CANCELLED'}

        if et == 'RIGHTMOUSE':
            if event.value == 'RELEASE':
                self._end(context)
                self._restore()
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}

        self.handle(context, event)
        self.refresh(context)
        return {'RUNNING_MODAL'}

    # -- drawing ------------------------------------------------------------

    def _draw(self):
        # The handler is global to all Image Editors: only draw in our own region.
        if bpy.context.region != self.region:
            return

        px, py = self._pivot_region()
        vp = gpu.state.viewport_get()
        gpu.state.blend_set('ALPHA')

        line_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        for p0, p1, color in self.draw_lines(px, py):
            batch = batch_for_shader(
                line_shader, 'LINES',
                {"pos": [(p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0)]})
            line_shader.bind()
            line_shader.uniform_float("viewportSize", (vp[2], vp[3]))
            line_shader.uniform_float("lineWidth", 1.5)
            line_shader.uniform_float("color", color)
            batch.draw(line_shader)

        point_shader = _point_shader()
        gpu.state.point_size_set(9.0)
        batch = batch_for_shader(point_shader, 'POINTS', {"pos": [(px, py, 0.0)]})
        point_shader.bind()
        point_shader.uniform_float("color", (1.0, 0.65, 0.1, 1.0))
        batch.draw(point_shader)
        gpu.state.point_size_set(1.0)

        gpu.state.blend_set('NONE')

    # -- to be provided by subclasses --------------------------------------

    def setup(self, context, event):
        raise NotImplementedError

    def handle(self, context, event):
        raise NotImplementedError

    def refresh(self, context):
        raise NotImplementedError

    def store_props(self, context):
        raise NotImplementedError

    def apply_props(self, context, objs):
        raise NotImplementedError

    def draw_lines(self, px, py):
        return []


# ----------------------------------------------------------------------------
# Rotate  (Ctrl+R)
# ----------------------------------------------------------------------------

class UV_OT_vertex_pivot_rotate(_PivotTransform, bpy.types.Operator):
    bl_idname = "uv.vertex_pivot_rotate"
    bl_label = "Rotate Around Nearest Vertex"
    bl_description = ("Rotate the selected UVs around the UV vertex closest to the mouse. "
                      "Type a value in degrees, hold Ctrl to snap, Alt to snap to 90°, "
                      "Shift for precision")
    bl_options = {'REGISTER', 'UNDO'}

    angle: FloatProperty(name="Angle", subtype='ANGLE', default=0.0)
    pivot: FloatVectorProperty(name="Pivot", size=2, default=(0.0, 0.0))

    @staticmethod
    def _sign():
        return -1.0 if _pref("angle_direction", 'CW') == 'CW' else 1.0

    def setup(self, context, event):
        self.raw = 0.0                  # accumulated mouse angle (displayed convention)
        self.num_active = False
        self.buf = ""
        self.neg = False
        self.sign = self._sign()
        self.aspect = _image_aspect(context.space_data)
        px, py = self._pivot_region()
        self.prev_vec = (self.mouse[0] - px, self.mouse[1] - py)
        self.status_text = ("Rotate  |  Mouse / type value: angle  |  Ctrl: snap  |  "
                            "Alt: snap 90°  |  Shift: precision  |  LMB/Enter: confirm  |  Esc/RMB: cancel")

    # -- mouse -> angle (incremental, wraps past 180° like Blender) ---------

    def _track(self, mx, my):
        px, py = self._pivot_region()
        cx, cy = mx - px, my - py
        self.mouse = (mx, my)

        if self._resync:
            self.prev_vec = (cx, cy)
            self._resync = False
            return

        if math.hypot(cx, cy) < 2.0:
            return
        ax, ay = self.prev_vec
        if math.hypot(ax, ay) < 2.0:
            self.prev_vec = (cx, cy)
            return

        delta = math.atan2(ax * cy - ay * cx, ax * cx + ay * cy)   # CCW positive
        self.prev_vec = (cx, cy)
        if self.shift:
            delta *= 0.1
        self.raw += delta * self.sign

    # -- events -------------------------------------------------------------

    def handle(self, context, event):
        et, val = event.type, event.value

        if et in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._track(event.mouse_region_x, event.mouse_region_y)
            return

        if val != 'PRESS':
            return

        if et in NUM_KEYS:
            ch = NUM_KEYS[et]
            if ch == '.' and '.' in self.buf:
                return
            self.num_active = True
            self.buf += ch
        elif et in {'MINUS', 'NUMPAD_MINUS'}:
            self.num_active = True
            self.neg = not self.neg
        elif et == 'BACK_SPACE':
            if event.ctrl:
                self.buf, self.neg = "", False
            else:
                self.buf = self.buf[:-1]
            if not self.buf and not self.neg:
                self.num_active = False

    # -- value / apply ------------------------------------------------------

    def _snap_increment(self, context):
        ts = context.scene.tool_settings
        if self.shift:
            inc = getattr(ts, "snap_angle_increment_2d_precision", math.radians(1.0))
        else:
            inc = getattr(ts, "snap_angle_increment_2d", math.radians(5.0))
        return inc if inc > 1e-6 else math.radians(5.0)

    def _value(self, context):
        """Returns (displayed angle in radians, snap increment or None)."""
        if self.num_active:
            try:
                deg = float(self.buf) if self.buf else 0.0
            except ValueError:
                deg = 0.0
            return math.radians(-deg if self.neg else deg), None
        if self.alt:
            inc = math.pi / 2.0
            return round(self.raw / inc) * inc, inc
        if self.ctrl:
            inc = self._snap_increment(context)
            return round(self.raw / inc) * inc, inc
        return self.raw, None

    def refresh(self, context):
        value, inc = self._value(context)
        if self._applied is None or abs(value - self._applied) > 1e-12:
            _rotate_uvs(self.objs, self.pivot, value * self.sign, self.aspect)
            self._applied = value

        if self.num_active:
            typed = ("-" if self.neg else "") + self.buf + "_"
            text = f"Rotate: {typed}°"
        else:
            text = f"Rotate: {math.degrees(value):.2f}°"
        if inc is not None:
            text += f"   (snap {math.degrees(inc):g}°)"
        self.area.header_text_set(text)
        self.area.tag_redraw()

    def store_props(self, context):
        self.angle = self._applied or 0.0

    def apply_props(self, context, objs):
        _rotate_uvs(objs, self.pivot, self.angle * self._sign(),
                    _image_aspect(context.space_data))

    def draw_lines(self, px, py):
        mx, my = self.mouse
        return [((px, py), (mx, my), (1.0, 1.0, 1.0, 0.55))]


# ----------------------------------------------------------------------------
# Mirror  (Ctrl+X)
# ----------------------------------------------------------------------------

class UV_OT_vertex_pivot_mirror(_PivotTransform, bpy.types.Operator):
    bl_idname = "uv.vertex_pivot_mirror"
    bl_label = "Mirror Around Nearest Vertex"
    bl_description = ("Mirror the selected UVs around the UV vertex closest to the mouse. "
                      "Press X to cycle X > Y > off, Y to jump to the Y axis")
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(
        name="Axis",
        items=[('X', "X", "Mirror along U"),
               ('Y', "Y", "Mirror along V"),
               ('NONE', "None", "No mirror")],
        default='X',
    )
    pivot: FloatVectorProperty(name="Pivot", size=2, default=(0.0, 0.0))

    def setup(self, context, event):
        self.cur_axis = 'X'
        self.status_text = ("Mirror  |  X: cycle axis (X > Y > off)  |  Y: Y axis  |  "
                            "LMB/Enter: confirm  |  Esc/RMB: cancel")

    def handle(self, context, event):
        if event.value != 'PRESS':
            return
        if event.type == 'X':
            cycle = ['X', 'Y', 'NONE'] if _pref("mirror_cycle_off", True) else ['X', 'Y']
            if self.cur_axis in cycle:
                self.cur_axis = cycle[(cycle.index(self.cur_axis) + 1) % len(cycle)]
            else:
                self.cur_axis = 'X'
        elif event.type == 'Y':
            self.cur_axis = 'Y'

    def refresh(self, context):
        if self._applied != self.cur_axis:
            _mirror_uvs(self.objs, self.pivot, self.cur_axis)
            self._applied = self.cur_axis
        label = {'X': "X axis", 'Y': "Y axis", 'NONE': "off"}[self.cur_axis]
        self.area.header_text_set(f"Mirror: {label}")
        self.area.tag_redraw()

    def store_props(self, context):
        self.axis = self.cur_axis

    def apply_props(self, context, objs):
        _mirror_uvs(objs, self.pivot, self.axis)

    def draw_lines(self, px, py):
        w, h = self.region.width, self.region.height
        if self.cur_axis == 'X':      # flips U -> the mirror line is vertical
            return [((px, 0), (px, h), (0.86, 0.22, 0.30, 0.9))]
        if self.cur_axis == 'Y':      # flips V -> the mirror line is horizontal
            return [((0, py), (w, py), (0.55, 0.86, 0.0, 0.9))]
        return []


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------

classes = (UVPivotPrefs, UV_OT_vertex_pivot_rotate, UV_OT_vertex_pivot_mirror)
addon_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="UV Editor", space_type='EMPTY')
        kmi = km.keymap_items.new(UV_OT_vertex_pivot_rotate.bl_idname, 'R', 'PRESS', ctrl=True)
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(UV_OT_vertex_pivot_mirror.bl_idname, 'X', 'PRESS', ctrl=True)
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
