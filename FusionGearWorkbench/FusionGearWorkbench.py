from dataclasses import fields
from json import loads
from math import degrees, radians
from pathlib import Path
from traceback import format_exc
from uuid import uuid4

import adsk.core as core
import adsk.fusion as fusion

from .fusion_model import build, load_spec
from .gear_geometry import GearSpec, TYPES, defaults, dimensions, size_modes, summary


TITLE = '齿轮工坊'
_MANIFEST = loads(Path(__file__).with_suffix('.manifest').read_text(encoding='utf-8'))
VERSION = _MANIFEST['version']
AUTHOR = _MANIFEST['author']
VERSIONED_TITLE = f'{TITLE} v{VERSION}'
PREFIX = 'InitialGearWorkbench'
EXPORT_EVENT = PREFIX + '_deferredExport'
COMMANDS = {
    'new': ('齿轮工坊 · 生成', '生成齿轮、内齿圈、蜗轮蜗杆或齿条。'),
    'edit': ('齿轮工坊 · 改参', '读取所选齿轮的参数，生成同位置的新版本。'),
    'export': ('齿轮工坊 · 导出', '导出所选齿轮组件的 STEP，或备份整个原生设计。'),
}
LENGTHS = {
    'module': '模数', 'diameter': '目标直径', 'rack_length': '目标齿条长度',
    'thickness': '厚度 / 齿宽', 'bore': '轴孔直径（0 = 无孔）', 'backlash': '齿厚减薄量',
    'ring_wall': '外圈径向壁厚', 'rack_base': '齿根以下底座高度',
    'worm_diameter': '蜗杆分度圆直径', 'worm_length': '蜗杆有齿长度',
}
ANGLES = {'pressure': '压力角', 'helix': '螺旋角（分度圆处）'}
COUNTS = {'teeth': ('齿数', 1, 180), 'mate_teeth': ('配对齿数（90° 轴交角）', 8, 180),
          'worm_starts': ('蜗杆头数', 1, 4)}
HANDS = {1: '右旋', -1: '左旋'}
RING_STYLES = {False: '直齿内齿圈', True: '斜齿内齿圈'}
WORM_OUTPUTS = {'pair': '一组 · 蜗轮 + 蜗杆', 'wheel': '单独生成蜗轮', 'screw': '单独生成蜗杆'}
FILE_TYPES = {'step': '所选组件 · STEP 实体', 'f3d': '整个设计 · Fusion 原生备份 (.f3d)'}

_app = None
_ui = None
_panel = None
_event = None
_handlers = []
_dialogs = {}
_pending = {}
_presets = {}


def _log():
    if _app:
        _app.log(format_exc())


def _design(for_build=False):
    design = fusion.Design.cast(_app.activeProduct)
    if not design:
        raise ValueError('请先打开或新建 Fusion 设计。')
    if for_build:
        intents = getattr(fusion, 'DesignIntentTypes', None)
        if intents and design.designIntent != intents.HybridDesignIntentType:
            raise ValueError('请使用混合设计（Hybrid）：在浏览器的「文档设置 → 设计类型」中转换，或新建混合设计。')
        if design.designType != fusion.DesignTypes.ParametricDesignType:
            raise ValueError('请先打开设计历史（参数化建模），以保留草图与特征时间线。')
        if design.timeline.markerPosition != design.timeline.count:
            raise ValueError('请先把时间线标记移到末尾。')
    return design


def _queue_export(design, component, file_type):
    token = uuid4().hex
    _pending[token] = (design, component, file_type)
    if not _app.fireCustomEvent(EXPORT_EVENT, token):
        _pending.pop(token, None)
        raise RuntimeError('无法打开导出对话框，请使用「齿轮工坊 · 导出」重试。')


class _Export(core.CustomEventHandler):
    def notify(self, args):
        request = _pending.pop(args.additionalInfo, None)
        if not request:
            return
        design, component, file_type = request
        try:
            if not design.isValid or not component.isValid:
                raise ValueError('原组件已关闭或不存在，请重新选择后导出。')
            manager = design.exportManager
            dialog = _ui.createFileDialog()
            dialog.title = '导出 ' + FILE_TYPES[file_type]
            dialog.filter = 'STEP (*.step;*.stp)' if file_type == 'step' else 'Fusion Archive (*.f3d)'
            dialog.initialFilename = component.name + '.' + file_type
            if dialog.showSave() != core.DialogResults.DialogOK:
                return
            path = Path(dialog.filename)
            allowed = ('.step', '.stp') if file_type == 'step' else ('.f3d',)
            if path.suffix.lower() not in allowed:
                path = path.with_name(path.name + allowed[0])
                if path.exists():
                    raise ValueError(f'补齐扩展名后的文件已存在，请重新选择名称：\n{path}')
            if file_type == 'step':
                options = manager.createSTEPExportOptions(str(path), component)
            else:
                options = manager.createFusionArchiveExportOptions(str(path))
            if not options or not manager.execute(options) or not path.is_file() or not path.stat().st_size:
                raise RuntimeError('Fusion 未写出导出文件。请确认目录可写，并检查组件实体。')
            if file_type == 'step':
                with path.open('rb') as exported:
                    if not exported.read(64).lstrip().startswith(b'ISO-10303-21;'):
                        raise RuntimeError('导出的文件未通过 STEP 文件头检查。')
            _ui.messageBox(f'已导出：\n{path}', TITLE)
        except Exception as error:
            _log()
            _ui.messageBox(f'导出失败：{error}', TITLE)


class _Changed(core.InputChangedEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        self.dialog.update(args.input.id)


class _Validate(core.ValidateInputsEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        args.areInputsValid = self.dialog.validate()


class _Execute(core.CommandEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        try:
            self.dialog.execute()
        except Exception as error:
            _log()
            args.executeFailed = True
            args.executeFailedMessage = f'{VERSIONED_TITLE}：{error}'


class _Destroy(core.CommandEventHandler):
    def __init__(self, token):
        super().__init__()
        self.token = token

    def notify(self, args):
        _dialogs.pop(self.token, None)


class _Dialog:
    def __init__(self, command, mode, token):
        self.mode = mode
        self.inputs = command.commandInputs
        self.controls = {}
        self.choices = {}
        self.loaded_kind = ''
        self.selected = None
        self.updating = False
        self.active_fields = set()
        self.form_error = ''
        command.setDialogInitialSize(460, 510)
        command.okButtonText = {'new': '生成实体', 'edit': '生成改参版本', 'export': '选择保存位置'}[mode]
        self.inputs.addTextBoxCommandInput('author_credit', '', f'{AUTHOR} · Fusion Gear Workbench', 1, True)
        if mode != 'new':
            selection = self.inputs.addSelectionInput('source', '原生齿轮组件', '在左侧模型树选择顶层组件')
            selection.addSelectionFilter('Occurrences')
            selection.addSelectionFilter('SolidFaces')
            selection.addSelectionFilter('SolidBodies')
            selection.setSelectionLimits(1, 1)
            self.controls['source'] = selection
        if mode == 'export':
            self._dropdown('format', '文件格式', FILE_TYPES, 'step')
            self.controls['source'].setSelectionLimits(0, 1)
        else:
            self._model_fields(_presets.get('spur', defaults('spur')) if mode == 'new' else defaults('spur'))
        self.controls['feedback'] = self.inputs.addTextBoxCommandInput('feedback', '', '', 6, True)
        self.handlers = (_Changed(self), _Validate(self), _Execute(self), _Destroy(token))
        for event, handler in zip((command.inputChanged, command.validateInputs, command.execute, command.destroy), self.handlers):
            event.add(handler)
        self.update('kind' if mode == 'new' else 'source')

    def _dropdown(self, key, title, options, selected):
        self.controls[key] = self.inputs.addDropDownCommandInput(key, title, core.DropDownStyles.TextListDropDownStyle)
        self._fill(key, options, selected)

    def _fill(self, key, options, selected):
        self.choices[key] = tuple(options)
        items = self.controls[key].listItems
        items.clear()
        for name, title in options.items():
            items.add(title, name == selected)

    def _choice(self, key):
        selected = self.controls[key].selectedItem
        if not selected:
            raise ValueError('请选择齿轮参数。')
        return self.choices[key][selected.index]

    def _set_choice(self, key, value):
        self.controls[key].listItems.item(self.choices[key].index(value)).isSelected = True

    def _model_fields(self, initial):
        self._dropdown('kind', '齿轮类型', TYPES, initial.kind)
        self._dropdown('worm_output', '蜗轮蜗杆输出', WORM_OUTPUTS, initial.worm_output)
        self._dropdown('ring_helical', '内齿齿线', RING_STYLES, initial.ring_helical)
        self._dropdown('size_mode', '尺寸输入方式', size_modes(initial.kind), initial.size_mode)
        for key in ('diameter', 'teeth', 'rack_length', 'module', 'thickness', 'helix',
                    'hand', 'bore', 'ring_wall', 'mate_teeth', 'rack_base',
                    'worm_diameter', 'worm_length', 'worm_starts'):
            value = getattr(initial, key)
            if key in LENGTHS:
                self.controls[key] = self.inputs.addValueInput(key, LENGTHS[key], 'mm', core.ValueInput.createByReal(value / 10))
            elif key in ANGLES:
                self.controls[key] = self.inputs.addValueInput(key, ANGLES[key], 'deg', core.ValueInput.createByReal(radians(value)))
            elif key in COUNTS:
                label, minimum, maximum = COUNTS[key]
                self.controls[key] = self.inputs.addIntegerSpinnerCommandInput(key, label, minimum, maximum, 1, value)
            else:
                self._dropdown(key, '旋向', HANDS, value)
        advanced = self.inputs.addGroupCommandInput('advanced', '高级参数（可选）')
        advanced.isExpanded = False
        self.controls['advanced'] = advanced
        self.controls['pressure'] = advanced.children.addValueInput(
            'pressure', ANGLES['pressure'], 'deg', core.ValueInput.createByReal(radians(initial.pressure)))
        self.controls['backlash'] = advanced.children.addValueInput(
            'backlash', LENGTHS['backlash'], 'mm', core.ValueInput.createByReal(initial.backlash / 10))
        self.controls['step'] = self.inputs.addBoolValueInput('step', '生成后导出 STEP', True, '', False)
        if self.mode == 'edit':
            self.controls['hide_old'] = self.inputs.addBoolValueInput('hide_old', '隐藏旧组件（不删除）', True, '', True)
            self.controls['kind'].isEnabled = False
        self.loaded_kind = initial.kind

    def _load(self, spec):
        if self._choice('kind') != spec.kind:
            self._set_choice('kind', spec.kind)
        self._fill('size_mode', size_modes(spec.kind), spec.size_mode)
        for field in fields(GearSpec):
            key, value = field.name, getattr(spec, field.name)
            if key in ('kind', 'size_mode'):
                continue
            if key in self.choices:
                self._set_choice(key, value)
            elif key in LENGTHS:
                self.controls[key].value = value / 10
            elif key in ANGLES:
                self.controls[key].value = radians(value)
            else:
                self.controls[key].value = value
        self.loaded_kind = spec.kind

    def _selection(self):
        control = self.controls['source']
        if control.selectionCount != 1:
            raise ValueError('请先选中齿轮组件；蜗轮蜗杆组选父组件。')
        entity = control.selection(0).entity
        return fusion.Occurrence.cast(entity) or getattr(entity, 'assemblyContext', None)

    def _visibility(self, ready):
        self.active_fields = self._model_layout() if ready else set()
        for key, control in self.controls.items():
            if key not in ('source', 'feedback'):
                control.isVisible = key in self.active_fields

    def _model_layout(self):
        kind = self._choice('kind')
        size = self._choice('size_mode')
        worm = kind == 'worm'
        output = self._choice('worm_output') if worm else None
        screw_only = output == 'screw'
        helix = kind in ('helical', 'herringbone') or (kind == 'ring' and self._choice('ring_helical'))
        visible = {'kind', 'module', 'advanced', 'pressure', 'backlash', 'step', 'hide_old'}
        if not screw_only:
            visible.update(('size_mode', 'thickness'))
            visible.add('teeth' if size == 'teeth' else 'rack_length' if kind == 'rack' else 'diameter')
            if kind not in ('rack', 'ring'):
                visible.add('bore')
        if helix:
            visible.update(('helix', 'hand'))
        if kind == 'ring':
            visible.update(('ring_wall', 'ring_helical'))
        if kind == 'bevel':
            visible.add('mate_teeth')
        if kind == 'rack':
            visible.add('rack_base')
        if worm:
            visible.update(('worm_output', 'worm_diameter', 'worm_starts', 'hand'))
            if output != 'wheel':
                visible.add('worm_length')
        self.controls['thickness'].tooltip = ('锥齿轮沿锥面量取的齿宽。' if kind == 'bevel' else
                                             '蜗轮沿轴线的齿宽。' if worm else '沿轴线的厚度或齿宽。')
        self.controls['module'].tooltip = '填写轴向模数。' if worm else '填写法向模数。' if helix else '填写齿轮模数。'
        self.controls['hand'].tooltip = '填写人字齿底半段的旋向。' if kind == 'herringbone' else '选择左旋或右旋。'
        self.controls['diameter'].tooltip = ('填写目标齿顶外径。' if size == 'outside' else '填写目标分度圆直径。')
        return visible

    def _spec(self):
        kind = self._choice('kind')
        base = defaults(kind)
        values = {}
        for field in fields(GearSpec):
            key = field.name
            control = self.controls[key]
            if key not in self.active_fields:
                values[key] = getattr(base, key)
                continue
            if key in self.choices:
                values[key] = self._choice(key)
            elif key in LENGTHS or key in ANGLES:
                if not control.isValidExpression:
                    raise ValueError(f'「{control.name}」数值无效。')
                values[key] = control.value * 10 if key in LENGTHS else degrees(control.value)
            else:
                values[key] = control.value
        return GearSpec(**values)

    def update(self, changed):
        if self.updating:
            return
        self.updating = True
        self.form_error = ''
        try:
            if self.mode == 'export':
                self.controls['source'].isVisible = self._choice('format') == 'step'
                self.validate()
                return
            ready = self.mode == 'new'
            if not ready:
                try:
                    selected = self._selection()
                    spec = load_spec(selected)
                    if self.mode == 'edit' and (changed == 'source' or not self.selected):
                        self._load(spec)
                    self.selected = selected
                    ready = True
                except ValueError:
                    self.selected = None
            if self.mode == 'new' and self.loaded_kind != self._choice('kind'):
                kind = self._choice('kind')
                self._load(_presets.get(kind, defaults(kind)))
            self._visibility(ready)
            self.validate()
        except Exception:
            _log()
            self.form_error = '无法更新参数面板，请关闭命令后重试。'
            self.controls['feedback'].text = self.form_error
        finally:
            self.updating = False

    def validate(self):
        try:
            if self.form_error:
                raise ValueError(self.form_error)
            _design(for_build=self.mode != 'export')
            if self.mode == 'export' and self._choice('format') == 'f3d':
                self.controls['feedback'].text = '将备份整个当前设计（包括其他组件），保留插件改参信息。'
                return True
            origin = self._selection() if self.mode != 'new' else None
            if self.mode != 'new':
                load_spec(origin)
            if self.mode == 'export':
                message = f'将导出：{origin.component.name}\nSTEP 仅包含实体；想保留改参信息请选择 Fusion 原生档案。'
            else:
                spec = self._spec()
                message = summary(spec, dimensions(spec))
                if self.mode == 'edit':
                    message += '\n改参生成同位置的新组件；原有倒角、键槽、装配关系不会复制。'
            self.controls['feedback'].text = message
            return True
        except (ValueError, RuntimeError) as error:
            self.controls['feedback'].text = str(error)
            return False

    def execute(self):
        if self.form_error:
            raise ValueError(self.form_error)
        design = _design(for_build=self.mode != 'export')
        if self.mode == 'export' and self._choice('format') == 'f3d':
            _queue_export(design, design.rootComponent, 'f3d')
            return
        original = self._selection() if self.mode != 'new' else None
        if self.mode != 'new':
            load_spec(original)
        if self.mode == 'export':
            _queue_export(design, original.component, self._choice('format'))
            return
        spec = self._spec()
        dims = dimensions(spec)
        occurrence = build(design, spec, dims, original.transform2 if original else None)
        if original and self.controls['hide_old'].value:
            original.isLightBulbOn = False
        _presets[spec.kind] = spec
        if self.controls['step'].value:
            try:
                _queue_export(design, occurrence.component, 'step')
            except Exception as error:
                _log()
                _ui.messageBox(f'实体已生成，未启动导出：{error}', TITLE)


class _Created(core.CommandCreatedEventHandler):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def notify(self, args):
        try:
            token = uuid4().hex
            _dialogs[token] = _Dialog(args.command, self.mode, token)
        except Exception as error:
            _log()
            _ui.messageBox(f'无法打开{TITLE}：{error}', TITLE)


def run(context):
    global _app, _ui, _panel, _event
    _app = core.Application.get()
    _ui = _app.userInterface
    try:
        workspace = _ui.workspaces.itemById('FusionSolidEnvironment')
        _panel = workspace.toolbarPanels.itemById('SolidCreatePanel')
        _event = _app.registerCustomEvent(EXPORT_EVENT)
        listener = _Export()
        _event.add(listener)
        _handlers.append(listener)
        for mode, (label, description) in COMMANDS.items():
            command_id = PREFIX + '_' + mode
            definition = _ui.commandDefinitions.addButtonDefinition(command_id, f'{label} · v{VERSION}',
                                                                   f'{description} · {AUTHOR}', '')
            listener = _Created(mode)
            definition.commandCreated.add(listener)
            _handlers.append(listener)
            _panel.controls.addCommand(definition)
    except Exception as error:
        _log()
        stop(context)
        _ui.messageBox(f'{TITLE}启动失败：{error}', TITLE)


def stop(context):
    global _event
    if _ui:
        for mode in COMMANDS:
            command_id = PREFIX + '_' + mode
            control = _panel.controls.itemById(command_id) if _panel else None
            if control:
                control.deleteMe()
            definition = _ui.commandDefinitions.itemById(command_id)
            if definition:
                definition.deleteMe()
    if _event:
        _app.unregisterCustomEvent(EXPORT_EVENT)
        _event = None
    _handlers.clear()
    _dialogs.clear()
    _pending.clear()
