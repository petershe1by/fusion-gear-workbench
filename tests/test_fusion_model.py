import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class FusionModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adsk = ModuleType('adsk')
        adsk.core = ModuleType('adsk.core')
        adsk.fusion = ModuleType('adsk.fusion')
        adsk.core.Point3D = SimpleNamespace(create=lambda *coordinates: coordinates)
        adsk.core.ValueInput = SimpleNamespace(createByReal=float)
        adsk.fusion.FeatureOperations = SimpleNamespace(NewBodyFeatureOperation='new')
        adsk.fusion.FeatureHealthStates = SimpleNamespace(ErrorFeatureHealthState='error')
        package = ModuleType('_gear_workbench_test_model')
        package.__path__ = [str(Path(__file__).resolve().parents[1] / 'FusionGearWorkbench')]
        with patch.dict(sys.modules, {'adsk': adsk, 'adsk.core': adsk.core, 'adsk.fusion': adsk.fusion,
                                      package.__name__: package}):
            cls.model = importlib.import_module(f'{package.__name__}.fusion_model')

    def setUp(self):
        self.native_line = object()
        self.sketch = SimpleNamespace(
            modelToSketchSpace=lambda point: point,
            sketchCurves=SimpleNamespace(sketchLines=SimpleNamespace(addByTwoPoints=Mock(return_value=self.native_line))),
            isVisible=True,
        )
        self.profile = object()
        self.path = object()
        self.sweep_input = SimpleNamespace()
        self.sweep_feature = SimpleNamespace(healthState='healthy')
        self.features = SimpleNamespace(
            createPath=Mock(return_value=self.path),
            sweepFeatures=SimpleNamespace(createInput=Mock(return_value=self.sweep_input),
                                          add=Mock(return_value=self.sweep_feature)),
        )
        self.component = SimpleNamespace(sketches=SimpleNamespace(add=Mock(return_value=self.sketch)),
                                         xZConstructionPlane=object(), features=self.features)

    def test_sweep_creates_path_from_native_axis_in_owning_component(self):
        target_body = object()
        result = self.model._sweep(self.component, self.profile, 0, 8, -0.15, '螺旋内齿切除', 'cut', target_body)
        self.assertIs(result, self.sweep_feature)
        self.features.createPath.assert_called_once_with(self.native_line, False)
        self.features.sweepFeatures.createInput.assert_called_once_with(self.profile, self.path, 'cut')
        self.assertEqual(self.sweep_input.twistAngle, -0.15)
        self.assertEqual(self.sweep_input.participantBodies, [target_body])
        self.assertFalse(self.sketch.isVisible)

    def test_path_error_names_the_failed_stage(self):
        self.features.createPath.side_effect = RuntimeError('InternalValidationError')
        with self.assertRaisesRegex(RuntimeError, '「螺旋扫掠」的扫掠路径：InternalValidationError'):
            self.model._sweep(self.component, self.profile, 0, 8, 0.15, '螺旋扫掠')
        self.features.sweepFeatures.createInput.assert_not_called()

    def test_worm_pair_builds_screw_before_rotating_it(self):
        parent = object()
        wheel = SimpleNamespace(component=object())
        timeline = SimpleNamespace(markerPosition=11)
        screw_component = SimpleNamespace(parentDesign=SimpleNamespace(timeline=timeline))

        def roll_back(before):
            self.assertFalse(before)
            timeline.markerPosition = 4
            return True

        screw = SimpleNamespace(component=screw_component, initialTransform=None,
                                timelineObject=SimpleNamespace(rollTo=Mock(side_effect=roll_back)))
        rotation = Mock()
        spec = SimpleNamespace(worm_starts=1, worm_length=24)
        dims = SimpleNamespace(teeth=32, axial_width=8, center_distance=42)

        def sweep_screw(component, spec, dims, name):
            self.assertIs(component, screw.component)
            self.assertIsNone(screw.initialTransform)

        with (patch.object(self.model, '_component', side_effect=[wheel, screw]) as add_component,
              patch.object(self.model, '_cylindrical') as build_wheel,
              patch.object(self.model, '_worm', side_effect=sweep_screw),
              patch.object(self.model.core, 'Matrix3D', SimpleNamespace(create=lambda: rotation), create=True),
              patch.object(self.model.core, 'Vector3D', self.model.core.Point3D, create=True)):
            self.model._worm_pair(parent, spec, dims)
        self.assertEqual([call.args[0] for call in add_component.call_args_list], [parent, parent])
        build_wheel.assert_called_once_with(wheel.component, spec, dims, '近似蜗轮 32T')
        self.assertIs(screw.initialTransform, rotation)
        self.assertEqual(timeline.markerPosition, 11)
        self.assertEqual(rotation.translation, (-1.2, 4.2, 0.4))


if __name__ == '__main__':
    unittest.main()
