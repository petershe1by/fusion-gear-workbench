from dataclasses import replace
from importlib import import_module
from pathlib import Path
from sys import modules
from tempfile import TemporaryDirectory
from traceback import format_exc
from types import ModuleType
from uuid import uuid4

import adsk.core as core
import adsk.fusion as fusion


def solid_bodies(component):
    bodies = list(component.bRepBodies)
    for occurrence in component.occurrences:
        bodies.extend(solid_bodies(occurrence.component))
    return bodies


def check_bodies(component, expected):
    bodies = solid_bodies(component)
    assert len(bodies) == expected, f'Expected {expected} bodies, got {len(bodies)}'
    assert all(body.isSolid and body.volume > 0 for body in bodies), 'Invalid solid'
    return bodies


def check_step(app, design, occurrence, expected):
    with TemporaryDirectory() as directory:
        path = str(Path(directory) / 'gear.step')
        export = design.exportManager
        assert export.execute(export.createSTEPExportOptions(path, occurrence.component)), 'STEP export failed'
        importer = app.importManager
        imported = importer.importToNewDocument(importer.createSTEPImportOptions(path))
        assert imported, 'STEP import failed'
        try:
            check_bodies(fusion.Design.cast(app.activeProduct).rootComponent, expected)
        finally:
            imported.close(False)


def check_worm_pair(occurrence, spec, dims):
    screw = occurrence.component.occurrences.item(1).createForAssemblyContext(occurrence)
    bounds = screw.bRepBodies.item(0).preciseBoundingBox
    assert abs((bounds.maxPoint.x - bounds.minPoint.x) * 10 - spec.worm_length) < 0.01, 'Wrong screw length or axis'
    assert abs((bounds.minPoint.x + bounds.maxPoint.x) * 5) < 0.01, 'Wrong screw axial position'
    assert abs((bounds.minPoint.y + bounds.maxPoint.y) * 5 - dims.center_distance) < 0.01, 'Wrong pair center distance'


def load_modules():
    project = Path(__file__).resolve().parents[1]
    package = ModuleType('_gear_smoke_' + uuid4().hex)
    package.__path__ = [str(project / 'FusionGearWorkbench')]
    modules[package.__name__] = package
    return project, package.__name__


def new_design(app):
    doc = app.documents.add(core.DocumentTypes.FusionDesignDocumentType)
    design = fusion.Design.cast(app.activeProduct)
    if design.designIntent != fusion.DesignIntentTypes.HybridDesignIntentType:
        design.designIntent = fusion.DesignIntentTypes.HybridDesignIntentType
    return doc, design


def run():
    project, package = load_modules()
    model = import_module(package + '.fusion_model')
    geometry = import_module(package + '.gear_geometry')
    app = core.Application.get()
    previous = app.activeDocument
    report = [f'Fusion {app.version}']
    gear = geometry.defaults
    cases = {
        'spur': gear('spur'),
        'helical': gear('helical'),
        'helical_left': replace(gear('helical'), helix=35, hand=-1),
        'herringbone': gear('herringbone'),
        'ring_helical': replace(gear('ring'), ring_helical=True),
        'worm_pair': gear('worm'),
        'worm_wheel': replace(gear('worm'), worm_output='wheel'),
        'worm_screw': replace(gear('worm'), worm_output='screw'),
    }
    try:
        for label, spec in cases.items():
            if label not in globals().get('only', cases):
                continue
            doc, design = new_design(app)
            try:
                placement = None
                if label == 'helical_left':
                    placement = core.Matrix3D.create()
                    placement.setToRotation(0.6, core.Vector3D.create(1, 0, 0), core.Point3D.create(0, 0, 0))
                    placement.translation = core.Vector3D.create(2, 3, 4)
                dims = geometry.dimensions(spec)
                occurrence = model.build(design, spec, dims, placement)
                if placement is not None:
                    assert all(abs(actual - expected) < 1e-6 for actual, expected in
                               zip(occurrence.transform2.asArray(), placement.asArray())), 'Wrong edit placement'
                expected = 2 if label == 'worm_pair' else 1
                bodies = check_bodies(occurrence.component, expected)
                if expected == 1:
                    bounds = bodies[0].boundingBox
                    width = spec.worm_length if label == 'worm_screw' else dims.axial_width
                    assert abs(10 * (bounds.maxPoint.z - bounds.minPoint.z) - width) < 0.01, 'Wrong axial width'
                else:
                    check_worm_pair(occurrence, spec, dims)
                assert model.load_spec(occurrence) == spec, 'Gear spec was not preserved'
                step_checked = globals().get('verify_step') and label in ('helical', 'herringbone', 'worm_pair')
                if step_checked:
                    check_step(app, design, occurrence, expected)
                report.append(f'PASS {label}: {expected} solid(s)' + ('; STEP round-trip' if step_checked else ''))
            except Exception:
                report.append(f'FAIL {label}\n{format_exc()}')
            finally:
                doc.close(False)
    finally:
        if previous:
            previous.activate()
        (project / 'tests' / 'fusion-smoke-result.txt').write_text('\n'.join(report) + '\n', encoding='utf-8')
        app.log('齿轮工坊：Fusion 本机验证结束。')


def open_test_panel():
    _, package = load_modules()
    plugin = import_module(package + '.FusionGearWorkbench')
    plugin.PREFIX = plugin.PREFIX + 'Smoke'
    plugin.EXPORT_EVENT = plugin.EXPORT_EVENT + 'Smoke'
    plugin._presets['spur'] = plugin.defaults('worm')
    app = core.Application.get()
    previous = app.activeDocument
    doc, design = new_design(app)
    plugin.run(None)
    app.userInterface.commandDefinitions.itemById(plugin.PREFIX + '_new').execute()
    return plugin, doc, design, previous


if globals().get('verify_ui'):
    preview = open_test_panel()
else:
    run()
