bl_info = {
    "name": "UV Collision Transform",
    "author": "Based on UV Sandbox by Alberto Gonzalez / Modified by Zafio",
    "version": (0, 9, 3),
    "blender": (4, 5, 0),
    "location": "UV Editor > G (Move) / Header toggle / N-Panel > UV Collision",
    "description": "Collision-aware UV Move (G) with per-triangle collision, sliding, overlap groups, "
                   "pixel snapping and bounds constraint. Hold Alt while moving to toggle collision.",
    "category": "UV",
}

import math
import time

import bpy
import gpu
import bmesh
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


SEP_EPS = 1e-7        # touching edges count as "not overlapping"
UV_MATCH_EPS = 1e-5   # UVs closer than this are considered connected
BISECT_STEPS = 10     # precision of the contact search
MAX_SUBSTEPS = 64     # anti-tunnelling cap per update
SKIN = 1e-6          # UV units kept between islands after a contact (avoids re-penetration)
SLIDE_ITERS = 4      # contact/slide iterations per update
SLOW_UPDATE = 0.012   # seconds; slower updates get coalesced on a timer instead of per event

addon_keymaps = []


# ---------------------------------------------------------------------------
# Geometry helpers (numpy, vectorised)
# ---------------------------------------------------------------------------

def tri_aabbs(tris):
    """(N,3,2) -> (N,4) [minx, miny, maxx, maxy]."""
    return np.concatenate([tris.min(axis=1), tris.max(axis=1)], axis=1)


def sat_overlap(A, B, eps=SEP_EPS):
    """Pairwise triangle overlap test (Separating Axis Theorem).
    A, B: (P,3,2). Returns (P,) bool, True where the pair truly overlaps."""
    ea = np.roll(A, -1, axis=1) - A
    eb = np.roll(B, -1, axis=1) - B
    axes = np.concatenate([
        np.stack([-ea[..., 1], ea[..., 0]], axis=-1),
        np.stack([-eb[..., 1], eb[..., 0]], axis=-1),
    ], axis=1)
    axes /= (np.linalg.norm(axes, axis=-1, keepdims=True) + 1e-20)
    pa = np.einsum('pkd,pvd->pkv', axes, A)
    pb = np.einsum('pkd,pvd->pkv', axes, B)
    sep = ((pa.max(axis=2) <= pb.min(axis=2) + eps) |
           (pb.max(axis=2) <= pa.min(axis=2) + eps))
    return ~sep.any(axis=1)


def iter_tri_hits(T, S, Sa=None):
    """Yield (ii, jj) index arrays of overlapping triangle pairs between T and S."""
    if len(T) == 0 or len(S) == 0:
        return
    if Sa is None:
        Sa = tri_aabbs(S)
    Ta = tri_aabbs(T)
    chunk = max(1, 4_000_000 // max(1, len(S)))
    for s in range(0, len(T), chunk):
        ta = Ta[s:s + chunk]
        m = ((ta[:, None, 0] < Sa[None, :, 2]) & (ta[:, None, 2] > Sa[None, :, 0]) &
             (ta[:, None, 1] < Sa[None, :, 3]) & (ta[:, None, 3] > Sa[None, :, 1]))
        ii, jj = np.nonzero(m)
        if ii.size == 0:
            continue
        hit = sat_overlap(T[s:s + chunk][ii], S[jj])
        if hit.any():
            yield ii[hit] + s, jj[hit]


def outline_tri_mask(isl):
    """Triangles with at least one vertex on the island outline (cached on the island)."""
    m = isl.get('btri')
    if m is not None:
        return m
    ti, b, off = isl['tri_idx'], isl['boundary'], isl['offsets']
    if len(ti) == 0 or len(b) == 0:
        m = np.ones(len(ti), dtype=bool)
    else:
        q = np.round(off / 1e-7).astype(np.int64)
        key = q[:, 0] * (1 << 32) + q[:, 1]
        outline = np.unique(key[b.ravel()])
        m = np.isin(key[ti], outline).any(axis=1)
    isl['btri'] = m
    return m


def point_in_tris(p, tris):
    """True if point p (2,) lies in any of tris (N,3,2)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]

    def cross(o, u, v):
        return (u[:, 0] - o[:, 0]) * (v[1] - o[:, 1]) - (u[:, 1] - o[:, 1]) * (v[0] - o[:, 0])

    d1, d2, d3 = cross(a, b, p), cross(b, c, p), cross(c, a, p)
    neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return bool(np.any(~(neg & pos)))


def get_resolution(context):
    sima = context.space_data
    if sima and sima.type == 'IMAGE_EDITOR' and sima.image:
        w, h = sima.image.size
        if w > 0 and h > 0:
            return w, h
    res = context.scene.uv_solid_tex_res
    return res, res


def get_padding(context):
    """Required gap between islands, in UV units."""
    sc = context.scene
    mode = sc.uv_solid_padding_mode
    if mode == 'PIXELS':
        rx, ry = get_resolution(context)
        return max(0.0, sc.uv_solid_padding_px) / float(min(rx, ry))
    if mode == 'PERCENT':
        return max(0.0, sc.uv_solid_padding_pct) / 100.0
    return 0.0


def _pt_seg_dist(P, G):
    """Distances (P, G) from points P (n,2) to segments G (m,2,2)."""
    a = G[None, :, 0]
    d = G[None, :, 1] - a
    dd = (d * d).sum(-1)
    t = np.clip(((P[:, None] - a) * d).sum(-1) / np.maximum(dd, 1e-30), 0.0, 1.0)
    diff = P[:, None] - (a + t[..., None] * d)
    return np.hypot(diff[..., 0], diff[..., 1])


def addon_prefs(context):
    addon = context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


# ---------------------------------------------------------------------------
# Shared collision core
# ---------------------------------------------------------------------------

class CollisionCore:
    """Island loading, overlap groups, rigid-body move with collision + sliding."""

    pad = 0.0

    # ------------------------------------------------------------ load ----

    def load_islands(self, context):
        """Vectorised island build from mesh arrays (fast); BMLoop refs are fetched lazily."""
        obj = context.active_object
        self.obj = obj
        obj.update_from_editmode()                  # sync bmesh -> mesh arrays (C side)
        me = obj.data
        self.bm = bmesh.from_edit_mesh(me)
        self.bm.faces.ensure_lookup_table()
        self.uv_layer = self.bm.loops.layers.uv.active

        nL, nF = len(me.loops), len(me.polygons)

        # UV data is not exposed on the mesh in Edit Mode -> read it from BMesh in one pass
        uvb = self.uv_layer
        bm_loops = [l for f in self.bm.faces for l in f.loops]
        if len(bm_loops) != nL:
            raise RuntimeError("UV Collision: mesh/BMesh loop count mismatch")
        uv = np.array([l[uvb].uv[:] for l in bm_loops], dtype=np.float64).reshape(-1, 2)
        lv = np.empty(nL, dtype=np.int64); me.loops.foreach_get('vertex_index', lv)
        le = np.empty(nL, dtype=np.int64); me.loops.foreach_get('edge_index', le)
        ls = np.empty(nF, dtype=np.int64); me.polygons.foreach_get('loop_start', ls)
        lt = np.empty(nF, dtype=np.int64); me.polygons.foreach_get('loop_total', lt)
        fhide = np.empty(nF, dtype=bool); me.polygons.foreach_get('hide', fhide)
        fsel = np.empty(nF, dtype=bool); me.polygons.foreach_get('select', fsel)

        sync = context.tool_settings.use_uv_select_sync
        lface = np.repeat(np.arange(nF), lt)
        nxt = np.arange(nL) + 1
        nxt[ls + lt - 1] = ls                          # wrap to first loop of each face

        fvis = ~fhide if sync else (~fhide & fsel)
        lvis = fvis[lface]

        if sync:
            vsel = np.empty(len(me.vertices), dtype=bool); me.vertices.foreach_get('select', vsel)
            lsel = vsel[lv]
        else:
            lsel = np.fromiter((l[uvb].select for l in bm_loops), dtype=bool, count=nL)

        # --- island connectivity: faces join across an edge when UVs match at both ends
        vis_idx = np.nonzero(lvis)[0]
        q = np.round(uv / UV_MATCH_EPS).astype(np.int64)
        a_v, b_v = lv[vis_idx], lv[nxt[vis_idx]]
        qa, qb = q[vis_idx], q[nxt[vis_idx]]
        swap = a_v > b_v
        q0 = np.where(swap[:, None], qb, qa)
        q1 = np.where(swap[:, None], qa, qb)
        keys = np.column_stack([le[vis_idx], q0, q1])
        _, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        inv = inv.ravel()
        connected = np.zeros(nL, dtype=bool)
        connected[vis_idx] = counts[inv] > 1

        order = np.argsort(inv, kind='stable')
        same = inv[order[1:]] == inv[order[:-1]]
        fa = lface[vis_idx[order[:-1][same]]]
        fb = lface[vis_idx[order[1:][same]]]

        label = np.arange(nF)
        if fa.size:
            while True:                                  # min-label propagation + pointer jumping
                m = np.minimum(label[fa], label[fb])
                new_label = label.copy()
                np.minimum.at(new_label, fa, m)
                np.minimum.at(new_label, fb, m)
                new_label = new_label[new_label]
                if np.array_equal(new_label, label):
                    break
                label = new_label

        # --- group loops per island, ordered by (face, loop) to match BMesh iteration
        isl_lab = label[lface[vis_idx]]
        ordr = np.lexsort((vis_idx, lface[vis_idx], isl_lab))
        sorted_loops = vis_idx[ordr]
        sorted_lab = isl_lab[ordr]
        cuts = np.nonzero(np.diff(sorted_lab))[0] + 1
        loop_groups = np.split(sorted_loops, cuts) if sorted_loops.size else []

        local = np.full(nL, -1, dtype=np.int64)
        lab_to_isl = {}
        self.islands = []
        for k, gl in enumerate(loop_groups):
            local[gl] = np.arange(len(gl))
            lab_to_isl[int(label[lface[gl[0]]])] = k
            pts = uv[gl]
            mn, mx = pts.min(axis=0), pts.max(axis=0)
            center = (mn + mx) * 0.5
            bmask = ~connected[gl]
            bl = gl[bmask]
            n_sel = int(lsel[gl].sum())
            self.islands.append({
                'center': center.copy(),
                'offsets': pts - center,
                'rel_bounds': np.array([mn[0] - center[0], mn[1] - center[1],
                                        mx[0] - center[0], mx[1] - center[1]]),
                'faces': np.unique(lface[gl]),
                'loops': [bm_loops[i] for i in gl],
                'boundary': np.column_stack([local[bl], local[nxt[bl]]]).reshape(-1, 2),
                'sel': 'all' if n_sel == len(gl) else ('some' if n_sel else 'none'),
                'tri_idx': None,
            })

        # --- triangles
        me.calc_loop_triangles()
        nT = len(me.loop_triangles)
        tl = np.empty(nT * 3, dtype=np.int64); me.loop_triangles.foreach_get('loops', tl)
        tl = tl.reshape(-1, 3)
        tp = np.empty(nT, dtype=np.int64); me.loop_triangles.foreach_get('polygon_index', tp)
        keep = fvis[tp]
        tl, tp = tl[keep], tp[keep]
        t_isl = np.array([lab_to_isl[int(x)] for x in label[tp]], dtype=np.int64) if len(tp) else np.zeros(0, np.int64)
        if len(t_isl):
            tord = np.argsort(t_isl, kind='stable')
            t_isl, tl = t_isl[tord], tl[tord]
            tcuts = np.nonzero(np.diff(t_isl))[0] + 1
            for grp in np.split(np.arange(len(t_isl)), tcuts):
                k = int(t_isl[grp[0]])
                self.islands[k]['tri_idx'] = local[tl[grp]]
        for isl in self.islands:
            if isl['tri_idx'] is None:
                isl['tri_idx'] = np.zeros((0, 3), dtype=np.int64)
            isl['tris0'] = isl['offsets'][isl['tri_idx']]

        self.original_centers = [isl['center'].copy() for isl in self.islands]
        self.group_of = None            # overlap groups are computed lazily
        self.overlap_group_count = 0
        self.members = []

    def island_loops(self, isl):
        if isl['loops'] is None:
            faces = self.bm.faces
            isl['loops'] = [l for f in isl['faces'] for l in faces[int(f)].loops]
        return isl['loops']

    def detect_overlap_groups(self):
        n = len(self.islands)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        world = [isl['tris0'] + isl['center'] for isl in self.islands]
        boxes = [isl['rel_bounds'] + np.tile(isl['center'], 2) for isl in self.islands]
        for i in range(n):
            for j in range(i + 1, n):
                if find(i) == find(j):
                    continue
                a, b = boxes[i], boxes[j]
                if not (a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]):
                    continue
                for _ in iter_tri_hits(world[i], world[j]):
                    parent[find(j)] = find(i)
                    break

        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        self.group_of = [None] * n
        for members in groups.values():
            for i in members:
                self.group_of[i] = members
        self.overlap_group_count = sum(1 for g in groups.values() if len(g) > 1)

    def ensure_groups(self):
        if self.group_of is None:
            t = time.perf_counter()
            self.detect_overlap_groups()
            print(f"[UV Collision] overlap groups: {(time.perf_counter() - t) * 1000:.1f} ms")

    def expand_groups(self, idxs, group):
        if not group:
            return sorted(set(idxs))
        self.ensure_groups()
        out = set()
        for i in idxs:
            out.update(self.group_of[i])
        return sorted(out)

    # ----------------------------------------------------- write back ----

    def island_luvs(self, isl):
        """Cached per-loop UV accessors (BMLoopUV) for fast writing."""
        luvs = isl.get('luvs')
        if luvs is None:
            uv_layer = self.uv_layer
            luvs = isl['luvs'] = [l[uv_layer] for l in self.island_loops(isl)]
        return luvs

    def apply_island(self, isl):
        pts = (isl['offsets'] + isl['center']).tolist()
        for luv, p in zip(self.island_luvs(isl), pts):
            luv.uv = p

    def apply_members(self, force=False):
        """Write the moving islands' UVs. Skipped when nothing moved. Returns True if written."""
        key = (tuple(self.members), tuple(self.body_center()) if self.members else ())
        if not force and key == getattr(self, '_applied_key', None):
            return False
        for m in self.members:
            self.apply_island(self.islands[m])
        # UV-only change: no re-triangulation / topology rebuild needed
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        self._applied_key = key
        return True

    def restore_members(self):
        for m in self.members:
            self.islands[m]['center'] = self.original_centers[m].copy()
        self.apply_members(force=True)

    # ------------------------------------------------------ rigid body ----

    def begin_body(self, members):
        """Treat `members` as one rigid body; everything else is static."""
        self.members = list(members)
        self.anchor = self.members[0]
        self.raw = None
        self._last_snap = None
        anchor_c = self.islands[self.anchor]['center']
        self.member_rel = {m: self.islands[m]['center'] - anchor_c for m in self.members}

        parts = [self.islands[m]['tris0'] + self.member_rel[m]
                 for m in self.members if len(self.islands[m]['tris0'])]
        self.mov_tris0 = np.concatenate(parts) if parts else np.zeros((0, 3, 2))
        rbs = np.array([self.islands[m]['rel_bounds'] + np.tile(self.member_rel[m], 2)
                        for m in self.members])
        self.mov_rb = np.array([rbs[:, 0].min(), rbs[:, 1].min(), rbs[:, 2].max(), rbs[:, 3].max()])

        member_set = set(self.members)
        tris, ids = [], []
        for j, other in enumerate(self.islands):
            if j in member_set or len(other['tris0']) == 0:
                continue
            t = other['tris0'] + other['center']
            tris.append(t)
            ids.append(np.full(len(t), j))
        if tris:
            self.all_static_tris = np.concatenate(tris)
            self.all_static_ids = np.concatenate(ids)
        else:
            self.all_static_tris = np.zeros((0, 3, 2))
            self.all_static_ids = np.zeros(0, dtype=np.int64)
        self.all_static_aabb = (tri_aabbs(self.all_static_tris)
                                if len(self.all_static_tris) else np.zeros((0, 4)))
        self.mov_aabb0 = tri_aabbs(self.mov_tris0) if len(self.mov_tris0) else np.zeros((0, 4))

        # Outline-band triangles (any vertex on the island outline): used for all tests while
        # moving. Penetration from outside always starts in these, so they're enough as long as
        # each step is smaller than the islands (guaranteed by the sub-steps).
        mb = [self.islands[m]['tris0'][outline_tri_mask(self.islands[m])] + self.member_rel[m]
              for m in self.members if len(self.islands[m]['tris0'])]
        self.mov_btris0 = np.concatenate(mb) if mb else np.zeros((0, 3, 2))
        self.mov_baabb0 = tri_aabbs(self.mov_btris0) if len(self.mov_btris0) else np.zeros((0, 4))
        sb, sid = [], []
        for j, other in enumerate(self.islands):
            if j in member_set or len(other['tris0']) == 0:
                continue
            t = other['tris0'][outline_tri_mask(other)] + other['center']
            sb.append(t)
            sid.append(np.full(len(t), j))
        self.all_static_btris = np.concatenate(sb) if sb else np.zeros((0, 3, 2))
        self.all_static_bids = np.concatenate(sid) if sid else np.zeros(0, dtype=np.int64)
        self.all_static_baabb = (tri_aabbs(self.all_static_btris)
                                 if len(self.all_static_btris) else np.zeros((0, 4)))

        # Outline segments: only used to get accurate contact normals for sliding
        ms = [self.islands[m]['offsets'][self.islands[m]['boundary']] + self.member_rel[m]
              for m in self.members if len(self.islands[m]['boundary'])]
        self.mov_segs0 = np.concatenate(ms) if ms else np.zeros((0, 2, 2))
        ss = [other['offsets'][other['boundary']] + other['center']
              for j, other in enumerate(self.islands)
              if j not in member_set and len(other['boundary'])]
        si = [np.full(len(other['boundary']), j)
              for j, other in enumerate(self.islands)
              if j not in member_set and len(other['boundary'])]
        self.all_static_segs = np.concatenate(ss) if ss else np.zeros((0, 2, 2))
        self.all_static_segids = np.concatenate(si) if si else np.zeros(0, dtype=np.int64)

        self.island_min_dim = np.array([min(i['rel_bounds'][2] - i['rel_bounds'][0],
                                            i['rel_bounds'][3] - i['rel_bounds'][1])
                                        for i in self.islands])
        self.arm_collision()

    def _set_static(self, exclude_ids=None, empty=False):
        names = (('static_tris', 'all_static_tris'), ('static_ids', 'all_static_ids'),
                 ('static_aabb', 'all_static_aabb'), ('static_btris', 'all_static_btris'),
                 ('static_bids', 'all_static_bids'), ('static_baabb', 'all_static_baabb'))
        for dst, src in names:
            arr = getattr(self, src)
            setattr(self, dst, arr[:0] if empty else arr)
        self.static_segs = self.all_static_segs[:0] if empty else self.all_static_segs
        if exclude_ids and not empty:
            k = ~np.isin(self.all_static_segids, exclude_ids)
            self.static_segs = self.static_segs[k]
            k = ~np.isin(self.all_static_ids, exclude_ids)
            self.static_tris, self.static_ids, self.static_aabb = (
                self.static_tris[k], self.static_ids[k], self.static_aabb[k])
            k = ~np.isin(self.all_static_bids, exclude_ids)
            self.static_btris, self.static_bids, self.static_baabb = (
                self.static_btris[k], self.static_bids[k], self.static_baabb[k])

    def arm_collision(self):
        """(Re)activate collision; islands overlapping right now are ignored so you can escape."""
        self._set_static()
        c = self.body_center()
        stuck = self.overlapping_ids(c) | self.too_close_ids(c)
        if stuck:
            self._set_static(exclude_ids=list(stuck))
        self._armed = True
        self.use_full()

    def disarm_collision(self):
        self._armed = False
        self._set_static(empty=True)
        self.use_full()

    # Active working sets for motion tests: full outline bands, or a subset near the sweep
    def use_full(self):
        self._Sseg = getattr(self, 'static_segs', np.zeros((0, 2, 2)))
        self._S, self._Sa, self._Sid = self.static_btris, self.static_baabb, self.static_bids
        self._M, self._Ma = self.mov_btris0, self.mov_baabb0

    def prepare_sweep(self, c, dist):
        """Restrict both sets to what can possibly touch within `dist` of c.
        Returns False when nothing can be hit (free move)."""
        if len(self.static_btris) == 0 or len(self.mov_btris0) == 0:
            return False
        rb, pad = self.mov_rb, dist + self.pad + 1e-6
        box = (rb[0] + c[0] - pad, rb[1] + c[1] - pad, rb[2] + c[0] + pad, rb[3] + c[1] + pad)
        sa = self.static_baabb
        s_idx = np.nonzero((sa[:, 0] < box[2]) & (sa[:, 2] > box[0]) &
                           (sa[:, 1] < box[3]) & (sa[:, 3] > box[1]))[0]
        if s_idx.size == 0:
            return False
        ssub = sa[s_idx]
        U = (ssub[:, 0].min(), ssub[:, 1].min(), ssub[:, 2].max(), ssub[:, 3].max())
        ma = self.mov_baabb0
        m_idx = np.nonzero((ma[:, 0] + c[0] - pad < U[2]) & (ma[:, 2] + c[0] + pad > U[0]) &
                           (ma[:, 1] + c[1] - pad < U[3]) & (ma[:, 3] + c[1] + pad > U[1]))[0]
        if m_idx.size == 0:
            return False
        self._S, self._Sa, self._Sid = self.static_btris[s_idx], ssub, self.static_bids[s_idx]
        self._M, self._Ma = self.mov_btris0[m_idx], ma[m_idx]
        S = self.static_segs
        if len(S):
            smn, smx = S.min(axis=1), S.max(axis=1)
            k = (smn[:, 0] <= box[2]) & (smx[:, 0] >= box[0]) & (smn[:, 1] <= box[3]) & (smx[:, 1] >= box[1])
            self._Sseg = S[k]
        else:
            self._Sseg = S
        return True

    def body_center(self):
        return self.islands[self.anchor]['center'].copy()

    def set_body_center(self, c):
        for m in self.members:
            self.islands[m]['center'] = c + self.member_rel[m]

    def _pair_hits(self, center):
        """Yield (moving idx, static idx) arrays of truly overlapping outline-band triangles."""
        S, Sa, M = self._S, self._Sa, self._M
        if len(S) == 0 or len(M) == 0:
            return
        rb = self.mov_rb
        bx0, by0 = rb[0] + center[0], rb[1] + center[1]
        bx1, by1 = rb[2] + center[0], rb[3] + center[1]
        cand = np.nonzero((Sa[:, 0] < bx1) & (Sa[:, 2] > bx0) &
                          (Sa[:, 1] < by1) & (Sa[:, 3] > by0))[0]
        if cand.size == 0:
            return
        Sc = Sa[cand]
        U = (Sc[:, 0].min(), Sc[:, 1].min(), Sc[:, 2].max(), Sc[:, 3].max())
        Ma = self._Ma
        mi = np.nonzero((Ma[:, 0] + center[0] < U[2]) & (Ma[:, 2] + center[0] > U[0]) &
                        (Ma[:, 1] + center[1] < U[3]) & (Ma[:, 3] + center[1] > U[1]))[0]
        if mi.size == 0:
            return
        for ii, jj in iter_tri_hits(M[mi] + center, S[cand], Sc):
            yield mi[ii], cand[jj]

    def collides(self, center):
        for _ in self._pair_hits(center):
            return True
        pad = self.pad
        if pad > 0.0:
            return self._min_dist(center, self._Sseg, pad) < pad * (1.0 - 1e-6)
        return False

    def _min_dist(self, center, S, reach):
        """Smallest outline-to-outline distance (only pairs within `reach` matter)."""
        if len(S) == 0 or len(self.mov_segs0) == 0:
            return np.inf
        M = self.mov_segs0 + center
        mmn, mmx = M.reshape(-1, 2).min(0) - reach, M.reshape(-1, 2).max(0) + reach
        smn, smx = S.min(axis=1), S.max(axis=1)
        k = (smn[:, 0] <= mmx[0]) & (smx[:, 0] >= mmn[0]) & (smn[:, 1] <= mmx[1]) & (smx[:, 1] >= mmn[1])
        if not k.any():
            return np.inf
        S = S[k]
        lo, hi = S.reshape(-1, 2).min(0) - reach, S.reshape(-1, 2).max(0) + reach
        a, b = M.min(axis=1), M.max(axis=1)
        k2 = (a[:, 0] <= hi[0]) & (b[:, 0] >= lo[0]) & (a[:, 1] <= hi[1]) & (b[:, 1] >= lo[1])
        if not k2.any():
            return np.inf
        M = M[k2]
        Vm = np.unique(M.reshape(-1, 2), axis=0)
        Vs = np.unique(S.reshape(-1, 2), axis=0)
        return min(_pt_seg_dist(Vm, S).min(), _pt_seg_dist(Vs, M).min())

    def too_close_ids(self, center):
        """Static islands already closer than the padding (ignored so you can move away)."""
        pad = self.pad
        if pad <= 0.0 or len(self.static_segs) == 0 or len(self.mov_segs0) == 0:
            return set()
        S, ids = self.all_static_segs, self.all_static_segids
        M = self.mov_segs0 + center
        mmn, mmx = M.reshape(-1, 2).min(0) - pad, M.reshape(-1, 2).max(0) + pad
        smn, smx = S.min(axis=1), S.max(axis=1)
        k = (smn[:, 0] <= mmx[0]) & (smx[:, 0] >= mmn[0]) & (smn[:, 1] <= mmx[1]) & (smx[:, 1] >= mmn[1])
        if not k.any():
            return set()
        S, ids = S[k], ids[k]
        Vm = np.unique(M.reshape(-1, 2), axis=0)
        d1 = _pt_seg_dist(Vm, S).min(axis=0)                     # per static seg
        P = S.reshape(-1, 2)
        d2 = _pt_seg_dist(P, M).min(axis=1).reshape(-1, 2).min(axis=1)
        close = (np.minimum(d1, d2) < pad * (1.0 - 1e-6))
        return set(np.unique(ids[close]).tolist())

    def overlapping_ids(self, center):
        """Static islands overlapping the body right now (full triangles: catches containment too)."""
        ids = set()
        if len(self.static_tris) == 0 or len(self.mov_tris0) == 0:
            return ids
        rb = self.mov_rb
        sa = self.static_aabb
        cand = np.nonzero((sa[:, 0] < rb[2] + center[0]) & (sa[:, 2] > rb[0] + center[0]) &
                          (sa[:, 1] < rb[3] + center[1]) & (sa[:, 3] > rb[1] + center[1]))[0]
        if cand.size == 0:
            return ids
        for _, jj in iter_tri_hits(self.mov_tris0 + center, self.static_tris[cand], sa[cand]):
            ids.update(self.static_ids[cand[jj]].tolist())
        return ids

    def contact_normal(self, free_pos, hit_pos, v):
        """Contact normal for sliding. Primary: direction between the closest points of the two
        outlines at the last free position (exact wall direction, no sideways drift).
        Fallback: minimum-penetration SAT axis at the hit position."""
        n = self._closest_normal(free_pos, v)
        if n is not None:
            return n
        return self._sat_normal(hit_pos, v)

    def _closest_normal(self, c, v):
        S, M = self.all_static_segs, self.mov_segs0
        if len(S) == 0 or len(M) == 0:
            return None
        # ignore islands that collision is currently ignoring (stuck at arm time)
        S = self.static_segs
        if len(S) == 0:
            return None
        g = float(np.hypot(*v)) + 1e-6
        rb = self.mov_rb
        box = (rb[0] + c[0] - g, rb[1] + c[1] - g, rb[2] + c[0] + g, rb[3] + c[1] + g)
        smn, smx = S.min(axis=1), S.max(axis=1)
        sk = (smn[:, 0] <= box[2]) & (smx[:, 0] >= box[0]) & (smn[:, 1] <= box[3]) & (smx[:, 1] >= box[1])
        if not sk.any():
            return None
        S = S[sk]
        Mw = M + c
        U = (S[:, :, 0].min() - g, S[:, :, 1].min() - g, S[:, :, 0].max() + g, S[:, :, 1].max() + g)
        mmn, mmx = Mw.min(axis=1), Mw.max(axis=1)
        mk = (mmn[:, 0] <= U[2]) & (mmx[:, 0] >= U[0]) & (mmn[:, 1] <= U[3]) & (mmx[:, 1] >= U[1])
        if not mk.any():
            return None
        Mw = Mw[mk]

        def pt_seg_dist(P, G):
            a = G[None, :, 0]
            d = G[None, :, 1] - a
            dd = (d * d).sum(-1)
            t = np.clip(((P[:, None] - a) * d).sum(-1) / np.maximum(dd, 1e-30), 0.0, 1.0)
            diff = P[:, None] - (a + t[..., None] * d)
            return np.hypot(diff[..., 0], diff[..., 1])        # (P, G)

        Pm = np.unique(Mw.reshape(-1, 2), axis=0)
        Ps = np.unique(S.reshape(-1, 2), axis=0)
        d1 = pt_seg_dist(Pm, S)          # moving vertices vs static segments
        d2 = pt_seg_dist(Ps, Mw)         # static vertices vs moving segments
        dmin = min(d1.min(), d2.min())
        tol = dmin + 1e-6

        def seg_normals(G, mask):
            used = G[np.nonzero(mask.any(axis=0))[0]]
            if len(used) == 0:
                return np.zeros((0, 2))
            d = used[:, 1] - used[:, 0]
            nn = np.stack([-d[:, 1], d[:, 0]], axis=1)
            return nn / (np.linalg.norm(nn, axis=1, keepdims=True) + 1e-20)

        N = np.concatenate([seg_normals(S, d1 <= tol), seg_normals(Mw, d2 <= tol)])
        if len(N) == 0:
            return None
        vhat = v / (np.hypot(*v) + 1e-30)
        dots = N @ vhat
        N = N * (-np.sign(dots))[:, None]          # face against the motion
        N = N[np.abs(dots) > 1e-6]                 # edges parallel to the motion don't block
        if len(N) == 0:
            return None
        n = N.sum(axis=0)
        ln = np.hypot(*n)
        if ln < 1e-12:
            return None
        return n / ln

    def _sat_normal(self, center, v):
        for ii, jj in self._pair_hits(center):
            A = self._M[ii] + center
            B = self._S[jj]
            ea = np.roll(A, -1, axis=1) - A
            eb = np.roll(B, -1, axis=1) - B
            axes = np.concatenate([np.stack([-ea[..., 1], ea[..., 0]], -1),
                                   np.stack([-eb[..., 1], eb[..., 0]], -1)], axis=1)
            axes /= (np.linalg.norm(axes, axis=-1, keepdims=True) + 1e-20)
            pa = np.einsum('pkd,pvd->pkv', axes, A)
            pb = np.einsum('pkd,pvd->pkv', axes, B)
            ov = np.minimum(pa.max(2) - pb.min(2), pb.max(2) - pa.min(2))
            k = ov.argmin(axis=1)
            P = np.arange(len(k))
            n = axes[P, k]
            sgn = np.sign(pa[P, k].mean(1) - pb[P, k].mean(1))
            sgn[sgn == 0] = 1.0
            n = (n * sgn[:, None]).sum(axis=0)
            ln = np.hypot(*n)
            if ln < 1e-12:
                return None
            n /= ln
            return n if np.dot(n, v) < 0 else None
        return None

    def in_bounds(self, center):
        rb, e = self.mov_rb, 1e-9
        return (rb[0] + center[0] >= -e and rb[2] + center[0] <= 1.0 + e and
                rb[1] + center[1] >= -e and rb[3] + center[1] <= 1.0 + e)

    def clamp_center(self, center):
        rb = self.mov_rb
        c = center.copy()
        if rb[2] - rb[0] <= 1.0:
            c[0] = min(max(c[0], -rb[0]), 1.0 - rb[2])
        if rb[3] - rb[1] <= 1.0:
            c[1] = min(max(c[1], -rb[1]), 1.0 - rb[3])
        return c

    def _ccd(self, p, v, S):
        """Exact time of first contact when the body translates from p by v (outline vs outline).
        Returns (t in [0,1], normal) or (None, None). Touching contacts at t≈0 are ignored here
        (the overlap check in advance() decides those)."""
        if len(S) == 0 or len(self.mov_segs0) == 0:
            return None, None
        M = self.mov_segs0 + p
        # cull moving segments to the region the static segments occupy (+ sweep)
        g = np.abs(v) + self.pad
        smn = S.reshape(-1, 2).min(0) - g - 1e-9
        smx = S.reshape(-1, 2).max(0) + g + 1e-9
        mmn, mmx = M.min(axis=1), M.max(axis=1)
        keep = (mmn[:, 0] <= smx[0]) & (mmx[:, 0] >= smn[0]) & (mmn[:, 1] <= smx[1]) & (mmx[:, 1] >= smn[1])
        M = M[keep]
        if len(M) == 0:
            return None, None

        def cr(a, b):
            return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]

        def rays(O, d, G):
            # rays O + t*d (t in (0,1]) against segments G: returns t (O,G) or inf
            a = G[None, :, 0]
            e = G[None, :, 1] - a
            denom = cr(np.broadcast_to(d, e.shape), e)
            ao = a - O[:, None]
            with np.errstate(divide='ignore', invalid='ignore'):
                t = cr(ao, e) / denom
                u = cr(ao, np.broadcast_to(d, e.shape)) / denom
            ok = (np.abs(denom) > 1e-18) & (t > 1e-9) & (t <= 1.0) & (u >= -1e-9) & (u <= 1.0 + 1e-9)
            return np.where(ok, t, np.inf)

        Vm = np.unique(M.reshape(-1, 2), axis=0)
        Vs = np.unique(S.reshape(-1, 2), axis=0)
        if self.pad > 0.0:
            return self._ccd_pad(Vm, Vs, M, S, v, rays)
        t1 = rays(Vm, v, S)          # moving vertices into static edges
        t2 = rays(Vs, -v, M)         # static vertices into moving edges
        tmin = min(t1.min() if t1.size else np.inf, t2.min() if t2.size else np.inf)
        if not np.isfinite(tmin):
            return None, None

        # normal: average of all edges hit at (nearly) the first contact time
        tol = tmin + 1e-7
        segs = []
        if t1.size:
            segs.append(S[np.nonzero((t1 <= tol).any(axis=0))[0]])
        if t2.size:
            segs.append(M[np.nonzero((t2 <= tol).any(axis=0))[0]])
        G = np.concatenate(segs)
        d = G[:, 1] - G[:, 0]
        N = np.stack([-d[:, 1], d[:, 0]], axis=1)
        N /= (np.linalg.norm(N, axis=1, keepdims=True) + 1e-20)
        N *= -np.sign(N @ v)[:, None]
        n = N.sum(axis=0)
        ln = np.hypot(*n)
        return tmin, (n / ln if ln > 1e-12 else None)

    def _ccd_pad(self, Vm, Vs, M, S, v, rays):
        """First time the outlines come within `pad` of each other (edge capsules)."""
        pad = self.pad

        def offset(G):
            d = G[:, 1] - G[:, 0]
            n = np.stack([-d[:, 1], d[:, 0]], axis=1)
            n /= (np.linalg.norm(n, axis=1, keepdims=True) + 1e-20)
            off = n[:, None, :] * pad
            return np.concatenate([G + off, G - off]), np.concatenate([n, n])

        hits_t, hits_n = [], []
        # moving vertices vs offset static edges
        So, Sn = offset(S)
        t = rays(Vm, v, So)
        if t.size:
            hits_t.append(t.min(axis=0)); hits_n.append(Sn)
        # static vertices vs offset moving edges
        Mo, Mn = offset(M)
        t = rays(Vs, -v, Mo)
        if t.size:
            hits_t.append(t.min(axis=0)); hits_n.append(Mn)
        # moving vertices vs circles around static vertices (corner rounding)
        oc = Vm[:, None] - Vs[None]                                  # (m, s, 2)
        a = float(v @ v)
        b = 2.0 * (oc @ v)
        c0 = (oc * oc).sum(-1) - pad * pad
        disc = b * b - 4 * a * c0
        with np.errstate(invalid='ignore'):
            tc = (-b - np.sqrt(np.maximum(disc, 0.0))) / (2 * a)
        ok = (disc >= 0) & (c0 > 0) & (tc > 1e-9) & (tc <= 1.0)
        tc = np.where(ok, tc, np.inf)
        if tc.size:
            i, j = np.unravel_index(np.argmin(tc), tc.shape)
            if np.isfinite(tc[i, j]):
                hp = Vm[i] + v * tc[i, j]
                hits_t.append(np.array([tc[i, j]])); hits_n.append(((hp - Vs[j]) / pad)[None])

        if not hits_t:
            return None, None
        T = np.concatenate(hits_t)
        N = np.concatenate(hits_n)
        tmin = T.min()
        if not np.isfinite(tmin):
            return None, None
        N = N[T <= tmin + 1e-7]
        N = N * (-np.sign(N @ v))[:, None]
        n = N.sum(axis=0)
        ln = np.hypot(*n)
        return tmin, (n / ln if ln > 1e-12 else None)

    def advance(self, p, v):
        """Move from p along v until first contact. Returns (new_p, normal or None, blocked)."""
        L = float(np.hypot(*v))
        if L < 1e-15:
            return p, None, False
        t, n = self._ccd(p, v, self._Sseg)
        if t is None:
            q = p + v
        else:
            back = SKIN / L
            q = p + v * max(0.0, t - back)
        if not self.collides(q):
            return q, n, t is not None
        # Touching/numerical edge case: fall back to bisection on the overlap test
        lo, hi = 0.0, (t if t is not None else 1.0)
        for _ in range(BISECT_STEPS):
            mid = (lo + hi) * 0.5
            if self.collides(p + v * mid):
                hi = mid
            else:
                lo = mid
        q = p + v * lo
        if n is None:
            n = self._closest_normal(q, v)
        if n is None:
            n = self._sat_normal(p + v * hi, v)
        return q, n, True

    def slide(self, p, v):
        """Collide-and-slide along contact edges (any angle). After each contact the island
        heads for the point on the contact edge nearest the cursor, so it never overshoots."""
        target = p + v
        budget = float(np.hypot(*v))
        lock = getattr(self, 'lock_axis', None)
        pos, rem = p, v
        for _ in range(SLIDE_ITERS):
            if budget < 1e-12 or np.abs(rem).max() < 1e-12:
                break
            q, n, blocked = self.advance(pos, rem)
            budget -= float(np.hypot(*(q - pos)))
            pos = q
            if not blocked:
                break
            d = target - pos
            if n is None:
                # unknown contact direction: try the axis components
                q2, _, _ = self.advance(pos, np.array([d[0], 0.0]))
                if lock != 0:
                    q2, _, _ = self.advance(q2, np.array([0.0, d[1]]))
                return q2
            d = d - np.dot(d, n) * n
            if np.dot(d, n) < 0:
                d = d - np.dot(d, n) * n
            if lock is not None:
                d[1 - lock] = 0.0      # axis-locked move: never slide off the locked axis
            L = float(np.hypot(*d))
            if L > budget:
                d *= budget / L
            rem = d
        return pos

    def snap_center(self, context, c):
        rx, ry = get_resolution(context)
        rb = self.mov_rb
        return np.array([round((rb[0] + c[0]) * rx) / rx - rb[0],
                         round((rb[1] + c[1]) * ry) / ry - rb[1]])

    def solve(self, context, target, collide, snap):
        """Move the body toward `target` (anchor center).

        The physics runs on an internal, unsnapped position (`raw`), so pixel snapping only
        affects what is displayed and never feeds back into the collision (no flicker between
        snapped / unsnapped positions). Blocked moves only ever bring the island closer to the
        cursor, which removes back-and-forth jitter at corners and bumpy edges."""
        bounds = context.scene.uv_solid_constrain_bounds
        newpad = get_padding(context)
        if newpad != self.pad:
            self.pad = newpad
            if collide and getattr(self, '_armed', False):
                self.arm_collision()

        raw = getattr(self, 'raw', None)
        if raw is None or getattr(self, '_raw_anchor', None) != self.anchor:
            raw = self.body_center()
        if bounds:
            target = self.clamp_center(target)

        if not collide:
            raw = target.copy()
        else:
            total = target - raw
            dist = float(np.hypot(*total))
            if dist > 1e-15:
                if not self.prepare_sweep(raw, dist):
                    new = target.copy()                 # nothing in reach: free move
                else:
                    new = self.slide(raw, total)        # exact sweep: no sub-steps needed
                    # hysteresis: a blocked move must get closer to the cursor, else stay put
                    if float(np.hypot(*(new - target))) >= dist - 1e-9:
                        new = raw
                    self.use_full()
                raw = new

        self.raw = raw
        self._raw_anchor = self.anchor

        c = raw
        if snap:
            c = self.snap_display(context, raw, collide, bounds)
        self.set_body_center(c)

    def snap_display(self, context, raw, collide, bounds):
        """Nearest valid pixel-grid position to `raw` (checks a 3x3 neighbourhood).
        Keeps the previous snapped position if none is valid, so it never flickers."""
        rx, ry = get_resolution(context)
        rb = self.mov_rb
        bx, by = (rb[0] + raw[0]) * rx, (rb[1] + raw[1]) * ry
        base = np.array([round(bx), round(by)])
        cands = []
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                px, py = base[0] + dx, base[1] + dy
                cpos = np.array([px / rx - rb[0], py / ry - rb[1]])
                cands.append((float(np.hypot(px - bx, py - by)), cpos))
        cands.sort(key=lambda t: t[0])
        for _, cpos in cands:
            if bounds and not self.in_bounds(cpos):
                continue
            if collide and self.collides(cpos):
                continue
            self._last_snap = cpos
            return cpos
        last = getattr(self, '_last_snap', None)
        if last is not None and not (collide and self.collides(last)):
            return last
        return raw

    # ------------------------------------------------------------ draw ----

    def draw_overlay(self, context):
        try:
            v2d = context.region.view2d
            o = np.array(v2d.view_to_region(0.0, 0.0, clip=False), dtype=np.float64)
            s = np.array(v2d.view_to_region(1.0, 1.0, clip=False), dtype=np.float64) - o

            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            shader.bind()
            gpu.state.blend_set('ALPHA')
            gpu.state.line_width_set(1.0)

            if context.scene.uv_solid_constrain_bounds:
                frame = [tuple(o + s * np.array(c)) for c in ((0, 0), (1, 0), (1, 1), (0, 1))]
                shader.uniform_float("color", (1.0, 0.8, 0.2, 0.4))
                batch_for_shader(shader, 'LINE_LOOP', {"pos": frame}).draw(shader)

            collide_on = getattr(self, 'collision_active', True)
            grouping = getattr(self, 'group_active', context.scene.uv_solid_group_overlaps)
            active_set = set(self.members)
            buckets = {'static': [], 'group': [], 'active': []}
            for i, isl in enumerate(self.islands):
                if len(isl['boundary']) == 0:
                    continue
                if i in active_set:
                    key = 'active'
                elif grouping and self.group_of is not None and len(self.group_of[i]) > 1:
                    key = 'group'
                elif collide_on:
                    key = 'static'
                else:
                    continue
                buckets[key].append(isl['offsets'][isl['boundary']] + isl['center'])

            styles = (('static', (0.8, 0.8, 0.8, 0.35), 1.0),
                      ('group', (1.0, 0.55, 0.2, 0.6), 1.0),
                      ('active', (0.2, 0.8, 1.0, 0.95) if collide_on else (0.6, 0.6, 0.6, 0.9), 2.0))
            for key, color, width in styles:
                if not buckets[key]:
                    continue
                segs = np.concatenate(buckets[key]).reshape(-1, 2) * s + o
                gpu.state.line_width_set(width)
                shader.uniform_float("color", color)
                batch_for_shader(shader, 'LINES', {"pos": segs.tolist()}).draw(shader)
            gpu.state.line_width_set(1.0)

            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def add_draw_handler(self, context):
        self._handle = bpy.types.SpaceImageEditor.draw_handler_add(
            self.draw_overlay, (context,), 'WINDOW', 'POST_PIXEL')

    def add_timer(self, context):
        self._timer = context.window_manager.event_timer_add(1.0 / 60.0, window=context.window)

    def remove_timer(self, context):
        if getattr(self, '_timer', None):
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def remove_draw_handler(self):
        if getattr(self, '_handle', None):
            bpy.types.SpaceImageEditor.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None


def _poll_uv_edit(context):
    obj = context.active_object
    return (context.area is not None and context.area.type == 'IMAGE_EDITOR' and
            context.mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH' and
            obj.data.uv_layers.active is not None)


# ---------------------------------------------------------------------------
# G replacement: collision-aware Move
# ---------------------------------------------------------------------------

NUM_KEYS = {
    'ZERO': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4',
    'FIVE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9',
    'NUMPAD_0': '0', 'NUMPAD_1': '1', 'NUMPAD_2': '2', 'NUMPAD_3': '3', 'NUMPAD_4': '4',
    'NUMPAD_5': '5', 'NUMPAD_6': '6', 'NUMPAD_7': '7', 'NUMPAD_8': '8', 'NUMPAD_9': '9',
    'PERIOD': '.', 'NUMPAD_PERIOD': '.',
}


class UV_OT_collision_translate(CollisionCore, bpy.types.Operator):
    """Move selected UV islands. With UV Collision on (or Alt held) islands collide and slide"""
    bl_idname = "uv.collision_translate"
    bl_label = "Move (UV Collision)"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return _poll_uv_edit(context)

    def invoke(self, context, event):
        scene = context.scene
        if not scene.uv_collision_addon_enabled:
            return {'PASS_THROUGH'}
        prefs = addon_prefs(context)
        alt_anytime = prefs.alt_enables_collision if prefs else True

        # Hand over to Blender's own Move when our version adds nothing
        if not scene.uv_collision_enabled:
            if not alt_anytime or context.tool_settings.use_proportional_edit:
                return {'PASS_THROUGH'}

        t = time.perf_counter()
        self.load_islands(context)
        print(f"[UV Collision] load islands: {(time.perf_counter() - t) * 1000:.1f} ms "
              f"({len(self.islands)} islands)")
        touched = [i for i, isl in enumerate(self.islands) if isl['sel'] != 'none']
        if not touched:
            return {'PASS_THROUGH'}
        if any(self.islands[i]['sel'] == 'some' for i in touched):
            # Partial islands (vertex tweaking) -> normal Move
            return {'PASS_THROUGH'}

        self.touched = touched
        self.ctrl = event.ctrl
        self.group_active = scene.uv_solid_group_overlaps != self.ctrl
        self.begin_body(self.expand_groups(touched, self.group_active))
        self.start_center = self.body_center()

        v2d = context.region.view2d
        self.last_mouse = np.array(v2d.region_to_view(event.mouse_region_x, event.mouse_region_y))
        self.mouse_offset = np.zeros(2)
        self.axis = None            # None, 0 (X) or 1 (Y)
        self.num = ['', '']         # numeric input buffers
        self.num_idx = 0
        self.alt = event.alt
        self.shift = event.shift
        self.collision_active = self.want_collision(context)
        if not self.collision_active:
            self.disarm_collision()

        self.dirty = False
        self.last_cost = 0.0
        self.stats = [0, 0.0, 0.0, 0.0]   # updates, solve s, apply s, worst s
        self.add_draw_handler(context)
        context.window_manager.modal_handler_add(self)
        self.add_timer(context)
        self.refresh(context)
        return {'RUNNING_MODAL'}

    # ---------------------------------------------------------- state ----

    def want_collision(self, context):
        return context.scene.uv_collision_enabled != self.alt

    def want_snap(self, context):
        return context.scene.uv_solid_pixel_snap

    def regroup(self, context):
        """Ctrl toggles overlap grouping; rebuild the moving body with the same offset."""
        want = context.scene.uv_solid_group_overlaps != self.ctrl
        if want == self.group_active:
            return
        self.group_active = want
        self.restore_members()
        self.begin_body(self.expand_groups(self.touched, want))
        self.start_center = self.body_center()
        self.collision_active = self.want_collision(context)
        if not self.collision_active:
            self.disarm_collision()

    def numeric_active(self):
        return any(self.num)

    def parse_num(self):
        out = np.zeros(2)
        for i, s in enumerate(self.num):
            if s in ('', '-', '.', '-.'):
                continue
            try:
                out[i] = float(s)
            except ValueError:
                pass
        return out

    def offset(self):
        off = self.parse_num() if self.numeric_active() else self.mouse_offset.copy()
        if self.axis is not None:
            if self.numeric_active():
                off = np.array([off[0], 0.0]) if self.axis == 0 else np.array([0.0, off[0]])
            else:
                off[1 - self.axis] = 0.0
        return off

    def refresh(self, context):
        want = self.want_collision(context)
        if want and not self.collision_active:
            self.arm_collision()
        self.collision_active = want

        target = self.start_center + self.offset()
        self.lock_axis = self.axis
        t0 = time.perf_counter()
        self.solve(context, target, collide=self.collision_active, snap=self.want_snap(context))
        t1 = time.perf_counter()
        moved = self.apply_members()
        t2 = time.perf_counter()
        self.last_cost = t2 - t0
        st = self.stats
        st[0] += 1; st[1] += t1 - t0; st[2] += t2 - t1; st[3] = max(st[3], t2 - t0)
        self.update_header(context)
        if moved:
            context.area.tag_redraw()

    def update_header(self, context):
        d = self.body_center() - self.start_center
        if self.numeric_active():
            parts = []
            for i in range(2):
                txt = self.num[i] or '0'
                parts.append(f"[{txt}|]" if i == self.num_idx else txt)
            dtxt = f"D: {parts[0]}  {parts[1]}"
        else:
            dtxt = f"D: {d[0]:.4f}  {d[1]:.4f}"
        axis = {None: '', 0: '  along X', 1: '  along Y'}[self.axis]
        col = "ON" if self.collision_active else "OFF"
        snap = "ON" if self.want_snap(context) else "OFF"
        bnd = "ON" if context.scene.uv_solid_constrain_bounds else "OFF"
        grp = f"ON ({len(self.members)} islands)" if self.group_active else "OFF"
        context.area.header_text_set(
            f"{dtxt}{axis}   |   Collision [{col}] (Alt)   Pixel Snap [{snap}] (S)   "
            f"Group Overlaps [{grp}] (V / hold Ctrl)   Bounds [{bnd}] (B)")

    # ---------------------------------------------------------- modal ----

    def modal(self, context, event):
        et, ev = event.type, event.value

        # Modifier tracking (the modifier's own press/release event is authoritative)
        if et in {'LEFT_ALT', 'RIGHT_ALT'}:
            self.alt = (ev == 'PRESS')
        elif et in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.ctrl = (ev == 'PRESS')
        elif et in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self.shift = (ev == 'PRESS')
        else:
            self.alt, self.ctrl, self.shift = event.alt, event.ctrl, event.shift

        if et in {'LEFT_ALT', 'RIGHT_ALT', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.regroup(context)
            self.refresh(context)
            return {'RUNNING_MODAL'}

        if et == 'MOUSEMOVE':
            v2d = context.region.view2d
            cur = np.array(v2d.region_to_view(event.mouse_region_x, event.mouse_region_y))
            self.mouse_offset += (cur - self.last_mouse) * (0.1 if self.shift else 1.0)
            self.last_mouse = cur
            if not self.numeric_active():
                if self.last_cost < SLOW_UPDATE:
                    self.refresh(context)      # fast enough: respond immediately
                else:
                    self.dirty = True          # slow update: coalesce on the timer (no backlog)
            return {'RUNNING_MODAL'}

        if et == 'TIMER':
            if self.dirty:
                self.dirty = False
                self.refresh(context)
            return {'RUNNING_MODAL'}

        if ev != 'PRESS':
            return {'RUNNING_MODAL'}

        if et in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'}:
            return self.finish(context, confirm=True)

        if et in {'ESC', 'RIGHTMOUSE'}:
            return self.finish(context, confirm=False)

        if et in {'X', 'Y'}:
            a = 0 if et == 'X' else 1
            self.axis = None if self.axis == a else a
            self.refresh(context)
            return {'RUNNING_MODAL'}

        if et == 'B':
            context.scene.uv_solid_constrain_bounds = not context.scene.uv_solid_constrain_bounds
            self.refresh(context)
            return {'RUNNING_MODAL'}

        if et == 'S':
            context.scene.uv_solid_pixel_snap = not context.scene.uv_solid_pixel_snap
            self.refresh(context)
            return {'RUNNING_MODAL'}

        if et == 'V':
            context.scene.uv_solid_group_overlaps = not context.scene.uv_solid_group_overlaps
            self.regroup(context)
            self.refresh(context)
            return {'RUNNING_MODAL'}

        # Numeric input
        if et in NUM_KEYS:
            ch = NUM_KEYS[et]
            buf = self.num[self.num_idx]
            if not (ch == '.' and '.' in buf):
                self.num[self.num_idx] = buf + ch
            self.refresh(context)
            return {'RUNNING_MODAL'}
        if et in {'MINUS', 'NUMPAD_MINUS'}:
            buf = self.num[self.num_idx]
            self.num[self.num_idx] = buf[1:] if buf.startswith('-') else '-' + buf
            self.refresh(context)
            return {'RUNNING_MODAL'}
        if et == 'BACK_SPACE':
            self.num[self.num_idx] = self.num[self.num_idx][:-1]
            self.refresh(context)
            return {'RUNNING_MODAL'}
        if et == 'TAB' and self.axis is None:
            self.num_idx = 1 - self.num_idx
            self.refresh(context)
            return {'RUNNING_MODAL'}

        if et in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPAD_PAN', 'TRACKPAD_ZOOM'}:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def finish(self, context, confirm):
        if confirm and self.dirty:
            self.refresh(context)
        if not confirm:
            self.restore_members()
        self.remove_timer(context)
        self.remove_draw_handler()
        n, ts, ta, worst = self.stats
        if n:
            print(f"[UV Collision] move: {n} updates, solve avg {ts / n * 1000:.2f} ms, "
                  f"write avg {ta / n * 1000:.2f} ms, worst {worst * 1000:.1f} ms")
        context.area.header_text_set(None)
        context.area.tag_redraw()
        return {'FINISHED'} if confirm else {'CANCELLED'}


# ---------------------------------------------------------------------------
# Click-and-drag tool (from earlier versions)
# ---------------------------------------------------------------------------

class UVSolidOperator(CollisionCore, bpy.types.Operator):
    """Click and drag individual UV islands with collision"""
    bl_idname = "uv.solid_drag"
    bl_label = "UV Solid Drag"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_uv_edit(context)

    def pick_island(self, uv):
        p = np.array([uv.x, uv.y])
        for i, isl in enumerate(self.islands):
            rb = isl['rel_bounds'] + np.tile(isl['center'], 2)
            if not (rb[0] <= p[0] <= rb[2] and rb[1] <= p[1] <= rb[3]):
                continue
            if len(isl['tris0']) and point_in_tris(p, isl['tris0'] + isl['center']):
                return i
        return -1

    def update_status(self, context):
        scene = context.scene
        rx, ry = get_resolution(context)
        b = "ON" if scene.uv_solid_constrain_bounds else "OFF"
        s = f"ON ({rx}x{ry})" if scene.uv_solid_pixel_snap else "OFF"
        if scene.uv_solid_group_overlaps:
            self.ensure_groups()
        g = f"ON ({self.overlap_group_count} groups)" if scene.uv_solid_group_overlaps else "OFF"
        context.workspace.status_text_set(
            f"Solid Drag: LMB drag (hold Alt: no collision) | B: Bounds [{b}] | S: Pixel Snap [{s}] | "
            f"V: Group Overlaps [{g}] (or hold Ctrl on click) | Enter: Confirm | Esc/RMB: Cancel")

    def invoke(self, context, event):
        self._handle = None
        self.dragging = False
        self.load_islands(context)
        if not self.islands:
            self.report({'WARNING'}, "No UV islands found")
            return {'CANCELLED'}
        self.touched = set()
        self.group_active = context.scene.uv_solid_group_overlaps
        self.pending = None
        self.last_mouse_target = None
        self.collision_active = True
        self.last_cost = 0.0
        self.add_draw_handler(context)
        self.update_status(context)
        context.window_manager.modal_handler_add(self)
        self.add_timer(context)
        return {'RUNNING_MODAL'}

    def set_alt(self, alt):
        """Alt held = bypass collision. Re-arm (ignoring current overlaps) when released."""
        want = not alt
        if self.dragging and want and not self.collision_active:
            self.arm_collision()
        self.collision_active = want

    def regroup_drag(self, context, group):
        if group == self.group_active:
            return
        self.group_active = group
        # put the current body back where it was at click time, rebuild, then re-apply the drag
        for m in self.members:
            self.islands[m]['center'] = self.click_centers[m].copy()
        self.apply_members(force=True)
        collide = self.collision_active
        self.begin_body(self.expand_groups([self.picked], group))
        self.touched.update(self.members)
        self.collision_active = True
        if not collide:
            self.disarm_collision()
            self.collision_active = False
        self.grab = self.click_mouse - self.body_center()
        if self.last_mouse_target is not None:
            # last_mouse_target was relative to the old grab; recompute from the mouse
            self.pending = self.last_mouse_pos - self.grab
            self.last_mouse_target = self.pending
            self.flush_pending(context)
        else:
            self.apply_members(force=True)

    def flush_pending(self, context):
        if self.pending is not None and self.dragging:
            t0 = time.perf_counter()
            self.solve(context, self.pending, collide=self.collision_active,
                       snap=context.scene.uv_solid_pixel_snap)
            if self.apply_members():
                context.area.tag_redraw()
            self.last_cost = time.perf_counter() - t0
        self.pending = None

    def modal(self, context, event):
        scene = context.scene
        if event.type in {'LEFT_ALT', 'RIGHT_ALT'}:
            self.set_alt(event.value == 'PRESS')
            if self.dragging and self.last_mouse_target is not None:
                self.pending = self.last_mouse_target
                self.flush_pending(context)
            self.update_status(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER':
            if self.pending is not None:
                self.flush_pending(context)
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        context.area.tag_redraw()

        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPAD_PAN', 'TRACKPAD_ZOOM'}:
            return {'PASS_THROUGH'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self.pending = None
            self.members = list(self.touched)
            self.restore_members()
            return self.cleanup(context, {'CANCELLED'})

        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self.cleanup(context, {'FINISHED'})

        if not self.dragging and event.value == 'PRESS':
            if event.type == 'B':
                scene.uv_solid_constrain_bounds = not scene.uv_solid_constrain_bounds
            elif event.type == 'S':
                scene.uv_solid_pixel_snap = not scene.uv_solid_pixel_snap
            elif event.type in {'G', 'V'}:
                scene.uv_solid_group_overlaps = not scene.uv_solid_group_overlaps
            self.update_status(context)

        # V while dragging: toggle grouping live (rebuild the body, keep the drag going)
        if self.dragging and event.type == 'V' and event.value == 'PRESS':
            scene.uv_solid_group_overlaps = not scene.uv_solid_group_overlaps
            self.regroup_drag(context, scene.uv_solid_group_overlaps)
            self.update_status(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        v2d = context.region.view2d
        mouse = np.array(v2d.region_to_view(event.mouse_region_x, event.mouse_region_y))

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            i = self.pick_island(Vector(mouse))
            if i >= 0:
                self.group_active = scene.uv_solid_group_overlaps != event.ctrl
                self.picked = i
                self.click_mouse = mouse.copy()
                self.click_centers = [isl['center'].copy() for isl in self.islands]
                self.begin_body(self.expand_groups([i], self.group_active))
                self.touched.update(self.members)
                self.grab = mouse - self.body_center()
                self.dragging = True
                self.collision_active = True
                self.set_alt(event.alt)
            return {'RUNNING_MODAL'}

        if self.dragging and event.type == 'MOUSEMOVE':
            if event.alt != (not self.collision_active):
                self.set_alt(event.alt)
            self.pending = mouse - self.grab
            self.last_mouse_target = self.pending
            self.last_mouse_pos = mouse.copy()
            if self.last_cost < SLOW_UPDATE:
                self.flush_pending(context)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.flush_pending(context)
            self.dragging = False
            self.members = []
        return {'RUNNING_MODAL'}

    def cleanup(self, context, result):
        if result == {'FINISHED'}:
            self.flush_pending(context)
        self.remove_timer(context)
        self.remove_draw_handler()
        context.workspace.status_text_set(None)
        return result


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def draw_header_toggle(self, context):
    if context.mode != 'EDIT_MESH':
        return
    sima = context.space_data
    if getattr(sima, 'show_uvedit', True) is False:
        return
    row = self.layout.row(align=True)
    row.prop(context.scene, "uv_collision_addon_enabled", text="", icon='SNAP_ON')
    sub = row.row(align=True)
    sub.enabled = context.scene.uv_collision_addon_enabled
    sub.prop(context.scene, "uv_collision_enabled", text="", icon='MOD_PHYSICS')


class UVSolidPanel(bpy.types.Panel):
    bl_label = "UV Collision"
    bl_idname = "UV_PT_solid_drag"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "UV Collision"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "uv_collision_addon_enabled",
                    text="Enable UV Collision Move (G)", toggle=True,
                    icon='CHECKBOX_HLT' if scene.uv_collision_addon_enabled else 'CHECKBOX_DEHLT')
        body = layout.column()
        body.enabled = scene.uv_collision_addon_enabled
        if not scene.uv_collision_addon_enabled:
            layout.label(text="Off: G uses Blender's native Move", icon='INFO')

        top = body.column(align=True)
        top.prop(scene, "uv_collision_enabled", text="Collision on Move (G)")
        top.prop(scene, "uv_solid_group_overlaps", text="Move Overlapping Together (V)")
        col = body.column(align=True)
        col.prop(scene, "uv_solid_constrain_bounds", text="Constrain to Bounds (0 - 1)")
        col.prop(scene, "uv_solid_pixel_snap", text="Pixel Snapping (S)")

        pbox = body.column(align=True)
        pbox.label(text="Padding (gap between islands):")
        pbox.row(align=True).prop(scene, "uv_solid_padding_mode", expand=True)
        if scene.uv_solid_padding_mode == 'PIXELS':
            pbox.prop(scene, "uv_solid_padding_px", text="Pixels")
        elif scene.uv_solid_padding_mode == 'PERCENT':
            pbox.prop(scene, "uv_solid_padding_pct", text="Percent of UV")

        sima = context.space_data
        if sima and sima.type == 'IMAGE_EDITOR' and sima.image:
            w, h = sima.image.size
            col.label(text=f"Detected Texture: {w} x {h}", icon='IMAGE_DATA')
        else:
            col.prop(scene, "uv_solid_tex_res", text="Texture Res")

        body.separator()
        body.operator("uv.solid_drag", text="Click-Drag Tool", icon='SNAP_ON')

        box = body.box()
        box.label(text="While moving (G):")
        box.label(text="• Alt: toggle collision")
        box.label(text="• S: toggle pixel snap")
        box.label(text="• V: toggle move overlapping together")
        box.label(text="• Ctrl (hold): invert overlapping together")
        box.label(text="• Shift: precision")
        box.label(text="• X / Y: lock axis, type numbers")
        box.label(text="• B: toggle bounds")


class UVCollisionPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    alt_enables_collision: bpy.props.BoolProperty(
        name="Alt can turn collision on",
        description=("Use the collision Move for G even when the toggle is off, so holding Alt "
                     "can switch collision on mid-move. Turn off to get Blender's own Move "
                     "whenever the toggle is off"),
        default=True,
    )

    def draw(self, context):
        self.layout.prop(self, "alt_enables_collision")
        self.layout.label(text="Partial-island selections and proportional editing always use Blender's Move.")


classes = (UVCollisionPreferences, UV_OT_collision_translate, UVSolidOperator, UVSolidPanel)


def register():
    for c in classes:
        bpy.utils.register_class(c)

    S = bpy.types.Scene
    S.uv_collision_addon_enabled = bpy.props.BoolProperty(
        name="Enable UV Collision Move",
        description="Replace G in the UV Editor with the collision-aware Move. "
                    "When off, Blender's native Move is used and this add-on does nothing",
        default=False,
    )
    S.uv_collision_enabled = bpy.props.BoolProperty(
        name="UV Collision",
        description="Islands collide and slide against each other when moved with G (hold Alt to invert)",
        default=False,
    )
    S.uv_solid_constrain_bounds = bpy.props.BoolProperty(
        name="Constrain to Bounds",
        description="Keep UV islands inside the 0.0 - 1.0 UV image space",
        default=True,
    )
    S.uv_solid_pixel_snap = bpy.props.BoolProperty(
        name="Pixel Snapping",
        description="Snap island position to the pixel grid (press S while moving to toggle)",
        default=False,
    )
    S.uv_solid_group_overlaps = bpy.props.BoolProperty(
        name="Move Overlapping Together",
        description="Islands that overlap each other (even through a chain) are moved as one body (V toggles while moving, hold Ctrl to invert)",
        default=False,
    )
    S.uv_solid_padding_mode = bpy.props.EnumProperty(
        name="Padding",
        items=[('NONE', "None", "Islands can touch"),
               ('PIXELS', "Pixels", "Minimum gap in texture pixels"),
               ('PERCENT', "Percent", "Minimum gap as a percentage of the UV tile")],
        default='NONE',
    )
    S.uv_solid_padding_px = bpy.props.FloatProperty(
        name="Padding (px)", description="Minimum gap between islands, in pixels",
        default=4.0, min=0.0, soft_max=64.0, step=100, precision=1,
    )
    S.uv_solid_padding_pct = bpy.props.FloatProperty(
        name="Padding (%)", description="Minimum gap between islands, as % of the UV tile",
        default=0.5, min=0.0, soft_max=10.0, step=10, precision=2, subtype='PERCENTAGE',
    )
    S.uv_solid_tex_res = bpy.props.IntProperty(
        name="Texture Res",
        description="Fallback texture resolution if no image is loaded in UV editor",
        default=1024, min=16, max=16384,
    )

    bpy.types.IMAGE_HT_header.append(draw_header_toggle)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="UV Editor", space_type='EMPTY')
        kmi = km.keymap_items.new(UV_OT_collision_translate.bl_idname, 'G', 'PRESS')
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.types.IMAGE_HT_header.remove(draw_header_toggle)

    S = bpy.types.Scene
    for p in ("uv_collision_addon_enabled", "uv_collision_enabled", "uv_solid_constrain_bounds", "uv_solid_pixel_snap",
              "uv_solid_group_overlaps", "uv_solid_tex_res",
              "uv_solid_padding_mode", "uv_solid_padding_px", "uv_solid_padding_pct"):
        delattr(S, p)

    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == '__main__':
    register()
