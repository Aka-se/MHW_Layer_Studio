"""Connected-component splitting; copy opaque vertex records without re-encoding."""
import struct
import math
from itertools import product
import numpy as np
from mod3 import BASE_HEADER, MESH_HEADER_SIZE, Mod3Document, Mod3Error


SEAM_TOLERANCE = 1e-5


def components(mesh, mode="seams-v1"):
    if mode not in ("exact", "seams-v1"):
        raise Mod3Error("不支持的拆分算法")
    if not len(mesh.faces):
        return []
    if not np.isfinite(mesh.vertices).all():
        raise Mod3Error("顶点坐标异常，禁止拆分")
    # Weld only the connectivity graph. Export keeps every original position and
    # opaque vertex record, so UV/normal/weight seams are never rewritten.
    positions, welded = np.unique(mesh.vertices, axis=0, return_inverse=True)
    parent = np.arange(len(positions))
    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = int(parent[a])
        return a
    if mode == "seams-v1":
        grid = {}
        positions = positions.astype(np.float64)
        neighbors = tuple(product((-1, 0, 1), repeat=3))
        for i, point in enumerate(positions):
            cell = tuple(math.floor(v / SEAM_TOLERANCE) for v in point)
            for delta in neighbors:
                adjacent = tuple(a + b for a, b in zip(cell, delta))
                for j in grid.get(adjacent, ()):
                    difference = positions[j] - point
                    if float(difference @ difference) <= SEAM_TOLERANCE ** 2:
                        parent[root(i)] = root(j)
            grid.setdefault(cell, []).append(i)
    for a, b, c in welded[mesh.faces]:
        a = root(int(a))
        for other in (b, c):
            other = root(int(other))
            parent[other] = a
    groups = {}
    for index, face in enumerate(welded[mesh.faces]):
        groups.setdefault(root(int(face[0])), []).append(index)
    return [np.asarray(indices, dtype=np.int32) for indices in
            sorted(groups.values(), key=lambda indices: (-len(indices), indices[0]))]


def rebuild_split(document, index, islands):
    if len(islands) < 2:
        raise Mod3Error("此 Mesh 没有可按连通性分离的子部件")
    return rebuild_partitions(document, {index: islands})


def rebuild_partitions(document, partitions_by_mesh):
    """Rebuild a batch of face partitions, preserving every original face once.

    Assigned boxes are copied conservatively; surplus original box records and
    the gap before vertices remain byte-identical, rather than being discarded.
    """
    if not partitions_by_mesh or any(type(i) is not int or not 0 <= i < len(document.meshes) for i in partitions_by_mesh):
        raise Mod3Error("拆分 Mesh 索引无效")
    for index, islands in partitions_by_mesh.items():
        count = document.meshes[index].triangle_count
        if not isinstance(islands, (list, tuple)) or not islands:
            raise Mod3Error("三角面分区无效")
        valid_parts = []
        for part in islands:
            values = np.asarray(part)
            if values.ndim != 1 or not len(values) or values.dtype.kind not in "iu":
                raise Mod3Error("三角面分区无效")
            if np.any(values < 0) or np.any(values >= count):
                raise Mod3Error("三角面分区超出范围")
            valid_parts.append(values.astype(np.int64))
        flat = np.concatenate(valid_parts)
        if len(flat) != count or not np.array_equal(np.sort(flat), np.arange(count)):
            raise Mod3Error("拆分必须保留所有三角面且不能重复")
    source = document.bytes()
    values = list(BASE_HEADER.unpack_from(source))
    mesh_offset, vertex_offset, face_offset, trail_offset = values[16:20]
    if values[10] or values[20] or not mesh_offset < vertex_offset <= face_offset <= trail_offset <= len(source):
        raise Mod3Error("此 MOD3 使用未支持的附加缓冲区，暂不能安全拆分")
    boxes_start = mesh_offset + len(document.meshes) * MESH_HEADER_SIZE
    count = struct.unpack_from("<I", source, boxes_start)[0]
    boxes_end = boxes_start + 4 + count * 144
    if boxes_end > vertex_offset:
        raise Mod3Error("包围盒数据超出范围，禁止拆分")
    box_cursor = boxes_start + 4
    records, boxes, vertex_buffers, face_buffers, origins, labels = [], [], [], [], [], []
    vertex_bytes = face_indices = total_vertices = 0
    groups = getattr(document, "_origin_groups", [document.original_group(i) for i in range(len(document.meshes))])
    for mesh in document.meshes:
        header = bytearray(source[mesh.header_offset:mesh.header_offset + MESH_HEADER_SIZE])
        box_count = header[41]
        mesh_boxes = source[box_cursor:box_cursor + box_count * 144]
        box_cursor += box_count * 144
        if box_cursor > boxes_end or mesh.index_count != mesh.triangle_count * 3:
            raise Mod3Error("原模型含异常索引或包围盒，禁止拆分")
        sub, local, _, _, _, base = struct.unpack_from("<IIIIII", header, 16)
        raw = np.frombuffer(source, dtype="<u2", count=mesh.index_count, offset=face_offset + struct.unpack_from("<I", header, 28)[0] * 2)
        adjustment = max(0, sub - int(raw.min())) if len(raw) else 0
        start = vertex_offset + local + mesh.block_size * (sub + base - adjustment)
        changed = mesh.index in partitions_by_mesh
        partitions = partitions_by_mesh[mesh.index] if changed else [np.arange(mesh.triangle_count)]
        for part_number, partition in enumerate(partitions):
            faces = mesh.faces[partition]
            used = np.unique(faces) if changed else np.arange(mesh.vertex_count)
            if not len(used) or len(used) > 65535:
                raise Mod3Error("子部件顶点数量无效")
            remap = np.full(mesh.vertex_count, -1, dtype=np.int32)
            remap[used] = np.arange(len(used))
            vertices = b"".join(source[start + int(v) * mesh.block_size:start + (int(v) + 1) * mesh.block_size] for v in used)
            indices = remap[faces].astype("<u2").tobytes()
            child = bytearray(header)
            struct.pack_into("<H", child, 2, len(used))
            struct.pack_into("<II", child, 16, 0, vertex_bytes)
            struct.pack_into("<III", child, 28, face_indices, len(faces) * 3, 0)
            struct.pack_into("<HH", child, 44, 0, len(used) - 1)
            struct.pack_into("<I", child, 60, total_vertices)
            records.append(bytes(child))
            boxes.append(mesh_boxes)  # Conservative original bounds, including bone metadata.
            vertex_buffers.append(vertices)
            face_buffers.append(indices)
            origins.append(groups[mesh.index])
            old_label = getattr(document, "_mesh_labels", [""] * len(document.meshes))[mesh.index]
            labels.append(f"{old_label or '原 Mesh ' + str(mesh.index)} / 子部件 {part_number + 1}" if changed else old_label)
            vertex_bytes += len(vertices)
            face_indices += len(faces) * 3
            total_vertices += len(used)
    if box_cursor > boxes_end or len(records) > 65535:
        raise Mod3Error("包围盒清单或 Mesh 数量异常")
    surplus = source[box_cursor:boxes_end]
    padding = source[boxes_end:vertex_offset]
    table = (b"".join(records) + struct.pack("<I", sum(len(b) // 144 for b in boxes) + len(surplus) // 144)
             + b"".join(boxes) + surplus + padding)
    values[4], values[6], values[7], values[9] = len(records), total_vertices, face_indices, vertex_bytes
    values[17] = mesh_offset + len(table)
    values[18] = values[17] + vertex_bytes
    values[19] = (values[18] + face_indices * 2 + 3) & ~3
    result = bytearray(source[:mesh_offset] + table + b"".join(vertex_buffers) + b"".join(face_buffers))
    result.extend(b"\0" * (values[19] - len(result)))
    result.extend(source[trail_offset:])
    BASE_HEADER.pack_into(result, 0, *values)
    parsed = Mod3Document(document.name, bytes(result))
    if sum(m.triangle_count for m in parsed.meshes) != sum(m.triangle_count for m in document.meshes):
        raise Mod3Error("拆分后的三角面总数不一致")
    return bytes(result), origins, labels
