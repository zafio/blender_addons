####RENAME TOOLS (coded by Luca Scheller)
Adds a modal operator to rename objects, and automatically renames object suffixes (based on Unity3D LOD naming) when duplicating an object: HI > LOD0 > LOD1 > LOD2... renaming also the mesh data name.

- CTRL+ALT+R = runs the rename tool (CTRL+ALT+R again deletes the whole name)
- Enter & LMB = Accept
- Esc & RMB = Cancel

####FRAME HOTKEYS
Adds hotkeys to improve control over animation playback. At Timeline, Graph or Dopesheet Editors:

- Q = Toggles preview range
- W = Previous frame (loops within start & end frames)
- E = Next frame (loops within start & end frames)
- D = Previous Action (loops within action list, auto-sets preview range for each one)
- F = Next Action (loops within action list, auto-sets preview range for each one)
- 2 = Decreases Framerate by 5
- 3 = Increases Framerate by 5

####PROJECT PAINT TOGGLE
Alt+S (Texture Paint mode) toggles "Occlude, Cull and Normal" at once; Alt+D toggles Bleed between 0 and its previous value.
Cursor: green = paint through, red = bleed on, blue = both, white = neither.
https://www.youtube.com/watch?v=rL1v3YSVyCg

####EASY PIXEL INTERPOL
Makes pixel-art texturing quicker: no more trips to the Shader Editor to set Image Texture nodes to "Closest".

- New Image Texture nodes default to Closest (toggle and default mode in the add-on preferences; existing nodes are left untouched)
- CTRL+ALT+L = Toggles Closest / Linear for the active image (Image Editor, Texture Paint mode, or the active Image Texture node in the Shader Editor)
- Interpolation dropdown in the Image Editor header and the Texture Slots panel

####UV VERTEX PIVOT TRANSFORM
Rotates and mirrors the UV selection using the closest vertex below the cursor as pivot (UV Editor). Works like Blender's own transforms: type a value, hold Ctrl to snap the angle, Alt to snap to 90º, Shift for precision.

- CTRL+R = Rotate around the nearest vertex
- CTRL+X = Mirror around the nearest vertex (X again switches to Y axis, again turns mirror off)
- Enter & LMB = Accept
- Esc & RMB = Cancel
