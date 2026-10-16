import math
import unittest
from types import SimpleNamespace
import numpy as np

from mod3 import Mod3Error
from surface_distance import SurfaceDistances


def mesh(triangles):
    vertices = np.asarray(triangles, np.float32).reshape(-1, 3)
    return SimpleNamespace(vertices=vertices, faces=np.arange(len(vertices)).reshape(-1, 3))


class SurfaceDistanceTests(unittest.TestCase):
    def test_overlapping_boxes_can_have_distant_surfaces(self):
        # Two triangles occupy opposite corners of the same square bounds.
        a = mesh([[[0, 0, 0], [4, 0, 0], [0, 4, 0]]])
        b = mesh([[[4, 4, 0], [4, 3, 0], [3, 4, 0]]])
        distances = SurfaceDistances()
        self.assertTrue(math.isinf(distances.distance(a, b, 1)))
        self.assertAlmostEqual(3 / math.sqrt(2), distances.distance(a, b, 4))
        self.assertTrue(math.isinf(distances.distance(a, b, 1)))

    def test_nearest_point_can_be_inside_a_large_face(self):
        a = mesh([[[-10, -10, 0], [10, -10, 0], [0, 10, 0]]])
        b = mesh([[[-.1, -.1, .3], [.1, -.1, .3], [0, .1, .3]]])
        self.assertAlmostEqual(.3, SurfaceDistances().distance(a, b, 1), places=6)

    def test_triangle_interiors_cross_and_degenerate_faces_are_safe(self):
        a = mesh([[[-2, -2, 0], [2, -2, 0], [0, 2, 0]]])
        b = mesh([[[0, 0, -2], [0, 0, 2], [3, 0, 2]]])
        self.assertEqual(0, SurfaceDistances().distance(a, b, 0))
        point = mesh([[[0, 0, 2]] * 3])
        self.assertAlmostEqual(2, SurfaceDistances().distance(a, point, 2))

    def test_bvh_search_preserves_data_and_cancellation(self):
        a = mesh([[[i, 0, 0], [i + .5, 0, 0], [i, .5, 0]] for i in range(40)])
        b = mesh([[[39, .1, 1], [39.2, .1, 1], [39, .2, 1]]])
        before = a.vertices.copy()
        self.assertAlmostEqual(1, SurfaceDistances().distance(a, b, 1))
        np.testing.assert_array_equal(before, a.vertices)
        with self.assertRaisesRegex(Mod3Error, "取消"):
            SurfaceDistances(lambda: True).distance(a, b, 1)

    def test_spatial_grouping_rejects_false_box_overlap(self):
        from mod3 import ModWorkspace
        from spatial_groups import suggest_groups
        a = mesh([[[1, 49, 0], [-1, 50, 1], [0, 51, -1]]])
        b = mesh([[[1, 51, -.5], [1, 49.5, 1], [-.5, 51, 1]]])
        frame = mesh([[[0, 0, 0], [0, 100, 0], [0, 0, 0]]])
        for i, m in enumerate((a, b, frame)):
            m.index, m.material_id, m.visible_condition, m.lod = i, 0, 0, 1
        document = SimpleNamespace(name="wst/overlap.mod3", outfit_id="outfit", equipment_part="wst",
                                   meshes=[a, b, frame], material_info=lambda _: SimpleNamespace(name="same"))
        workspace = ModWorkspace()
        workspace.documents = [document]
        groups = suggest_groups(workspace, {(document.name, 0), (document.name, 1)}, distance="保守", pair=False)
        self.assertEqual(2, len(groups))


if __name__ == "__main__":
    unittest.main()
