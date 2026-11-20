import importlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

class Input:
    def __init__(self, key, name, value=None):
        self.id = key
        self._name = name
        self.value = value
        self.isValidExpression = True
        self.isVisible = True
        self.selectionCount = 0

    @property
    def name(self):
        return self._name

    def addSelectionFilter(self, filter_name):
        pass

    def setSelectionLimits(self, minimum, maximum):
        pass


class ListItem:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    @property
    def index(self):
        return self.owner.items.index(self)

    @property
    def isSelected(self):
        return self.owner.selectedItem is self

    @isSelected.setter
    def isSelected(self, value):
        if value:
            self.owner.selectedItem = self


class Dropdown(Input):
    def __init__(self, key, name):
        super().__init__(key, name)
        self.listItems = self
        self.items = []
        self.selectedItem = None

    def clear(self):
        self.items.clear()
        self.selectedItem = None

    def add(self, title, selected):
        item = ListItem(self, title)
        self.items.append(item)
        item.isSelected = selected
        return item

    def item(self, index):
        return self.items[index]

class CommandInputs:
    def __init__(self):
        self.text_boxes = {}

    def addDropDownCommandInput(self, key, name, style):
        return Dropdown(key, name)

    def addValueInput(self, key, name, units, value):
        return Input(key, name, value)

    def addIntegerSpinnerCommandInput(self, key, name, minimum, maximum, increment, value):
        return Input(key, name, value)

    def addGroupCommandInput(self, key, name):
        return SimpleNamespace(id=key, name=name, children=CommandInputs())

    def addBoolValueInput(self, key, name, checkbox, resource, value):
        return Input(key, name, value)

    def addTextBoxCommandInput(self, key, name, value, rows, read_only):
        control = Input(key, name, value)
        control.isReadOnly = read_only
        self.text_boxes[key] = control
        return control

    def addSelectionInput(self, key, name, prompt):
        return Input(key, name)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adsk = ModuleType('adsk')
        adsk.core = ModuleType('adsk.core')
        adsk.fusion = ModuleType('adsk.fusion')
        for name in ('CustomEventHandler', 'InputChangedEventHandler', 'ValidateInputsEventHandler',
                     'CommandEventHandler', 'CommandCreatedEventHandler'):
            setattr(adsk.core, name, object)
        adsk.core.ValueInput = SimpleNamespace(createByReal=float)
        adsk.core.DropDownStyles = SimpleNamespace(TextListDropDownStyle=0)
        package = ModuleType('_gear_workbench_test_plugin')
        package.__path__ = [str(Path(__file__).resolve().parents[1] / 'FusionGearWorkbench')]
        model = ModuleType(f'{package.__name__}.fusion_model')
        model.build = model.load_spec = None
        with patch.dict(sys.modules, {'adsk': adsk, 'adsk.core': adsk.core, 'adsk.fusion': adsk.fusion,
                                      package.__name__: package, model.__name__: model}):
            cls.plugin = importlib.import_module(f'{package.__name__}.FusionGearWorkbench')

    def setUp(self):
        design = patch.object(self.plugin, '_design')
        design.start()
        self.addCleanup(design.stop)
        self.dialog = self.open_dialog('new')

    def open_dialog(self, mode):
        event = SimpleNamespace(add=lambda handler: None)
        command = SimpleNamespace(setDialogInitialSize=lambda width, height: None, commandInputs=CommandInputs(),
                                  inputChanged=event, validateInputs=event, execute=event, destroy=event)
        return self.plugin._Dialog(command, mode, mode)

    def select(self, key, value):
        control = self.dialog.controls[key]
        control.item(self.dialog.choices[key].index(value)).isSelected = True
        self.dialog.handlers[0].notify(SimpleNamespace(input=control))

    def visible(self):
        return {key for key, control in self.dialog.controls.items() if control.isVisible}

    def test_author_credit_is_visible_and_read_only_in_each_mode(self):
        for mode in ('new', 'edit', 'export'):
            with self.subTest(mode=mode):
                credit = self.open_dialog(mode).inputs.text_boxes['author_credit']
                self.assertIn('Peter Shelby', credit.value)
                self.assertTrue(credit.isReadOnly)
                self.assertTrue(credit.isVisible)

    def test_default_spur_opens_with_only_spur_parameters(self):
        self.assertEqual(self.visible(), {'kind', 'size_mode', 'diameter', 'module', 'thickness', 'bore',
                                         'advanced', 'pressure', 'backlash', 'step', 'feedback'})
        self.assertFalse(self.dialog.controls['advanced'].isExpanded)
        self.assertTrue(self.dialog.validate())
        self.assertIn('24 齿', self.dialog.controls['feedback'].text)

    def test_size_mode_ignores_inactive_inputs(self):
        self.select('size_mode', 'outside')
        self.assertIn('齿顶外径', self.dialog.controls['diameter'].tooltip)
        self.dialog.controls['diameter'].isValidExpression = False
        self.assertFalse(self.dialog.validate())
        self.select('size_mode', 'teeth')
        self.assertIn('teeth', self.visible())
        self.assertNotIn('diameter', self.visible())
        self.assertEqual(self.dialog._spec().teeth, 24)
        self.assertTrue(self.dialog.validate())

    def test_type_switches_between_helical_rack_bevel_and_spur(self):
        for kind in ('helical', 'herringbone'):
            self.select('kind', kind)
            self.assertTrue({'helix', 'hand'} <= self.visible())
            self.assertIn('法向', self.dialog.controls['module'].tooltip)
        self.select('kind', 'rack')
        self.assertTrue({'rack_length', 'rack_base'} <= self.visible())
        self.assertFalse({'helix', 'hand', 'diameter', 'bore', 'worm_length'} & self.visible())
        self.assertEqual(self.dialog.choices['size_mode'], ('pitch', 'teeth'))
        self.select('kind', 'bevel')
        self.assertIn('mate_teeth', self.visible())
        self.assertNotIn('rack_base', self.visible())
        self.assertIn('沿锥面', self.dialog.controls['thickness'].tooltip)
        self.select('kind', 'spur')
        self.assertNotIn('mate_teeth', self.visible())
        self.assertIn('outside', self.dialog.choices['size_mode'])
        self.assertTrue(self.dialog.validate())

    def test_inner_ring_helix_inputs_depend_on_tooth_style(self):
        self.select('kind', 'ring')
        self.assertTrue({'ring_helical', 'ring_wall'} <= self.visible())
        self.assertFalse({'helix', 'hand', 'bore'} & self.visible())
        self.select('ring_helical', True)
        self.assertTrue({'helix', 'hand'} <= self.visible())
        self.select('ring_helical', False)
        self.assertFalse({'helix', 'hand'} & self.visible())

    def test_worm_pair_and_single_output_fields(self):
        self.select('kind', 'worm')
        self.assertTrue({'worm_output', 'worm_diameter', 'worm_length', 'worm_starts', 'hand', 'diameter'} <= self.visible())
        self.select('worm_output', 'wheel')
        self.assertNotIn('worm_length', self.visible())
        self.assertIn('thickness', self.visible())
        self.select('worm_output', 'screw')
        self.assertFalse({'diameter', 'bore', 'thickness', 'size_mode', 'teeth'} & self.visible())
        self.assertIn('worm_length', self.visible())
        self.assertTrue(self.dialog.validate())
        self.assertIn('蜗杆分度', self.dialog.controls['feedback'].text)

    def test_edit_hides_fields_until_selection_and_restores_advanced_values(self):
        self.dialog = self.open_dialog('edit')
        self.assertEqual(self.visible(), {'source', 'feedback'})
        saved = replace(self.plugin.defaults('ring'), ring_helical=True, pressure=22.0, backlash=0.13)
        with patch.object(self.dialog, '_selection', return_value=object()), patch.object(self.plugin, 'load_spec', return_value=saved):
            self.dialog.update('source')
            self.assertTrue({'ring_wall', 'helix', 'hand', 'hide_old'} <= self.visible())
            self.assertFalse(self.dialog.controls['advanced'].isExpanded)
            self.assertAlmostEqual(self.dialog._spec().pressure, 22)
            self.assertAlmostEqual(self.dialog._spec().backlash, 0.13)


if __name__ == '__main__':
    unittest.main()
