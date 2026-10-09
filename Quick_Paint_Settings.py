bl_info = {
    "name": "QuickPaintSettingsPanel",
    "description": "Popup Panel and Hotkeys for quicker access to common paint settings",
    "author": "Zafio",
    "version": (0, 0, 8),
    "blender": (4, 5, 0),
    "location": "View3D (Image Paint & Vertex Paint) and Image Editor",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Paint"
}


###############   IMPORTS
import bpy
from bpy.utils import previews
import os
import math


###############   INITIALIZE VARIABLES
quickpaintsettingspanel = {
    "imageeditorstatus": True, 
    "ui_size": 1.0, 
    "ui_icon_style": True, 
}


###############   SERPENS FUNCTIONS
def exec_line(line):
    exec(line)

def sn_print(tree_name, *args):
    if tree_name in bpy.data.node_groups:
        item = bpy.data.node_groups[tree_name].sn_graphs[0].prints.add()
        for arg in args:
            item.value += str(arg) + ";;;"
        if bpy.context and bpy.context.screen:
            for area in bpy.context.screen.areas:
                area.tag_redraw()
    print(*args)

def sn_cast_string(value):
    return str(value)

def sn_cast_boolean(value):
    if type(value) == tuple:
        for data in value:
            if bool(data):
                return True
        return False
    return bool(value)

def sn_cast_float(value):
    if type(value) == str:
        try:
            value = float(value)
            return value
        except:
            return float(bool(value))
    elif type(value) == tuple:
        return float(value[0])
    elif type(value) == list:
        return float(len(value))
    elif not type(value) in [float, int, bool]:
        try:
            value = len(value)
            return float(value)
        except:
            return float(bool(value))
    return float(value)

def sn_cast_int(value):
    return int(sn_cast_float(value))

def sn_cast_boolean_vector(value, size):
    if type(value) in [str, bool, int, float]:
        return_value = []
        for i in range(size):
            return_value.append(bool(value))
        return tuple(return_value)
    elif type(value) == tuple:
        return_value = []
        for i in range(size):
            return_value.append(bool(value[i]) if len(value) > i else bool(value[0]))
        return tuple(return_value)
    elif type(value) == list:
        return sn_cast_boolean_vector(tuple(value), size)
    else:
        try:
            value = tuple(value)
            return sn_cast_boolean_vector(value, size)
        except:
            return sn_cast_boolean_vector(bool(value), size)

def sn_cast_float_vector(value, size):
    if type(value) in [str, bool, int, float]:
        return_value = []
        for i in range(size):
            return_value.append(sn_cast_float(value))
        return tuple(return_value)
    elif type(value) == tuple:
        return_value = []
        for i in range(size):
            return_value.append(sn_cast_float(value[i]) if len(value) > i else sn_cast_float(value[0]))
        return tuple(return_value)
    elif type(value) == list:
        return sn_cast_float_vector(tuple(value), size)
    else:
        try:
            value = tuple(value)
            return sn_cast_float_vector(value, size)
        except:
            return sn_cast_float_vector(sn_cast_float(value), size)

def sn_cast_int_vector(value, size):
    return tuple(map(int, sn_cast_float_vector(value, size)))

def sn_cast_color(value, use_alpha):
    length = 4 if use_alpha else 3
    value = sn_cast_float_vector(value, length)
    tuple_list = []
    for data in range(length):
        data = value[data] if len(value) > data else value[0]
        tuple_list.append(sn_cast_float(min(1, max(0, data))))
    return tuple(tuple_list)

def sn_cast_list(value):
    if type(value) in [str, tuple, list]:
        return list(value)
    elif type(value) in [int, float, bool]:
        return [value]
    else:
        try:
            value = list(value)
            return value
        except:
            return [value]

def sn_cast_blend_data(value):
    if hasattr(value, "bl_rna"):
        return value
    elif type(value) in [tuple, bool, int, float, list]:
        return None
    elif type(value) == str:
        try:
            value = eval(value)
            return value
        except:
            return None
    else:
        return None

def sn_cast_enum(string, enum_values):
    for item in enum_values:
        if item[1] == string:
            return item[0]
        elif item[0] == string.upper():
            return item[0]
    return string


###############   IMPERATIVE CODE
addon_keymaps = {}


###############   COLOR JITTER / RANDOMIZE COLOR LOGIC
def apply_color_jitter(context, h=None, s=None, v=None):
    ts = getattr(context.scene, "tool_settings", None) or getattr(context, "tool_settings", None)
    if not ts:
        return

    ups = getattr(ts, "unified_paint_settings", None)
    if ups and hasattr(ups, "use_color_jitter"):
        ups.use_color_jitter = True

    brushes = []
    if hasattr(ts, "image_paint") and getattr(ts.image_paint, "brush", None):
        brushes.append(ts.image_paint.brush)
    if hasattr(ts, "vertex_paint") and getattr(ts.vertex_paint, "brush", None):
        brushes.append(ts.vertex_paint.brush)
    if hasattr(ts, "sculpt") and getattr(ts.sculpt, "brush", None):
        brushes.append(ts.sculpt.brush)

    targets = ([ups] if ups else []) + brushes

    for target in targets:
        if not target:
            continue
        if hasattr(target, "use_color_jitter"):
            target.use_color_jitter = True

        if h is not None:
            for attr in ("color_jitter_hue", "jitter_hue", "hue_jitter", "random_hue"):
                if hasattr(target, attr):
                    setattr(target, attr, h)

        if s is not None:
            for attr in ("color_jitter_saturation", "jitter_saturation", "saturation_jitter", "random_saturation"):
                if hasattr(target, attr):
                    setattr(target, attr, s)

        if v is not None:
            for attr in ("color_jitter_value", "jitter_value", "value_jitter", "random_value"):
                if hasattr(target, attr):
                    setattr(target, attr, v)


def get_rand_hue(self):
    ts = getattr(bpy.context.scene, "tool_settings", None)
    if ts:
        ups = getattr(ts, "unified_paint_settings", None)
        targets = ([ups] if ups else []) + [getattr(getattr(ts, "image_paint", None), "brush", None)]
        for target in targets:
            if target:
                for attr in ("color_jitter_hue", "jitter_hue", "hue_jitter", "random_hue"):
                    if hasattr(target, attr):
                        return getattr(target, attr)
    return self.get("rand_hue", 0.0)

def set_rand_hue(self, value):
    self["rand_hue"] = value
    apply_color_jitter(bpy.context, h=value)


def get_rand_sat(self):
    ts = getattr(bpy.context.scene, "tool_settings", None)
    if ts:
        ups = getattr(ts, "unified_paint_settings", None)
        targets = ([ups] if ups else []) + [getattr(getattr(ts, "image_paint", None), "brush", None)]
        for target in targets:
            if target:
                for attr in ("color_jitter_saturation", "jitter_saturation", "saturation_jitter", "random_saturation"):
                    if hasattr(target, attr):
                        return getattr(target, attr)
    return self.get("rand_sat", 0.0)

def set_rand_sat(self, value):
    self["rand_sat"] = value
    apply_color_jitter(bpy.context, s=value)


def get_rand_val(self):
    ts = getattr(bpy.context.scene, "tool_settings", None)
    if ts:
        ups = getattr(ts, "unified_paint_settings", None)
        targets = ([ups] if ups else []) + [getattr(getattr(ts, "image_paint", None), "brush", None)]
        for target in targets:
            if target:
                for attr in ("color_jitter_value", "jitter_value", "value_jitter", "random_value"):
                    if hasattr(target, attr):
                        return getattr(target, attr)
    return self.get("rand_val", 0.0)

def set_rand_val(self, value):
    self["rand_val"] = value
    apply_color_jitter(bpy.context, v=value)


###############   PAINT SYMMETRY HANDLER
def get_paint_symmetry(context, axis):
    axis = axis.lower()
    attr_sym = f"use_symmetry_{axis}"
    attr_mesh = f"use_mesh_mirror_{axis}"
    attr_mirror = f"use_mirror_{axis}"

    ts = getattr(context.scene, "tool_settings", None) or getattr(context, "tool_settings", None)
    if ts:
        ip = getattr(ts, "image_paint", None)
        if ip and hasattr(ip, attr_sym):
            return getattr(ip, attr_sym)

        vp = getattr(ts, "vertex_paint", None)
        if vp and hasattr(vp, attr_sym):
            return getattr(vp, attr_sym)

        sc = getattr(ts, "sculpt", None)
        if sc and hasattr(sc, attr_sym):
            return getattr(sc, attr_sym)

        for p in (ip, vp, sc):
            b = getattr(p, "brush", None)
            if b and hasattr(b, attr_sym):
                return getattr(b, attr_sym)

        if hasattr(ts, attr_mesh):
            return getattr(ts, attr_mesh)

    obj = getattr(context, "active_object", None)
    if obj:
        if hasattr(obj, attr_mesh):
            return getattr(obj, attr_mesh)
        if hasattr(getattr(obj, "data", None), attr_mirror):
            return getattr(obj.data, attr_mirror)

    return False


def toggle_paint_symmetry(context, axis):
    axis = axis.lower()
    attr_sym = f"use_symmetry_{axis}"
    attr_mesh = f"use_mesh_mirror_{axis}"
    attr_mirror = f"use_mirror_{axis}"

    new_val = not get_paint_symmetry(context, axis)

    ts = getattr(context.scene, "tool_settings", None) or getattr(context, "tool_settings", None)
    if ts:
        ip = getattr(ts, "image_paint", None)
        if ip and hasattr(ip, attr_sym):
            setattr(ip, attr_sym, new_val)

        vp = getattr(ts, "vertex_paint", None)
        if vp and hasattr(vp, attr_sym):
            setattr(vp, attr_sym, new_val)

        sc = getattr(ts, "sculpt", None)
        if sc and hasattr(sc, attr_sym):
            setattr(sc, attr_sym, new_val)

        for p in (ip, vp, sc):
            b = getattr(p, "brush", None)
            if b and hasattr(b, attr_sym):
                setattr(b, attr_sym, new_val)

        if hasattr(ts, attr_mesh):
            setattr(ts, attr_mesh, new_val)

    obj = getattr(context, "active_object", None)
    if obj:
        if hasattr(obj, attr_mesh):
            setattr(obj, attr_mesh, new_val)
        if hasattr(getattr(obj, "data", None), attr_mirror):
            setattr(obj.data, attr_mirror, new_val)


###############   OPERATORS
class SNA_OT_Toggle_X_Mirror(bpy.types.Operator):
    bl_idname = "sna.toggle_x_mirror"
    bl_label = "Toggle_X_Mirror"
    bl_description = "Toggles X Paint Symmetry"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            toggle_paint_symmetry(context, 'x')
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_X_Mirror")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Y_Mirror(bpy.types.Operator):
    bl_idname = "sna.toggle_y_mirror"
    bl_label = "Toggle_Y_Mirror"
    bl_description = "Toggles Y Paint Symmetry"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            toggle_paint_symmetry(context, 'y')
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_Y_Mirror")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Z_Mirror(bpy.types.Operator):
    bl_idname = "sna.toggle_z_mirror"
    bl_label = "Toggle_Z_Mirror"
    bl_description = "Toggles Z Paint Symmetry"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            toggle_paint_symmetry(context, 'z')
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_Z_Mirror")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Jitter_Mode(bpy.types.Operator):
    bl_idname = "sna.toggle_jitter_mode"
    bl_label = "Toggle_Jitter_Mode"
    bl_description = "Toggles Jitter mode between View and Brush"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            if bpy.context.scene.tool_settings.image_paint.brush.jitter_unit == r"BRUSH":
                bpy.context.scene.tool_settings.image_paint.brush.jitter_unit = sn_cast_enum(r"VIEW", [("VIEW","View","Jittering happens in screen space, in pixels"),("BRUSH","Brush","Jittering happens relative to the brush size"),])
                bpy.context.scene.tool_settings.vertex_paint.brush.jitter_unit = sn_cast_enum(r"VIEW", [("VIEW","View","Jittering happens in screen space, in pixels"),("BRUSH","Brush","Jittering happens relative to the brush size"),])
            else:
                bpy.context.scene.tool_settings.image_paint.brush.jitter_unit = sn_cast_enum(r"BRUSH", [("VIEW","View","Jittering happens in screen space, in pixels"),("BRUSH","Brush","Jittering happens relative to the brush size"),])
                bpy.context.scene.tool_settings.vertex_paint.brush.jitter_unit = sn_cast_enum(r"BRUSH", [("VIEW","View","Jittering happens in screen space, in pixels"),("BRUSH","Brush","Jittering happens relative to the brush size"),])
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_Jitter_Mode")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


###############   PIXEL CURVE PRESET
# Hard step falloff: full strength up to just under half the radius, then zero.
PIXEL_CURVE_POINTS = ((0.0, 1.0), (0.499, 1.0), (0.5, 0.0), (1.0, 0.0))


def get_active_paint_brush(context):
    """Return the brush of the current paint mode (vertex or texture/image paint)."""
    ts = context.scene.tool_settings
    if context.mode == 'PAINT_VERTEX':
        return ts.vertex_paint.brush
    return ts.image_paint.brush


def apply_pixel_curve(brush):
    # Blender 4.5 uses brush.curve / curve_preset; 5.0 renames them to curve_distance_falloff*
    if hasattr(brush, "curve_distance_falloff"):
        brush.curve_distance_falloff_preset = 'CUSTOM'
        mapping = brush.curve_distance_falloff
    else:
        brush.curve_preset = 'CUSTOM'
        mapping = brush.curve

    points = mapping.curves[0].points
    # A curve must keep at least 2 points: trim down to 2, then rebuild
    while len(points) > 2:
        points.remove(points[-1])
    (x0, y0), (x1, y1) = PIXEL_CURVE_POINTS[0], PIXEL_CURVE_POINTS[-1]
    points[0].location = (x0, y0)
    points[1].location = (x1, y1)
    for x, y in PIXEL_CURVE_POINTS[1:-1]:
        points.new(x, y)
    # Vector handles keep the segments straight (no overshoot around the step)
    for p in points:
        p.handle_type = 'VECTOR'
    mapping.update()


class SNA_OT_Pixel_Curve_Preset(bpy.types.Operator):
    bl_idname = "sna.pixel_curve_preset"
    bl_label = "Pixel Curve Preset"
    bl_description = "Pixel preset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            brush = get_active_paint_brush(context)
            if brush is None:
                self.report({'WARNING'}, "No active paint brush")
                return {"CANCELLED"}
            apply_pixel_curve(brush)
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Pixel_Curve_Preset")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


###############   BACKGROUND GRADIENT
def get_background_color(context):
    """Return the current 3D View theme background color."""
    try:
        theme = context.preferences.themes[0]
        return tuple(theme.view_3d.space.gradients.high_gradient)
    except Exception:
        return (0.05, 0.05, 0.05)


def set_background_color(context, color):
    """Set the 3D View theme background color while preserving alpha."""
    try:
        theme = context.preferences.themes[0]
        theme.view_3d.space.gradients.high_gradient = (
            float(color[0]), float(color[1]), float(color[2])
        )
    except Exception as exc:
        print(str(exc) + " | Error setting background color")


def get_bg_gradient(self):
    return get_background_color(bpy.context)


def set_bg_gradient(self, value):
    self["bg_gradient"] = tuple(value)
    set_background_color(bpy.context, value)
    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class SNA_OT_Reset_Background(bpy.types.Operator):
    bl_idname = "sna.reset_background"
    bl_label = "Reset Background"
    bl_description = "Reset the background color to the current theme's default"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            # Restore Blender's built-in/theme background gradient value.
            # The theme's high_gradient is the background color used by the viewport.
            theme = context.preferences.themes[0]
            theme.view_3d.space.gradients.high_gradient = (
                0.05, 0.05, 0.05
            )

            if context.screen:
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except Exception as exc:
            print(str(exc) + " | Error resetting background color")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


###############   PANEL
class SNA_PT_Brush_Settings_86BC5(bpy.types.Panel):
    bl_label = "Brush Settings"
    bl_idname = "SNA_PT_Brush_Settings_86BC5"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return (bpy.context.mode == r"PAINT_TEXTURE" or bpy.context.mode == r"PAINT_VERTEX")

    def draw_header(self, context):
        pass

    def draw(self, context):
        try:
            layout = self.layout
            ui_style = quickpaintsettingspanel["ui_icon_style"]

            col = layout.column(align=True)
            col.enabled = True
            col.alert = False
            col.scale_x = quickpaintsettingspanel["ui_size"]
            col.scale_y = quickpaintsettingspanel["ui_size"]

            # ── Color Selector ──
            ups = bpy.context.scene.tool_settings.unified_paint_settings
            if bpy.context.mode == r"PAINT_TEXTURE":
                brush = bpy.context.scene.tool_settings.image_paint.brush
            else:
                brush = bpy.context.scene.tool_settings.vertex_paint.brush

            # Use unified color if enabled, otherwise use per-brush color
            if ups.use_unified_color:
                color_data = ups
            else:
                color_data = brush

            # Color picker wheel with value (brightness) slider
            col.template_color_picker(color_data, "color", value_slider=True)

            # Primary / Secondary color swatches + swap button
            row = col.row(align=True)
            row.scale_y = 0.8
            row.prop(color_data, "color", text="")
            row.prop(color_data, "secondary_color", text="")
            row.operator("paint.brush_colors_flip", icon='FILE_REFRESH', text="")

            col.separator(factor=0.5)

            # Row 1: Blend Mode + UI Style + UI Size
            row = col.row(align=True)
            row.enabled = True
            row.alert = False
            row.scale_x = 1.0
            row.scale_y = 1.0
            if bpy.context.mode == r"PAINT_TEXTURE":
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'blend',icon_value=0,text=r"",emboss=ui_style,expand=False,)
            else:
                row.prop(bpy.context.scene.tool_settings.vertex_paint.brush,'blend',icon_value=0,text=r"",emboss=ui_style,expand=False,)
            op = row.operator("sna.toggle_ui_style",text=r"",emboss=False,depress=True,icon='PREFERENCES')
            op = row.operator("sna.toggle_ui_size",text=r"",emboss=False,depress=True,icon='FULLSCREEN_ENTER')
            
            # Row 2: Falloff Curve Presets + Paint Symmetry
            # Curves + AA get 73% of the width, the X/Y/Z mirror buttons the rest
            row_split = col.split(factor=0.60, align=True)
            row_split.enabled = True
            row_split.alert = False
            row = row_split.row(align=True)
            op = row.operator("brush.curve_preset",text=r"",emboss=ui_style,depress=False,icon='SMOOTHCURVE')
            op.shape = sn_cast_enum(r"SMOOTH", [("SHARP","Sharp",""),("SMOOTH","Smooth",""),("MAX","Max",""),("LINE","Line",""),("ROUND","Round",""),("ROOT","Root",""),])
            op = row.operator("brush.curve_preset",text=r"",emboss=ui_style,depress=False,icon='SPHERECURVE')
            op.shape = sn_cast_enum(r"ROUND", [("SHARP","Sharp",""),("SMOOTH","Smooth",""),("MAX","Max",""),("LINE","Line",""),("ROUND","Round",""),("ROOT","Root",""),])
            op = row.operator("brush.curve_preset",text=r"",emboss=ui_style,depress=False,icon='SHARPCURVE')
            op.shape = sn_cast_enum(r"SHARP", [("SHARP","Sharp",""),("SMOOTH","Smooth",""),("MAX","Max",""),("LINE","Line",""),("ROUND","Round",""),("ROOT","Root",""),])
            op = row.operator("brush.curve_preset",text=r"",emboss=ui_style,depress=False,icon='NOCURVE')
            op.shape = sn_cast_enum(r"MAX", [("SHARP","Sharp",""),("SMOOTH","Smooth",""),("MAX","Max",""),("LINE","Line",""),("ROUND","Round",""),("ROOT","Root",""),])
            op = row.operator("sna.pixel_curve_preset",text=r"",emboss=ui_style,depress=False,icon='DOT')
            if bpy.context.scene.tool_settings.image_paint.brush:
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'use_paint_antialiasing',text=r"AA",emboss=ui_style,toggle=True,)

            # Paint Symmetry buttons (X, Y, Z)
            row = row_split.row(align=True)
            depress_x = get_paint_symmetry(bpy.context, 'x')
            depress_y = get_paint_symmetry(bpy.context, 'y')
            depress_z = get_paint_symmetry(bpy.context, 'z')
            op = row.operator("sna.toggle_x_mirror",text=r"X",emboss=ui_style,depress=depress_x,icon_value=0)
            op = row.operator("sna.toggle_y_mirror",text=r"Y",emboss=ui_style,depress=depress_y,icon_value=0)
            op = row.operator("sna.toggle_z_mirror",text=r"Z",emboss=ui_style,depress=depress_z,icon_value=0)
            
            col.separator(factor=0.18)
            
            # Row 3: Radius
            row = col.row(align=True)
            row.enabled = True
            row.alert = False
            row.scale_x = 1.0
            row.scale_y = 1.0
            row.prop(bpy.context.scene.tool_settings.unified_paint_settings,'size',icon_value=0,text=r"Radius",emboss=ui_style,slider=True,)
            if bpy.context.mode == r"PAINT_TEXTURE":
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'use_pressure_size',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            else:
                row.prop(bpy.context.scene.tool_settings.vertex_paint.brush,'use_pressure_size',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            
            # Row 4: Strength
            row = col.row(align=True)
            row.enabled = True
            row.alert = False
            row.scale_x = 1.0
            row.scale_y = 1.0
            row.prop(bpy.context.scene.tool_settings.unified_paint_settings,'strength',icon_value=0,text=r"Strength",emboss=ui_style,slider=True,)
            if bpy.context.mode == r"PAINT_TEXTURE":
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'use_pressure_strength',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            else:
                row.prop(bpy.context.scene.tool_settings.vertex_paint.brush,'use_pressure_strength',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            
            # Row 5: Spacing
            row = col.row(align=True)
            row.enabled = True
            row.alert = False
            row.scale_x = 1.0
            row.scale_y = 1.0
            row.prop(bpy.context.scene.tool_settings.image_paint.brush,'spacing',icon_value=0,text=r"Spacing",emboss=ui_style,slider=True,)
            if bpy.context.mode == r"PAINT_TEXTURE":
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'use_pressure_spacing',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            else:
                row.prop(bpy.context.scene.tool_settings.vertex_paint.brush,'use_pressure_spacing',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            
            # Row 6: Jitter
            row = col.row(align=True)
            row.enabled = True
            row.alert = False
            row.scale_x = 1.0
            row.scale_y = 1.0
            if bpy.context.scene.tool_settings.image_paint.brush.jitter_unit == r"BRUSH":
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'jitter',icon_value=0,text=r"Jitter",emboss=ui_style,slider=True,)
            else:
                row.prop(bpy.context.scene.tool_settings.image_paint.brush,'jitter_absolute',icon_value=0,text=r"Jitter",emboss=ui_style,slider=False,)
            jitter_icon = 'BRUSH_DATA' if bpy.context.scene.tool_settings.image_paint.brush.jitter_unit == r"BRUSH" else 'RESTRICT_VIEW_OFF'
            op = row.operator("sna.toggle_jitter_mode",text=r"",emboss=ui_style,depress=False,icon=jitter_icon)
            row.prop(bpy.context.scene.tool_settings.image_paint.brush,'use_pressure_jitter',icon='STYLUS_PRESSURE',text=r"",emboss=ui_style,toggle=True,)
            
            # Row 7: Bleed
            if bpy.context.mode == r"PAINT_TEXTURE":
                split = col.split(align=True,factor=0.9)
                split.enabled = True
                split.alert = False
                split.scale_x = 1.0
                split.scale_y = 1.0
                split.prop(bpy.context.scene.tool_settings.image_paint,'seam_bleed',icon_value=0,text=r"Bleed",emboss=ui_style,slider=True,)
                op = split.operator("sna.multiply_bleed",text=r"x2",emboss=ui_style,depress=False,icon_value=0)
            else:
                pass
            
            col.separator(factor=0.18)
            
            # Row 8: Randomize Color (Hue, Saturation, Value)
            col.prop(bpy.context.scene, 'rand_hue', text="Hue", emboss=ui_style, slider=True)
            col.prop(bpy.context.scene, 'rand_sat', text="Saturation", emboss=ui_style, slider=True)
            col.prop(bpy.context.scene, 'rand_val', text="Value", emboss=ui_style, slider=True)


            # Bottom: 3D View background gradient color/value control
            col.separator(factor=0.5)
            row = col.row(align=True)
            row.prop(bpy.context.scene, 'bg_gradient', text="Background", slider=True)
            row.operator("sna.reset_background", text="", icon='FILE_REFRESH', emboss=ui_style)

        except Exception as exc:
            print(str(exc) + " | Error in Brush Settings panel")


class SNA_OT_Multiply_Bleed(bpy.types.Operator):
    bl_idname = "sna.multiply_bleed"
    bl_label = "Multiply_Bleed"
    bl_description = "Multiply x2 (up to 64 then resets)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            if bpy.context.scene.tool_settings.image_paint.seam_bleed > 32:
                bpy.context.scene.tool_settings.image_paint.seam_bleed = 0
            else:
                if bpy.context.scene.tool_settings.image_paint.seam_bleed == 0:
                    bpy.context.scene.tool_settings.image_paint.seam_bleed = 1
                else:
                    bpy.context.scene.tool_settings.image_paint.seam_bleed = int((sn_cast_float(bpy.context.scene.tool_settings.image_paint.seam_bleed) * 2.0))
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Multiply_Bleed")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Ui_Style(bpy.types.Operator):
    bl_idname = "sna.toggle_ui_style"
    bl_label = "Toggle_UI_Style"
    bl_description = "Changes the UI style"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            quickpaintsettingspanel["ui_icon_style"] = not quickpaintsettingspanel["ui_icon_style"]
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_UI_Style")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_OT_Toggle_Ui_Size(bpy.types.Operator):
    bl_idname = "sna.toggle_ui_size"
    bl_label = "Toggle_UI_Size"
    bl_description = ""
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        try:
            if quickpaintsettingspanel["ui_size"] == 1.0:
                quickpaintsettingspanel["ui_size"] = 1.5
            else:
                quickpaintsettingspanel["ui_size"] = 1.0
        except Exception as exc:
            print(str(exc) + " | Error in execute function of Toggle_UI_Size")
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


def register_key_757AB():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("wm.call_panel",
                                    type= "S",
                                    value= "PRESS",
                                    repeat= False,
                                    ctrl=False,
                                    alt=False,
                                    shift=False)
        kmi.properties.name = "SNA_PT_Brush_Settings_86BC5"
        kmi.properties.keep_open = True
        addon_keymaps['757AB'] = (km, kmi)

def register_key_C6025():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Image", space_type="IMAGE_EDITOR")
        kmi = km.keymap_items.new("wm.call_panel",
                                    type= "S",
                                    value= "PRESS",
                                    repeat= False,
                                    ctrl=False,
                                    alt=False,
                                    shift=False)
        kmi.properties.name = "SNA_PT_Brush_Settings_86BC5"
        kmi.properties.keep_open = True
        addon_keymaps['C6025'] = (km, kmi)

def register_key_314FA():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("wm.call_panel",
                                    type= "GRLESS",
                                    value= "PRESS",
                                    repeat= False,
                                    ctrl=False,
                                    alt=False,
                                    shift=False)
        kmi.properties.name = "SNA_PT_Brush_Settings_86BC5"
        kmi.properties.keep_open = True
        addon_keymaps['314FA'] = (km, kmi)

def register_key_B0360():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Image", space_type="IMAGE_EDITOR")
        kmi = km.keymap_items.new("wm.call_panel",
                                    type= "GRLESS",
                                    value= "PRESS",
                                    repeat= False,
                                    ctrl=False,
                                    alt=False,
                                    shift=False)
        kmi.properties.name = "SNA_PT_Brush_Settings_86BC5"
        kmi.properties.keep_open = True
        addon_keymaps['B0360'] = (km, kmi)


###############   REGISTER ICONS
def sn_register_icons():
    icons = []
    bpy.types.Scene.quickpaintsettingspanel_icons = bpy.utils.previews.new()
    icons_dir = os.path.join( os.path.dirname( __file__ ), "icons" )
    for icon in icons:
        bpy.types.Scene.quickpaintsettingspanel_icons.load( icon, os.path.join( icons_dir, icon + ".png" ), 'IMAGE' )

def sn_unregister_icons():
    bpy.utils.previews.remove( bpy.types.Scene.quickpaintsettingspanel_icons )


###############   REGISTER PROPERTIES
def sn_register_properties():
    bpy.types.Scene.rand_hue = bpy.props.FloatProperty(
        name='Hue',
        description='Hue Randomization',
        subtype='PERCENTAGE',
        unit='NONE',
        options={'HIDDEN'},
        precision=2,
        default=0.0,
        min=0.0,
        max=1.0,
        get=get_rand_hue,
        set=set_rand_hue
    )
    bpy.types.Scene.rand_sat = bpy.props.FloatProperty(
        name='Saturation',
        description='Saturation Randomization',
        subtype='PERCENTAGE',
        unit='NONE',
        options={'HIDDEN'},
        precision=2,
        default=0.0,
        min=0.0,
        max=1.0,
        get=get_rand_sat,
        set=set_rand_sat
    )
    bpy.types.Scene.rand_val = bpy.props.FloatProperty(
        name='Value',
        description='Value Randomization',
        subtype='PERCENTAGE',
        unit='NONE',
        options={'HIDDEN'},
        precision=2,
        default=0.0,
        min=0.0,
        max=1.0,
        get=get_rand_val,
        set=set_rand_val
    )
    bpy.types.Scene.bg_gradient = bpy.props.FloatVectorProperty(
        name='Background',
        description='Adjusts the 3D View background gradient color',
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.05, 0.05, 0.05),
        get=get_bg_gradient,
        set=set_bg_gradient
    )

def sn_unregister_properties():
    if hasattr(bpy.types.Scene, "rand_hue"):
        del bpy.types.Scene.rand_hue
    if hasattr(bpy.types.Scene, "rand_sat"):
        del bpy.types.Scene.rand_sat
    if hasattr(bpy.types.Scene, "rand_val"):
        del bpy.types.Scene.rand_val
    if hasattr(bpy.types.Scene, "bg_gradient"):
        del bpy.types.Scene.bg_gradient


###############   REGISTER ADDON
def register():
    sn_register_icons()
    sn_register_properties()
    bpy.utils.register_class(SNA_OT_Toggle_X_Mirror)
    bpy.utils.register_class(SNA_OT_Toggle_Y_Mirror)
    bpy.utils.register_class(SNA_OT_Toggle_Z_Mirror)
    bpy.utils.register_class(SNA_OT_Toggle_Jitter_Mode)
    bpy.utils.register_class(SNA_OT_Pixel_Curve_Preset)
    bpy.utils.register_class(SNA_OT_Reset_Background)
    bpy.utils.register_class(SNA_PT_Brush_Settings_86BC5)
    bpy.utils.register_class(SNA_OT_Multiply_Bleed)
    bpy.utils.register_class(SNA_OT_Toggle_Ui_Style)
    bpy.utils.register_class(SNA_OT_Toggle_Ui_Size)
    register_key_757AB()
    register_key_C6025()
    register_key_314FA()
    register_key_B0360()


###############   UNREGISTER ADDON
def unregister():
    sn_unregister_icons()
    sn_unregister_properties()
    for key in addon_keymaps:
        km, kmi = addon_keymaps[key]
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    bpy.utils.unregister_class(SNA_OT_Toggle_Ui_Size)
    bpy.utils.unregister_class(SNA_OT_Toggle_Ui_Style)
    bpy.utils.unregister_class(SNA_OT_Multiply_Bleed)
    bpy.utils.unregister_class(SNA_PT_Brush_Settings_86BC5)
    bpy.utils.unregister_class(SNA_OT_Reset_Background)
    bpy.utils.unregister_class(SNA_OT_Pixel_Curve_Preset)
    bpy.utils.unregister_class(SNA_OT_Toggle_Jitter_Mode)
    bpy.utils.unregister_class(SNA_OT_Toggle_Z_Mirror)
    bpy.utils.unregister_class(SNA_OT_Toggle_Y_Mirror)
    bpy.utils.unregister_class(SNA_OT_Toggle_X_Mirror)


if __name__ == "__main__":
    register()
