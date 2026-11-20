import sys
import unittest
from dataclasses import asdict, replace
from json import dumps, loads
from math import atan, cos, degrees, hypot, pi, radians, tan
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'FusionGearWorkbench'))

from gear_geometry import GearSpec, TYPES, defaults, dimensions, gear_outline, rack_outline, summary, worm_outline


class GeometryTests(unittest.TestCase):
    def assert_outline(self, segments):
        for (kind, points), (_, following) in zip(segments, segments[1:] + segments[:1]):
            self.assertIn(kind, ('line', 'arc', 'spline'))
            self.assertAlmostEqual(hypot(points[-1][0] - following[0][0], points[-1][1] - following[0][1]), 0)
            self.assertGreater(hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]), 1e-6)
        points = [point for _, segment in segments for point in segment[:-1]]
        twice_area = sum(start[0] * end[1] - end[0] * start[1]
                         for start, end in zip(points, points[1:] + points[:1]))
        self.assertGreater(twice_area, 0)

    def test_every_default_is_valid_and_closed(self):
        for kind in TYPES:
            with self.subTest(kind=kind):
                spec = defaults(kind)
                dims = dimensions(spec)
                generator = rack_outline if kind == 'rack' else gear_outline
                self.assert_outline(generator(spec, dims))
                self.assertTrue(summary(spec, dims))
                if kind == 'worm':
                    self.assert_outline(worm_outline(spec, dims))

    def test_diameter_modes_snap_to_integer_teeth(self):
        spec = replace(defaults('spur'), diameter=50.4, size_mode='outside')
        dims = dimensions(spec)
        self.assertEqual((dims.teeth, dims.pitch_d, dims.tip_d), (23, 46, 50))
        self.assertTrue(dims.warnings)
        spec = replace(defaults('spur'), teeth=30, size_mode='teeth', diameter=1)
        self.assertEqual(dimensions(spec).pitch_d, 60)

    def test_normal_module_and_twist(self):
        spec = replace(defaults('helical'), size_mode='teeth', teeth=20, helix=30, hand=-1)
        dims = dimensions(spec)
        self.assertAlmostEqual(dims.pitch_d, 40 / cos(radians(30)))
        self.assertAlmostEqual(dims.pressure_t, atan(tan(radians(20)) / cos(radians(30))))
        self.assertAlmostEqual(dims.twist, -spec.thickness * tan(radians(30)) / (dims.pitch_d / 2))
        self.assertEqual(dimensions(replace(spec, kind='herringbone')).twist, dims.twist)
        self.assertEqual(dimensions(replace(spec, helix=0)).twist, 0)

    def test_ring_tip_root_wall_and_helical_option(self):
        spec = defaults('ring')
        dims = dimensions(spec)
        self.assertEqual((dims.tip_d, dims.pitch_d, dims.root_d, dims.outer_d), (76, 80, 85, 93))
        left = replace(spec, ring_helical=True, hand=-1)
        self.assertLess(dimensions(left).twist, 0)
        self.assert_outline(gear_outline(left, dimensions(left)))

    def test_tooth_thinning_decreases_material_on_external_and_internal_gears(self):
        for kind in ('spur', 'ring'):
            spec = defaults(kind)
            thinned = replace(spec, backlash=0.25)
            base_arc = next(points for code, points in gear_outline(spec, dimensions(spec)) if code == 'arc')
            thin_arc = next(points for code, points in gear_outline(thinned, dimensions(thinned)) if code == 'arc')
            difference = abs(thin_arc[0][1]) - abs(base_arc[0][1])
            self.assertGreater(difference if kind == 'ring' else -difference, 0)

    def test_bevel_90_degree_pair(self):
        spec = replace(defaults('bevel'), size_mode='teeth', teeth=18, mate_teeth=36)
        primary = dimensions(spec)
        mate = dimensions(replace(spec, teeth=36, mate_teeth=18))
        self.assertAlmostEqual(degrees(primary.bevel_angle + mate.bevel_angle), 90)
        self.assertAlmostEqual(primary.tip_d - primary.pitch_d, 2 * spec.module * cos(primary.bevel_angle))
        self.assertAlmostEqual(primary.cone_scale, 1 - spec.thickness / hypot(18, 36))

    def test_rack_pitch_and_longitudinal_closure(self):
        spec = replace(defaults('rack'), size_mode='teeth', teeth=7, module=1.5)
        dims = dimensions(spec)
        self.assertAlmostEqual(dims.rack_length, 7 * pi * 1.5)
        self.assertEqual(len(rack_outline(spec, dims)), 4 + 4 * 7)
        self.assert_outline(rack_outline(spec, dims))

    def test_worm_lead_center_distance_and_individual_outputs(self):
        pair = replace(defaults('worm'), worm_starts=2, hand=-1)
        dims = dimensions(pair)
        self.assertAlmostEqual(dims.worm_lead, 4 * pi)
        self.assertAlmostEqual(dims.worm_twist, -12)
        self.assertEqual(dims.center_distance, 42)
        self.assertEqual(dimensions(replace(pair, worm_output='wheel', worm_length=0)).worm_twist, 0)
        screw = replace(pair, worm_output='screw', bore=999, diameter=0)
        self.assert_outline(worm_outline(screw, dimensions(screw)))

    def test_rejects_unusable_dimensions(self):
        invalid = [replace(defaults('spur'), module=0), replace(defaults('spur'), bore=60),
                   replace(defaults('bevel'), thickness=50), replace(defaults('ring'), ring_wall=0),
                   replace(defaults('helical'), helix=80), replace(defaults('rack'), rack_length=1),
                   replace(defaults('worm'), worm_diameter=2), replace(defaults('spur'), diameter=float('nan'))]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                dimensions(spec)

    def test_saved_inputs_round_trip(self):
        for kind in TYPES:
            expected = defaults(kind)
            self.assertEqual(GearSpec(**loads(dumps(asdict(expected)))), expected)


if __name__ == '__main__':
    unittest.main()
