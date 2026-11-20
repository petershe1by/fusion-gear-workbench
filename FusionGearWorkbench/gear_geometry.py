from dataclasses import dataclass, replace
from math import atan, cos, degrees, floor, hypot, isfinite, pi, radians, sin, sqrt, tan, tau


TYPES = {
    'spur': '直齿圆柱齿轮', 'helical': '斜齿圆柱齿轮', 'herringbone': '人字齿轮',
    'ring': '内齿圈', 'bevel': '直齿锥齿轮（近似）',
    'worm': '蜗轮蜗杆（蜗轮近似）', 'rack': '直齿齿条',
}


@dataclass(frozen=True)
class GearSpec:
    kind: str = 'spur'
    size_mode: str = 'pitch'
    diameter: float = 48.0
    teeth: int = 24
    module: float = 2.0
    thickness: float = 8.0
    pressure: float = 20.0
    helix: float = 20.0
    hand: int = 1
    bore: float = 6.0
    backlash: float = 0.0
    ring_wall: float = 4.0
    ring_helical: bool = False
    mate_teeth: int = 32
    rack_length: float = 100.0
    rack_base: float = 5.0
    worm_diameter: float = 20.0
    worm_length: float = 24.0
    worm_starts: int = 1
    worm_output: str = 'pair'


def defaults(kind):
    base = GearSpec(kind=kind)
    if kind == 'ring':
        return replace(base, diameter=80.0, teeth=40, bore=0.0)
    if kind == 'worm':
        return replace(base, diameter=64.0, teeth=32, backlash=0.1)
    if kind == 'rack':
        return replace(base, teeth=16, bore=0.0)
    return base


@dataclass(frozen=True)
class Dimensions:
    teeth: int
    pitch_d: float = 0.0
    tip_d: float = 0.0
    root_d: float = 0.0
    outer_d: float = 0.0
    pressure_t: float = 0.0
    helix: float = 0.0
    twist: float = 0.0
    backlash_t: float = 0.0
    bevel_angle: float = 0.0
    cone_scale: float = 1.0
    axial_width: float = 0.0
    rack_length: float = 0.0
    worm_lead: float = 0.0
    worm_twist: float = 0.0
    center_distance: float = 0.0
    warnings: tuple = ()


def _bounded(value, low, high, label):
    if not isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}须在 {low:.4g}～{high:.4g} 之间。')
    return value


def _integer(value, low, high, label):
    _bounded(value, low, high, label)
    if int(value) != value:
        raise ValueError(f'{label}须为整数。')
    return int(value)


def size_modes(kind):
    if kind == 'rack':
        return {'pitch': '按目标长度（整齿）', 'teeth': '按齿数'}
    modes = {'pitch': '按分度圆直径', 'teeth': '按齿数'}
    if kind in ('spur', 'helical', 'herringbone'):
        modes['outside'] = '按齿顶外径'
    return modes


def dimensions(spec):
    if spec.kind not in TYPES or spec.hand not in (-1, 1):
        raise ValueError('未知的齿轮类型或旋向。')
    module = _bounded(spec.module, 0.2, 50, '模数（mm）')
    alpha = radians(_bounded(spec.pressure, 14.5, 25, '压力角（°）'))
    backlash = _bounded(spec.backlash, 0, 0.5 * module, '齿厚减薄量（mm）')
    worm, rack, ring, bevel = (spec.kind == kind for kind in ('worm', 'rack', 'ring', 'bevel'))
    if spec.size_mode not in size_modes(spec.kind):
        raise ValueError('当前类型不支持该尺寸输入方式。')
    beta = lead = worm_twist = 0.0
    warnings = []
    if worm:
        if spec.worm_output not in ('pair', 'wheel', 'screw'):
            raise ValueError('未知的蜗轮蜗杆输出方式。')
        starts = _integer(spec.worm_starts, 1, 4, '蜗杆头数')
        _bounded(spec.worm_diameter, 6 * module, 25 * module, '蜗杆分度圆直径（mm）')
        beta = atan(starts * module / spec.worm_diameter)
        lead = pi * module * starts
        if spec.worm_output != 'wheel':
            _bounded(spec.worm_length, 0.5 * module, 12 * lead, '蜗杆有齿长度（mm）')
            worm_twist = spec.hand * tau * spec.worm_length / lead
        if spec.worm_output == 'screw':
            return Dimensions(0, helix=beta, backlash_t=backlash, worm_lead=lead, worm_twist=worm_twist)
        warnings.append('蜗轮为圆柱斜齿近似，未做滚刀包络、干涉与接触校核。')
    elif spec.kind in ('helical', 'herringbone') or (ring and spec.ring_helical):
        beta = radians(_bounded(spec.helix, 0, 45, '螺旋角（°）'))
    _bounded(spec.thickness, 0.2, 1000, '厚度/齿宽（mm）')
    module_t = module if worm else module / cos(beta)
    alpha_t = alpha if worm else atan(tan(alpha) / cos(beta))
    backlash_t = backlash if worm else backlash / cos(beta)
    if spec.size_mode == 'teeth':
        teeth = spec.teeth
    elif rack:
        requested = _bounded(spec.rack_length, 0.1, 30000, '目标齿条长度（mm）')
        teeth = floor(requested / (pi * module) + 0.5)
    else:
        requested = _bounded(spec.diameter, 0.1, 20000, '目标直径（mm）')
        teeth = floor((requested - (2 * module if spec.size_mode == 'outside' else 0)) / module_t + 0.5)
    minimum = 1 if rack else 18 if ring else 12 if worm else 8
    teeth = _integer(teeth, minimum, 180, '齿数（检查直径与模数）')
    if rack:
        _bounded(spec.rack_base, 0.2, 500, '齿根以下底座高度（mm）')
        length = pi * module * teeth
        if spec.size_mode == 'pitch' and abs(length - spec.rack_length) > 1e-6:
            warnings.append('目标长度已按最接近的整齿数取整。')
        return Dimensions(teeth, pressure_t=alpha, backlash_t=backlash, axial_width=spec.thickness,
                          rack_length=length, warnings=tuple(warnings))
    pitch_d = teeth * module_t
    bevel_angle = 0.0
    cone_scale = 1.0
    axial_width = spec.thickness
    if bevel:
        mate = _integer(spec.mate_teeth, 8, 180, '配对齿轮齿数')
        bevel_angle = atan(teeth / mate)
        cone_distance = hypot(teeth, mate) * module / 2
        if spec.thickness > cone_distance / 3:
            raise ValueError(f'锥齿轮沿锥面齿宽须 ≤ {cone_distance / 3:.3f} mm（锥距的 1/3）。')
        cone_scale -= spec.thickness / cone_distance
        axial_width *= cos(bevel_angle)
        warnings.append('锥齿轮为 90° 轴交角的缩放截面近似，非标准展成齿面。')
    addendum = module * cos(bevel_angle)
    tip_d = pitch_d + (-2 if ring else 2) * addendum
    root_d = pitch_d + (2 if ring else -2) * 1.25 * addendum
    outer_d = tip_d
    if ring:
        outer_d = root_d + 2 * _bounded(spec.ring_wall, 0.2, 500, '齿根外圈径向壁厚（mm）')
        if tip_d < pitch_d * cos(alpha_t):
            warnings.append('内齿齿顶进入基圆以内；该段用径向齿面近似。')
    else:
        _bounded(spec.bore, 0, root_d * cone_scale - 0.4, '孔径（mm）')
        if teeth / (cos(beta) ** 3 * cos(bevel_angle)) < 2 / sin(alpha) ** 2:
            warnings.append('少齿数可能根切；本工具不生成滚刀根切或过渡圆角。')
    if spec.size_mode != 'teeth':
        actual = tip_d if spec.size_mode == 'outside' else pitch_d
        if abs(actual - spec.diameter) > 1e-6:
            warnings.append('目标直径已按最接近的整齿数取整。')
    twist = spec.hand * 2 * axial_width * tan(beta) / pitch_d
    if not worm and abs(twist) > radians(90 if spec.kind == 'herringbone' else 45):
        raise ValueError('齿宽与螺旋角组合造成过长的螺旋；请减小齿宽或螺旋角。')
    result = Dimensions(teeth, pitch_d, tip_d, root_d, outer_d, alpha_t, beta, twist, backlash_t,
                        bevel_angle, cone_scale, axial_width, worm_lead=lead, worm_twist=worm_twist,
                        center_distance=(pitch_d + spec.worm_diameter) / 2 if worm else 0.0,
                        warnings=tuple(warnings))
    _gear_shape(spec, result)
    return result


def _polar(radius, angle):
    return radius * cos(angle), radius * sin(angle)


def _arc(radius, start, end):
    return 'arc', tuple(_polar(radius, angle) for angle in (start, (start + end) / 2, end))


def _roll(radius, base):
    return sqrt(max(0, (radius / base) ** 2 - 1))


def _gear_shape(spec, dims):
    inner, outer = sorted((dims.tip_d / 2, dims.root_d / 2))
    pitch = dims.pitch_d / 2
    base = pitch * cos(dims.pressure_t)
    step = tau / dims.teeth
    sign = 1 if spec.kind == 'ring' else -1
    half_pitch = step / 4 + sign * dims.backlash_t / (2 * pitch)
    inv_pitch = tan(dims.pressure_t) - dims.pressure_t
    bevel_factor = cos(dims.bevel_angle)

    def half_width(radius):
        roll = _roll(radius, base)
        return half_pitch + bevel_factor * (inv_pitch - roll + atan(roll))

    start = max(inner, base)
    if start - inner < 0.001:
        start = inner
    start_roll, end_roll = _roll(start, base), _roll(outer, base)
    samples = tuple(base * sqrt(1 + (start_roll + (end_roll - start_roll) * index / 9) ** 2)
                    for index in range(1, 9))
    radii = (start, *samples, outer)
    if outer * half_width(outer) < 0.004 or inner * (step / 2 - half_width(inner)) < 0.004:
        raise ValueError('参数使齿顶过尖或齿根槽过窄；请调整齿数、压力角或齿厚减薄量。')
    return inner, outer, half_width, radii


def _radial_outline(count, inner, outer, half_width, radii):
    step = tau / count
    root_half = half_width(inner)
    tip_half = half_width(outer)
    has_extension = radii[0] != inner
    for tooth in range(count):
        center = tooth * step
        left = tuple(_polar(radius, center - half_width(radius)) for radius in radii)
        right = tuple(_polar(radius, center + half_width(radius)) for radius in reversed(radii))
        if has_extension:
            yield 'line', (_polar(inner, center - root_half), left[0])
        yield 'spline', left
        yield _arc(outer, center - tip_half, center + tip_half)
        yield 'spline', right
        if has_extension:
            yield 'line', (right[-1], _polar(inner, center + root_half))
        yield _arc(inner, center + root_half, (tooth + 1) * step - root_half)


def gear_outline(spec, dims):
    return tuple(_radial_outline(dims.teeth, *_gear_shape(spec, dims)))


def worm_outline(spec, dims):
    pitch = spec.worm_diameter / 2
    root, tip = pitch - 1.25 * spec.module, pitch + spec.module
    alpha = radians(spec.pressure)

    def half_width(radius):
        return tau / dims.worm_lead * (pi * spec.module / 4 - dims.backlash_t / 2 - (radius - pitch) * tan(alpha))

    radii = (root, *(root + (tip - root) * index / 9 for index in range(1, 9)), tip)
    return tuple(_radial_outline(spec.worm_starts, root, tip, half_width, radii))


def rack_outline(spec, dims):
    pitch = pi * spec.module
    root, tip = -1.25 * spec.module, spec.module
    half = pitch / 4 - dims.backlash_t / 2
    root_half = half - root * tan(dims.pressure_t)
    tip_half = half - tip * tan(dims.pressure_t)
    points = [(0.0, root - spec.rack_base), (dims.rack_length, root - spec.rack_base), (dims.rack_length, root)]
    for tooth in reversed(range(dims.teeth)):
        center = (tooth + 0.5) * pitch
        points.extend(((center + root_half, root), (center + tip_half, tip),
                       (center - tip_half, tip), (center - root_half, root)))
    points.append((0.0, root))
    return tuple(('line', (start, end)) for start, end in zip(points, points[1:] + points[:1]))


def summary(spec, dims):
    if spec.kind == 'rack':
        main = f'{dims.teeth} 齿｜实际长度 {dims.rack_length:.3f} mm｜齿距 {pi * spec.module:.3f} mm'
    elif spec.kind == 'worm' and spec.worm_output == 'screw':
        main = f'{spec.worm_starts} 头｜蜗杆分度 Ø{spec.worm_diameter:.3f} mm｜齿顶 Ø{spec.worm_diameter + 2 * spec.module:.3f} mm'
    else:
        main = (f'{dims.teeth} 齿｜分度 Ø{dims.pitch_d:.3f} mm｜'
                f'齿顶 Ø{dims.tip_d:.3f} mm｜齿根 Ø{dims.root_d:.3f} mm')
    if spec.kind == 'ring':
        main += f'\n外圈 Ø{dims.outer_d:.3f} mm'
    if spec.kind == 'bevel':
        main += f'\n分度锥角 {degrees(dims.bevel_angle):.2f}°｜轴向厚度 {dims.axial_width:.3f} mm'
    if spec.kind == 'worm':
        main += f'\n蜗杆导程 {dims.worm_lead:.3f} mm｜导程角 {degrees(dims.helix):.2f}°'
        if spec.worm_output != 'screw':
            main += f'｜理论中心距 {dims.center_distance:.3f} mm｜传动比 {dims.teeth / spec.worm_starts:.2f}:1'
    if dims.warnings:
        main += '\n' + '\n'.join(f'注意：{warning}' for warning in dims.warnings)
    return main
