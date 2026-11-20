from contextlib import contextmanager
from dataclasses import asdict
from json import dumps, loads
from math import ceil, cos, radians, sin

import adsk.core as core
import adsk.fusion as fusion

from .gear_geometry import GearSpec, TYPES, gear_outline, rack_outline, worm_outline


ATTRIBUTE_GROUP = 'InitialGearWorkbench'
ATTRIBUTE_KEY = 'gear_spec_v1'
OP = fusion.FeatureOperations


def _require(result, message):
    if not result:
        raise RuntimeError(message)
    return result


def _collection(items):
    result = core.ObjectCollection.create()
    for item in items:
        result.add(item)
    return result


@contextmanager
def _stage(label):
    try:
        yield
    except RuntimeError as error:
        raise RuntimeError(f'{label}：{error}') from error


def _length(value):
    return core.ValueInput.createByReal(value / 10)


def _point(x_mm, y_mm, z_mm=0.0):
    return core.Point3D.create(x_mm / 10, y_mm / 10, z_mm / 10)


def _plane(comp, height, name):
    if abs(height) < 1e-8:
        return comp.xYConstructionPlane
    planes = comp.constructionPlanes
    definition = planes.createInput()
    _require(definition.setByOffset(comp.xYConstructionPlane, _length(height)), '不能设置轴向基准面。')
    plane = _require(planes.add(definition), '不能创建轴向基准面。')
    plane.name = name
    plane.isLightBulbOn = False
    return plane


def _one_profile(sketch):
    profiles = sketch.profiles
    count = profiles.count
    if count != 1:
        raise RuntimeError(f'「{sketch.name}」应有 1 个闭合轮廓，Fusion 检测到 {count} 个。')
    return _require(profiles.item(0), f'不能读取「{sketch.name}」的闭合轮廓。')


def _outline(comp, segments, height, name, scale=1.0, phase=0.0):
    sketch = _require(comp.sketches.add(_plane(comp, height, name + '基准面')), f'不能创建「{name}」草图。')
    sketch.name = name
    curves = sketch.sketchCurves
    cosine, sine = cos(phase), sin(phase)

    def convert(point):
        x_mm, y_mm = point
        return sketch.modelToSketchSpace(_point(scale * (x_mm * cosine - y_mm * sine),
                                                scale * (x_mm * sine + y_mm * cosine), height))

    with _stage(f'绘制「{name}」'):
        sketch.isComputeDeferred = True
        try:
            joints = [_require(sketch.sketchPoints.add(convert(points[0])), '不能创建齿形节点。')
                      for _, points in segments]
            for index, (kind, points) in enumerate(segments):
                start, end = joints[index], joints[(index + 1) % len(joints)]
                with _stage(f'第 {index + 1}/{len(segments)} 段曲线（{kind}）'):
                    if kind == 'line':
                        curve = curves.sketchLines.addByTwoPoints(start, end)
                    elif kind == 'arc':
                        curve = curves.sketchArcs.addByThreePoints(start, convert(points[1]), end)
                    else:
                        fit = _collection([start, *(convert(point) for point in points[1:-1]), end])
                        curve = curves.sketchFittedSplines.add(fit)
                    _require(curve, 'Fusion 没有返回草图曲线。')
        finally:
            sketch.isComputeDeferred = False
    return sketch, _one_profile(sketch)


def _circle(comp, radius, name, height=0.0):
    sketch = comp.sketches.add(_plane(comp, height, name + '基准面'))
    sketch.name = name
    sketch.sketchCurves.sketchCircles.addByCenterRadius(core.Point3D.create(0, 0, 0), radius / 10)
    return sketch, _one_profile(sketch)


def _feature(feature, name):
    _require(feature, f'Fusion 无法创建「{name}」。')
    if feature.healthState == fusion.FeatureHealthStates.ErrorFeatureHealthState:
        raise RuntimeError(f'「{name}」失败：{feature.errorOrWarningMessage}')
    feature.name = name
    return feature


def _extrude(comp, profile, length, name, operation=OP.NewBodyFeatureOperation, target=None):
    with _stage(f'拉伸「{name}」'):
        extrudes = comp.features.extrudeFeatures
        definition = _require(extrudes.createInput(profile, operation), '不能读取截面。')
        extent = fusion.DistanceExtentDefinition.create(_length(length))
        _require(definition.setOneSideExtent(extent, fusion.ExtentDirections.PositiveExtentDirection), '不能设置厚度。')
        if target is not None:
            definition.participantBodies = [target]
        return _feature(extrudes.add(definition), name)


def _sweep(comp, profile, start, end, twist, name, operation=OP.NewBodyFeatureOperation, target=None):
    with _stage(f'「{name}」的扫掠路径'):
        path_sketch = _require(comp.sketches.add(comp.xZConstructionPlane), '不能创建轴线草图。')
        path_sketch.name = name + ' · 轴线'
        points = [path_sketch.modelToSketchSpace(_point(0, 0, height)) for height in (start, end)]
        line = _require(path_sketch.sketchCurves.sketchLines.addByTwoPoints(*points), '不能生成轴线。')
        path = _require(comp.features.createPath(line, False), '不能读取轴线路径。')
    with _stage(f'扫掠「{name}」'):
        sweeps = comp.features.sweepFeatures
        definition = _require(sweeps.createInput(profile, path, operation), '不能初始化扫掠。')
        definition.twistAngle = core.ValueInput.createByReal(twist)
        if target is not None:
            definition.participantBodies = [target]
        feature = _feature(sweeps.add(definition), name)
    path_sketch.isVisible = False
    return feature


def _solid(comp, name):
    bodies = comp.bRepBodies
    if bodies.count != 1 or not bodies.item(0).isSolid:
        raise RuntimeError(f'「{name}」未生成预期的单一封闭实体。')
    body = bodies.item(0)
    body.name = name
    return body


def _bore(comp, diameter, width, name):
    if diameter > 0:
        sketch, profile = _circle(comp, diameter / 2, '轴孔截面')
        _extrude(comp, profile, width + 0.5, '贯穿轴孔', OP.CutFeatureOperation, _solid(comp, name))
        sketch.isVisible = False


def _cylindrical(comp, spec, dims, name, phase=0.0):
    ring = spec.kind == 'ring'
    if ring:
        blank_sketch, blank = _circle(comp, dims.outer_d / 2, '齿圈毛坯外圆')
        _extrude(comp, blank, dims.axial_width, '齿圈毛坯')
        blank_sketch.isVisible = False
    sketch, profile = _outline(comp, gear_outline(spec, dims), 0, '内齿槽截面' if ring else '渐开线齿形截面', phase=phase)
    operation = OP.CutFeatureOperation if ring else OP.NewBodyFeatureOperation
    target = _solid(comp, name) if ring else None
    if spec.kind == 'herringbone' and abs(dims.twist) > 1e-7:
        half = dims.axial_width / 2
        first = _sweep(comp, profile, 0, half, dims.twist / 2, '人字齿第一段')
        middle = _plane(comp, half, '人字齿对称面')
        mirrors = comp.features.mirrorFeatures
        mirror_input = mirrors.createInput(_collection([first.bodies.item(0)]), middle)
        mirror_input.isCombine = True
        _feature(mirrors.add(mirror_input), '人字齿镜像')
        bodies = comp.bRepBodies
        if bodies.count == 2:
            combines = comp.features.combineFeatures
            join_input = combines.createInput(bodies.item(0), _collection([bodies.item(1)]))
            join_input.operation = OP.JoinFeatureOperation
            join_input.isKeepToolBodies = False
            _feature(combines.add(join_input), '合并人字齿两半')
    elif abs(dims.twist) > 1e-7:
        _sweep(comp, profile, 0, dims.axial_width, dims.twist, '螺旋内齿切除' if ring else '螺旋扫掠', operation, target)
    else:
        _extrude(comp, profile, dims.axial_width, '内齿切除' if ring else '齿形拉伸', operation, target)
    sketch.isVisible = False
    if not ring:
        _bore(comp, spec.bore, dims.axial_width, name)
    _solid(comp, name)


def _bevel(comp, spec, dims, name):
    segments = gear_outline(spec, dims)
    large_sketch, large = _outline(comp, segments, 0, '锥齿大端截面')
    small_sketch, small = _outline(comp, segments, dims.axial_width, '锥齿小端截面', dims.cone_scale)
    lofts = comp.features.loftFeatures
    definition = lofts.createInput(OP.NewBodyFeatureOperation)
    definition.isSolid = True
    definition.loftSections.add(large)
    definition.loftSections.add(small)
    with _stage('锥齿放样'):
        _feature(lofts.add(definition), '锥齿放样（近似）')
    large_sketch.isVisible = small_sketch.isVisible = False
    _bore(comp, spec.bore, dims.axial_width, name)
    _solid(comp, name)


def _end_face(body, height):
    for face in body.faces:
        bounds = face.boundingBox
        if (core.Plane.cast(face.geometry) and abs(bounds.minPoint.z - height / 10) < 1e-5
                and abs(bounds.maxPoint.z - height / 10) < 1e-5):
            return face
    raise RuntimeError('未找到蜗杆扫掠的轴向端面。')


def _worm(comp, spec, dims, name):
    sketch, profile = _outline(comp, worm_outline(spec, dims), 0, '蜗杆轴向梯形导出的横截面')
    count = max(1, ceil(abs(dims.worm_twist) / radians(150)))
    length = spec.worm_length / count
    twist = dims.worm_twist / count
    for index in range(count):
        start = index * length
        operation = OP.NewBodyFeatureOperation if index == 0 else OP.JoinFeatureOperation
        _sweep(comp, profile, start, (index + 1) * length, twist, f'蜗杆螺旋扫掠 {index + 1}', operation)
        if index + 1 < count:
            with _stage(f'读取蜗杆第 {index + 1} 段端面'):
                profile = _end_face(_solid(comp, name), (index + 1) * length)
    sketch.isVisible = False
    _solid(comp, name)


def _component(parent, name):
    occurrence = _require(parent.occurrences.addNewComponent(core.Matrix3D.create()),
                          '不能创建组件。请在混合设计中运行。')
    occurrence.component.name = name
    return occurrence


def _place(occurrence, transform):
    timeline = occurrence.component.parentDesign.timeline
    position = timeline.markerPosition
    _require(occurrence.timelineObject.rollTo(False), '不能定位至新组件的创建步骤。')
    try:
        occurrence.initialTransform = transform
    finally:
        timeline.markerPosition = position


def _worm_pair(comp, spec, dims):
    wheel_name = f'近似蜗轮 {dims.teeth}T'
    wheel = _component(comp, wheel_name)
    _cylindrical(wheel.component, spec, dims, wheel_name)
    screw_name = f'蜗杆 {spec.worm_starts} 头'
    screw = _component(comp, screw_name)
    _worm(screw.component, spec, dims, screw_name)
    rotation = core.Matrix3D.create()
    rotation.setToRotation(radians(90), core.Vector3D.create(0, 1, 0), _point(0, 0))
    rotation.translation = core.Vector3D.create(-spec.worm_length / 20, dims.center_distance / 10,
                                                dims.axial_width / 20)
    with _stage('放置蜗杆'):
        _place(screw, rotation)


def build(design, spec, dims, placement=None):
    name = f'{TYPES[spec.kind]} {dims.teeth}T · m{spec.module:g}'
    if spec.kind == 'worm' and spec.worm_output != 'pair':
        name = (f'蜗杆 {spec.worm_starts} 头 · m{spec.module:g}' if spec.worm_output == 'screw'
                else f'近似蜗轮 {dims.teeth}T · m{spec.module:g}')
    occurrence = _component(design.rootComponent, name)
    comp = occurrence.component
    if spec.kind == 'rack':
        sketch, profile = _outline(comp, rack_outline(spec, dims), 0, '直齿齿条截面')
        _extrude(comp, profile, dims.axial_width, '齿条拉伸')
        sketch.isVisible = False
        _solid(comp, name)
    elif spec.kind == 'bevel':
        _bevel(comp, spec, dims, name)
    elif spec.kind == 'worm' and spec.worm_output == 'pair':
        _worm_pair(comp, spec, dims)
    elif spec.kind == 'worm' and spec.worm_output == 'screw':
        _worm(comp, spec, dims, name)
    else:
        _cylindrical(comp, spec, dims, name)
    if placement is not None:
        with _stage('恢复组件位置'):
            _place(occurrence, placement)
    _require(comp.attributes.add(ATTRIBUTE_GROUP, ATTRIBUTE_KEY, dumps(asdict(spec))), '不能保存改参信息。')
    return occurrence


def load_spec(occurrence):
    if not occurrence or occurrence.assemblyContext or occurrence.isReferencedComponent:
        raise ValueError('请在浏览器中选择插件生成的顶层本地组件。蜗轮蜗杆组选父组件。')
    attr = occurrence.component.attributes.itemByName(ATTRIBUTE_GROUP, ATTRIBUTE_KEY)
    if not attr:
        raise ValueError('该组件没有齿轮工坊参数。请选插件生成的 Fusion 原生组件，而非导入的 STEP。')
    try:
        return GearSpec(**loads(attr.value))
    except (TypeError, ValueError) as error:
        raise ValueError('该组件的齿轮工坊参数无法读取。') from error
