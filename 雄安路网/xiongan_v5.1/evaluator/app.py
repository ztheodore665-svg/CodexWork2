#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SUMO 交通红绿灯算法评价平台 - Flask 后端
"""

import os
import sys
import json
import glob
import subprocess
import datetime
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict

from flask import Flask, render_template, request, jsonify, send_from_directory, Response

# 添加项目根目录和 scripts 目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, 'scripts')
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, PROJECT_ROOT)

from simulation_core import (
    find_sumo_binary,
    find_sumo_cli_binary,
    validate_project,
    load_intersection_configs,
    generate_add_xml,
    generate_sumocfg,
    get_project_info
)

from optimizer import optimize as optimizer_optimize

app = Flask(__name__)
EVALUATOR_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVALUATOR_DIR, 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)

# 仿真锁，防止同时运行多个仿真
simulation_lock = False


def scan_projects(root_path=None):
    """递归扫描有效项目目录"""
    if root_path is None:
        root_path = PROJECT_ROOT
    root_path = os.path.abspath(root_path)

    projects = []
    # 扫描深度限制为 3 层
    for dirpath, dirnames, _ in os.walk(root_path):
        depth = dirpath.replace(root_path, '').count(os.sep)
        if depth > 3:
            dirnames.clear()
            continue

        network_dir = os.path.join(dirpath, 'network')
        intersections_dir = os.path.join(dirpath, 'intersections')
        if os.path.isdir(network_dir) and os.path.isdir(intersections_dir):
            net_files = glob.glob(os.path.join(network_dir, '*.net.xml'))
            json_files = glob.glob(os.path.join(intersections_dir, '*_config.json'))
            if net_files and json_files:
                try:
                    configs = load_intersection_configs(intersections_dir)
                    name = os.path.basename(dirpath)
                    if dirpath == PROJECT_ROOT:
                        name = '雄安新区（全部路口）'
                    elif dirpath.startswith(os.path.join(PROJECT_ROOT, 'projects')):
                        name = os.path.basename(dirpath)
                    projects.append({
                        'path': dirpath,
                        'name': name,
                        'intersection_count': len(configs),
                        'has_routes': len(glob.glob(os.path.join(network_dir, '*.rou.xml'))) > 0
                    })
                except Exception:
                    pass

    projects.sort(key=lambda p: (-p['intersection_count'], p['name']))
    return projects


def parse_tripinfo(tripinfo_path):
    """解析 tripinfo.xml，返回车辆统计列表"""
    vehicles = []
    if not os.path.exists(tripinfo_path):
        return vehicles

    tree = ET.parse(tripinfo_path)
    root = tree.getroot()

    for trip in root.findall('tripinfo'):
        try:
            v = {
                'id': trip.get('id', ''),
                'depart': float(trip.get('depart', 0)),
                'duration': float(trip.get('duration', 0)),
                'routeLength': float(trip.get('routeLength', 0)),
                'waitTime': float(trip.get('waitTime', 0)),
                'waitCount': int(float(trip.get('waitCount', 0))),
                'timeLoss': float(trip.get('timeLoss', 0)),
                'departDelay': float(trip.get('departDelay', 0)),
                'vType': trip.get('vType', ''),
                'arrival': float(trip.get('arrival', 0)) if trip.get('arrival') else None,
                'completed': trip.get('arrival') is not None
            }
            vehicles.append(v)
        except (ValueError, TypeError):
            continue

    return vehicles


def compute_overall_stats(vehicles, edge_stats=None, intersection_data=None):
    """从车辆列表计算总体统计指标。
    主指标（avg_wait_time / total_wait_time）使用 edgedata。
    avg_wait_per_intersection: 先算每辆车经过各路口的等待，再取所有车的平均。
      等价于 total_wait / total_vehicle_passages（所有路口的车辆通过次数之和）。
    """
    if not vehicles:
        return {}

    completed = [v for v in vehicles if v['completed']]
    total = len(vehicles)

    edgedata_wait = 0.0
    if edge_stats:
        edgedata_wait = round(sum(e['waitingTime'] for e in edge_stats.values()), 2)

    avg_wait = round(edgedata_wait / total, 2) if total > 0 else 0

    # 每车每路口等待 = total_wait / sum(各路口车流量)
    # 即所有车辆-路口交汇的平均等待时间
    total_passages = 0
    if intersection_data:
        total_passages = sum(v['vehicle_count'] for v in intersection_data.values())
    avg_wait_per_inter = round(edgedata_wait / total_passages, 2) if total_passages > 0 else 0

    stats = {
        'total_vehicles': total,
        'completed_vehicles': len(completed),
        'incomplete_vehicles': total - len(completed),
        'completion_rate': round(len(completed) / total * 100, 1) if total > 0 else 0,
        'avg_wait_time': avg_wait,
        'total_wait_time': edgedata_wait,
        'avg_wait_per_intersection': avg_wait_per_inter,
        'intersection_count': len(intersection_data) if intersection_data else 0,
        'total_vehicle_passages': total_passages,
        'avg_time_loss': round(sum(v['timeLoss'] for v in vehicles) / total, 2),
        'total_time_loss': round(sum(v['timeLoss'] for v in vehicles), 2),
    }

    if completed:
        stats['avg_duration'] = round(sum(v['duration'] for v in completed) / len(completed), 2)
        stats['avg_route_length'] = round(sum(v['routeLength'] for v in completed) / len(completed), 2)
        speeds = [v['routeLength'] / v['duration'] * 3.6 for v in completed if v['duration'] > 0]
        stats['avg_speed'] = round(sum(speeds) / len(speeds), 2) if speeds else 0
        stats['avg_depart_delay'] = round(sum(v['departDelay'] for v in vehicles) / total, 2)
    else:
        stats['avg_duration'] = 0
        stats['avg_route_length'] = 0
        stats['avg_speed'] = 0
        stats['avg_depart_delay'] = 0

    return stats


def parse_edges(edge_path):
    """解析 edge.xml，返回各边统计数据"""
    edges = {}
    if not os.path.exists(edge_path):
        return edges

    tree = ET.parse(edge_path)
    root = tree.getroot()

    for interval in root.findall('interval'):
        for edge in interval.findall('edge'):
            try:
                departed = int(float(edge.get('departed', 0)))
                entered = int(float(edge.get('entered', 0)))
                edges[edge.get('id')] = {
                    'traveltime': float(edge.get('traveltime', 0)),
                    'waitingTime': float(edge.get('waitingTime', 0)),
                    'vehicleCount': departed + entered,
                    'avgSpeed': float(edge.get('speed', 0)),
                    'density': float(edge.get('density', 0)),
                    'sampledSeconds': float(edge.get('sampledSeconds', 0)),
                }
            except (ValueError, TypeError):
                continue

    return edges


def _get_approach_label(from_edge_id, to_node, net):
    """根据进口边 ID 和路口节点确定进口方向标签"""
    from_edge = net.getEdge(from_edge_id)
    to_node = net.getNode(to_node) if isinstance(to_node, str) else to_node
    if from_edge is None or to_node is None:
        return 'Unknown'
    from_node = from_edge.getFromNode()
    if from_node is None:
        return 'Unknown'
    from_x, from_y = from_node.getCoord()
    to_x, to_y = to_node.getCoord()
    dx = to_x - from_x
    dy = to_y - from_y
    if abs(dx) > abs(dy):
        return 'West' if dx > 0 else 'East'
    else:
        return 'North' if dy > 0 else 'South'


def compute_intersection_stats(net_path, edge_stats):
    """将边统计数据聚合到各个路口，包含进口方向信息"""
    try:
        import sumolib
        net = sumolib.net.readNet(net_path)
    except Exception:
        return {}

    intersection_stats = {}

    for node in net.getNodes():
        node_id = node.getID()

        has_tl = False
        incoming_edges = set()
        for conn in node.getConnections():
            li = conn.getTLLinkIndex()
            if li is not None and li >= 0:
                has_tl = True
                from_edge = conn.getFromLane().getEdge().getID()
                incoming_edges.add(from_edge)

        if not has_tl:
            continue

        total_vehicles = 0
        total_wait_time = 0.0
        total_travel_time = 0.0
        edge_details = []

        for edge_id in incoming_edges:
            if edge_id in edge_stats:
                es = edge_stats[edge_id]
                total_vehicles += es['vehicleCount']
                total_wait_time += es['waitingTime']
                total_travel_time += es['traveltime']
                approach = _get_approach_label(edge_id, node_id, net)
                edge_details.append({
                    'edge_id': edge_id,
                    'approach': approach,
                    'vehicle_count': es['vehicleCount'],
                    'wait_time': round(es['waitingTime'], 2),
                    'avg_speed': round(es['avgSpeed'], 2)
                })

        avg_wait = round(total_wait_time / total_vehicles, 2) if total_vehicles > 0 else 0

        intersection_stats[node_id] = {
            'id': node_id,
            'vehicle_count': total_vehicles,
            'total_wait_time': round(total_wait_time, 2),
            'avg_wait_time': avg_wait,
            'edge_details': edge_details
        }

    return intersection_stats


def compute_evaluation_score(tripinfo_path, intersections_result, edge_stats, duration):
    """使用 scoring 模块计算综合评分（方案一+四融合）。
    intersections_result 需含 cycle_time / edge_details / vehicle_count。
    """
    try:
        from scoring import compute_total_score
    except ImportError:
        return None

    vehicles = parse_tripinfo(tripinfo_path)
    if not vehicles:
        return None

    total_passages = sum(v.get('vehicle_count', 0) for v in intersections_result)
    edgedata_wait = sum(e['waitingTime'] for e in edge_stats.values()) if edge_stats else 0

    # 平均周期（按车流量加权）
    total_cycle_w = sum(v.get('vehicle_count', 0) * (v.get('cycle_time') or 60) for v in intersections_result)
    avg_cycle = total_cycle_w / total_passages if total_passages > 0 else 60

    overall = {
        'completion_rate': round(len([v for v in vehicles if v['completed']]) / len(vehicles) * 100, 1),
        'total_vehicle_passages': total_passages,
        'simulation_duration': duration,
        'total_wait_time': edgedata_wait,
        'avg_cycle_time': avg_cycle,
        'avg_wait_time': edgedata_wait / len(vehicles) if vehicles else 0,
        '_vehicle_time_loss': [v['timeLoss'] for v in vehicles],
    }

    return compute_total_score(overall, intersections_result)


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


@app.route('/')
def index():
    """主页"""
    projects = scan_projects()
    return render_template('index.html', projects=projects)


@app.route('/editor')
def editor():
    """红绿灯可视化编辑平台"""
    return render_template('editor.html')


@app.route('/optimizer')
def optimizer_page():
    """配时枚举优化页面"""
    projects = scan_projects()
    return render_template('optimizer.html', projects=projects)


@app.route('/api/projects/scan', methods=['POST'])
def api_scan_projects():
    """扫描有效项目"""
    data = request.get_json(silent=True) or {}
    root_path = data.get('root_path', PROJECT_ROOT)
    try:
        projects = scan_projects(root_path)
        return jsonify({'projects': projects})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/projects/validate', methods=['POST'])
def api_validate_project():
    """校验并返回项目信息"""
    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')

    if not project_path:
        return jsonify({'valid': False, 'error': '未提供项目路径'}), 400

    if not os.path.exists(project_path):
        return jsonify({'valid': False, 'error': f'项目路径不存在: {project_path}'}), 400

    try:
        info = get_project_info(project_path)
        return jsonify({'valid': True, **info})
    except ValueError as e:
        return jsonify({'valid': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'valid': False, 'error': f'加载失败: {str(e)}'}), 500


@app.route('/api/routes/generate', methods=['POST'])
def api_generate_routes():
    """生成车流路由"""
    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')
    total_vehicles = int(data.get('total_vehicles', 500))
    duration = float(data.get('duration', 600))
    seed = int(data.get('seed', 42))

    if not project_path:
        return jsonify({'success': False, 'error': '未提供项目路径'}), 400

    try:
        network_dir, _ = validate_project(project_path)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    net_files = glob.glob(os.path.join(network_dir, '*.net.xml'))
    net_file = net_files[0]
    output_path = os.path.join(network_dir, 'routes.rou.xml')

    generate_routes_script = os.path.join(SCRIPTS_DIR, 'generate_routes.py')

    cmd = [
        sys.executable, generate_routes_script,
        '--net', net_file,
        '--output', output_path,
        '--total_vehicles', str(total_vehicles),
        '--duration', str(duration),
        '--seed', str(seed),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=PROJECT_ROOT
        )
        if result.returncode != 0:
            return jsonify({
                'success': False,
                'error': '车流生成失败',
                'stderr': result.stderr[-500:],
                'stdout': result.stdout[-500:]
            }), 500

        return jsonify({
            'success': True,
            'message': f'成功生成 {total_vehicles} 辆车',
            'output': output_path,
            'stdout': result.stdout.strip()
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': '车流生成超时（120秒）'}), 500


@app.route('/api/simulation/run', methods=['POST'])
def api_run_simulation():
    """无头模式运行仿真并返回统计数据"""
    global simulation_lock

    if simulation_lock:
        return jsonify({'success': False, 'error': '已有仿真正在运行中'}), 409

    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')
    total_vehicles = int(data.get('total_vehicles', 500))
    duration = float(data.get('duration', 600))
    seed = int(data.get('seed', 42))

    if not project_path:
        return jsonify({'success': False, 'error': '未提供项目路径'}), 400

    # 校验项目
    try:
        network_dir, intersections_dir = validate_project(project_path)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    simulation_lock = True
    try:
        # 步骤 1: 生成车流路由
        net_files = glob.glob(os.path.join(network_dir, '*.net.xml'))
        net_file = net_files[0]
        output_rou = os.path.join(network_dir, 'routes.rou.xml')

        generate_routes_script = os.path.join(SCRIPTS_DIR, 'generate_routes.py')
        cmd = [
            sys.executable, generate_routes_script,
            '--net', net_file,
            '--output', output_rou,
            '--total_vehicles', str(total_vehicles),
            '--duration', str(duration),
            '--seed', str(seed),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=PROJECT_ROOT)

        # 步骤 2: 生成配时文件
        configs = load_intersection_configs(intersections_dir)
        add_file = os.path.join(network_dir, 'timing.add.xml')
        generate_add_xml(net_file, configs, add_file)

        # 步骤 3: 生成仿真配置
        sumocfg_file = os.path.join(network_dir, 'sumo.sumocfg')
        generate_sumocfg(network_dir, net_file, add_file, sumocfg_file, duration=duration)

        # 步骤 4: 运行 SUMO headless
        sumo_bin = find_sumo_cli_binary()
        if sumo_bin is None:
            sumo_bin = find_sumo_binary()
            if sumo_bin and 'gui' in sumo_bin.lower():
                sumo_bin = sumo_bin.replace('-gui', '')
        if sumo_bin is None or not os.path.exists(sumo_bin):
            return jsonify({'success': False, 'error': '未找到 SUMO 可执行文件，请检查 SUMO_HOME 环境变量'}), 500

        try:
            sumo_cmd = [
                sumo_bin, '-c', sumocfg_file,
                '--tripinfo-output', 'tripinfo.xml',
                '--fcd-output', 'fcd.xml',
                '--queue-output', 'queue.xml',
                '--edgedata-output', 'edge.xml',
                '--summary-output', 'summary.xml',
                '--no-warnings', 'true',
                '--no-step-log', 'true',
            ]
            sim_result = subprocess.run(
                sumo_cmd,
                capture_output=True, text=True,
                timeout=int(duration * 2 + 120),
                cwd=network_dir
            )
        except subprocess.TimeoutExpired:
            return jsonify({
                'success': False,
                'error': f'仿真超时（{int(duration * 2 + 120)}秒），请减少仿真时长或车流量'
            }), 500

        # 步骤 5: 解析输出文件
        tripinfo_path = os.path.join(network_dir, 'tripinfo.xml')
        edge_path = os.path.join(network_dir, 'edge.xml')

        if not os.path.exists(tripinfo_path):
            return jsonify({
                'success': False,
                'error': '仿真未生成 tripinfo.xml，可能是 SUMO 运行异常',
                'stderr': sim_result.stderr[-1000:]
            }), 500

        vehicles = parse_tripinfo(tripinfo_path)
        edge_stats = parse_edges(edge_path)
        intersection_data = compute_intersection_stats(net_file, edge_stats)
        overall = compute_overall_stats(vehicles, edge_stats, intersection_data)

        # 合并路口配置信息
        intersections_result = []
        for int_id, stats in intersection_data.items():
            cfg = configs.get(int_id) or configs.get('J' + int_id) or configs.get(int_id.replace('J', ''))
            name = cfg.get('name', int_id) if cfg else int_id
            intersections_result.append({
                **stats,
                'name': name,
                'cycle_time': cfg.get('cycle_time', 0) if cfg else 0,
                'phase_count': len(cfg.get('phases', [])) if cfg else 0
            })

        # 排序
        intersections_result.sort(key=lambda x: x['vehicle_count'], reverse=True)

        # 综合评分
        score = compute_evaluation_score(tripinfo_path, intersections_result, edge_stats, duration)

        run_id = datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]

        return jsonify({
            'success': True,
            'run_id': run_id,
            'parameters': {
                'project_path': project_path,
                'total_vehicles': total_vehicles,
                'duration': duration,
                'seed': seed
            },
            'statistics': {
                'overall': overall,
                'intersections': intersections_result,
                'edge_count': len(edge_stats),
                'score': score
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'仿真异常: {str(e)}'}), 500
    finally:
        simulation_lock = False


@app.route('/api/optimization/run', methods=['POST'])
def api_optimization_run():
    """枚举配时方案并评分，返回 Top N 最优配时"""
    global simulation_lock

    if simulation_lock:
        return jsonify({'success': False, 'error': '已有仿真/优化正在运行中'}), 409

    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')
    intersection_id = data.get('intersection_id', '')
    total_vehicles = int(data.get('total_vehicles', 80))
    duration = float(data.get('duration', 120))
    seed = int(data.get('seed', 42))
    min_green = int(data.get('min_green', 15))
    max_green = int(data.get('max_green', 90))
    step = int(data.get('step', 15))
    target_cycles = tuple(int(x) for x in data.get('target_cycles', [90, 120, 150]))
    top_n = int(data.get('top_n', 5))
    phase_constraints = data.get('phase_constraints')

    if not project_path or not intersection_id:
        return jsonify({'success': False, 'error': '缺少项目路径或路口ID'}), 400

    try:
        network_dir, intersections_dir = validate_project(project_path)
        configs = load_intersection_configs(intersections_dir)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    inter = (configs.get(intersection_id)
             or configs.get('J' + intersection_id)
             or configs.get(intersection_id.replace('J', '')))
    if not inter:
        return jsonify({'success': False, 'error': f'未找到路口: {intersection_id}'}), 404

    net_files = glob.glob(os.path.join(network_dir, '*.net.xml'))
    net_file = net_files[0]

    # 一次性生成车流（固定 seed，保证候选间公平比较）
    output_rou = os.path.join(network_dir, 'routes.rou.xml')
    gen_script = os.path.join(SCRIPTS_DIR, 'generate_routes.py')
    subprocess.run(
        [sys.executable, gen_script, '--net', net_file, '--output', output_rou,
         '--total_vehicles', str(total_vehicles), '--duration', str(duration),
         '--seed', str(seed)],
        capture_output=True, text=True, timeout=120, cwd=PROJECT_ROOT
    )

    sumo_bin = find_sumo_cli_binary() or find_sumo_binary()
    if sumo_bin is None or not os.path.exists(sumo_bin):
        return jsonify({'success': False, 'error': '未找到 SUMO 可执行文件，请检查 SUMO_HOME'}), 500

    simulation_lock = True
    evaluated = 0

    def run_candidate(cand_cfg):
        nonlocal evaluated
        evaluated += 1
        # 保留其他路口配置，仅替换目标路口
        cand_configs = dict(configs)
        cand_configs[inter['id']] = cand_cfg

        add_file = os.path.join(network_dir, 'timing.add.xml')
        generate_add_xml(net_file, cand_configs, add_file)
        sumocfg_file = os.path.join(network_dir, 'sumo.sumocfg')
        generate_sumocfg(network_dir, net_file, add_file, sumocfg_file, duration=duration)

        try:
            subprocess.run(
                [sumo_bin, '-c', sumocfg_file,
                 '--tripinfo-output', 'tripinfo.xml',
                 '--edgedata-output', 'edge.xml',
                 '--no-warnings', 'true', '--no-step-log', 'true'],
                capture_output=True, text=True,
                timeout=int(duration * 2 + 60),
                cwd=network_dir
            )
        except subprocess.TimeoutExpired:
            return None

        tripinfo_path = os.path.join(network_dir, 'tripinfo.xml')
        edge_path = os.path.join(network_dir, 'edge.xml')
        if not os.path.exists(tripinfo_path):
            return None

        vehicles = parse_tripinfo(tripinfo_path)
        edge_stats = parse_edges(edge_path)
        intersection_data = compute_intersection_stats(net_file, edge_stats)

        # 构建带 cycle_time 的路口结果
        inter_result = []
        for iid, st in intersection_data.items():
            cfg = configs.get(iid) or configs.get('J' + iid) or configs.get(iid.replace('J', ''))
            inter_result.append({
                **st,
                'name': cfg.get('name', iid) if cfg else iid,
                'cycle_time': cfg.get('cycle_time', 0) if cfg else 0,
                'phase_count': len(cfg.get('phases', [])) if cfg else 0
            })

        score = compute_evaluation_score(tripinfo_path, inter_result, edge_stats, duration)
        if score:
            score['overall_stats'] = compute_overall_stats(vehicles, edge_stats, intersection_data)
        return score

    try:
        def progress_cb(cur, total, desc, score):
            nonlocal evaluated
            pass  # 前端可通过轮询/SSE 获取，此处仅做占位

        result = optimizer_optimize(
            inter, run_candidate,
            min_green=min_green, max_green=max_green, step=step,
            target_cycles=target_cycles, top_n=top_n,
            phase_constraints=phase_constraints,
            progress_cb=progress_cb
        )
        result['project_path'] = project_path
        result['intersection_id'] = inter['id']
        result['parameters'] = {
            'total_vehicles': total_vehicles, 'duration': duration, 'seed': seed,
            'min_green': min_green, 'max_green': max_green, 'step': step,
            'target_cycles': list(target_cycles)
        }
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'success': False, 'error': f'优化异常: {str(e)}'}), 500
    finally:
        simulation_lock = False


@app.route('/api/simulation/run-gui', methods=['POST'])
def api_run_simulation_gui():
    """启动 SUMO-GUI 可视化（非阻塞）"""
    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')
    total_vehicles = int(data.get('total_vehicles', 500))
    duration = float(data.get('duration', 600))
    seed = int(data.get('seed', 42))

    if not project_path:
        return jsonify({'success': False, 'error': '未提供项目路径'}), 400

    try:
        network_dir, intersections_dir = validate_project(project_path)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        net_files = glob.glob(os.path.join(network_dir, '*.net.xml'))
        net_file = net_files[0]

        # 生成车流
        output_rou = os.path.join(network_dir, 'routes.rou.xml')
        generate_routes_script = os.path.join(SCRIPTS_DIR, 'generate_routes.py')
        cmd = [
            sys.executable, generate_routes_script,
            '--net', net_file,
            '--output', output_rou,
            '--total_vehicles', str(total_vehicles),
            '--duration', str(duration),
            '--seed', str(seed),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=PROJECT_ROOT)

        # 生成配时和配置
        configs = load_intersection_configs(intersections_dir)
        add_file = os.path.join(network_dir, 'timing.add.xml')
        generate_add_xml(net_file, configs, add_file)

        sumocfg_file = os.path.join(network_dir, 'sumo.sumocfg')
        generate_sumocfg(network_dir, net_file, add_file, sumocfg_file, duration=duration)

        # 启动 SUMO-GUI
        sumo_bin = find_sumo_binary()
        if sumo_bin is None:
            return jsonify({'success': False, 'error': '未找到 SUMO-GUI，请检查 SUMO_HOME'}), 500

        subprocess.Popen(
            [sumo_bin, '-c', sumocfg_file],
            cwd=network_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )

        return jsonify({
            'success': True,
            'message': 'SUMO-GUI 已启动，请在弹出的窗口中查看仿真'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'启动失败: {str(e)}'}), 500


@app.route('/api/results/list', methods=['GET'])
def api_results_list():
    """列出所有保存的结果"""
    results = []
    if not os.path.exists(RESULTS_DIR):
        return jsonify({'results': results})

    for filename in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(RESULTS_DIR, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            results.append({
                'run_id': data.get('run_id', filename.replace('.json', '')),
                'label': data.get('label', ''),
                'project_path': data.get('project_path', ''),
                'timestamp': data.get('timestamp', ''),
                'parameters': data.get('parameters', {}),
                'summary': {
                    'total_vehicles': data.get('statistics', {}).get('overall', {}).get('total_vehicles', 0),
                    'avg_wait_time': data.get('statistics', {}).get('overall', {}).get('avg_wait_time', 0),
                    'completion_rate': data.get('statistics', {}).get('overall', {}).get('completion_rate', 0),
                }
            })
        except Exception:
            continue

    return jsonify({'results': results})


def _find_result_file(run_id):
    """通过 run_id 前缀查找结果文件"""
    for filename in os.listdir(RESULTS_DIR):
        if filename.startswith(run_id) and filename.endswith('.json'):
            return os.path.join(RESULTS_DIR, filename)
    return None


@app.route('/api/results/<run_id>', methods=['GET'])
def api_results_get(run_id):
    """获取指定结果详情"""
    filepath = _find_result_file(run_id)
    if not filepath:
        return jsonify({'error': '结果不存在'}), 404

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return jsonify(data)


@app.route('/api/results/<run_id>', methods=['DELETE'])
def api_results_delete(run_id):
    """删除指定结果"""
    filepath = _find_result_file(run_id)
    if not filepath:
        return jsonify({'error': '结果不存在'}), 404

    os.remove(filepath)
    return jsonify({'success': True, 'message': '结果已删除'})


@app.route('/api/results/save', methods=['POST'])
def api_results_save():
    """保存评估结果"""
    data = request.get_json(silent=True) or {}
    statistics = data.get('statistics', {})
    label = data.get('label', '')
    project_path = data.get('project_path', '')
    parameters = data.get('parameters', {})

    if not statistics:
        return jsonify({'success': False, 'error': '没有可保存的统计数据'}), 400

    run_id = data.get('run_id', datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    project_name = data.get('project_name', '')

    # 清理各部分文件名
    safe_label = ''.join(c if c.isalnum() or c in '._- ' else '_' for c in label)
    safe_label = safe_label.strip().replace(' ', '_')[:50]
    safe_project = ''.join(c if c.isalnum() or c in '._- ' else '_' for c in project_name)
    safe_project = safe_project.strip().replace(' ', '_')[:30]

    # 文件名: {run_id}_{project_name}_{label}.json
    parts = [run_id]
    if safe_project:
        parts.append(safe_project)
    if safe_label:
        parts.append(safe_label)
    base_name = '_'.join(parts)

    filename = f'{base_name}.json'
    filepath = os.path.join(RESULTS_DIR, filename)

    counter = 1
    while os.path.exists(filepath):
        filename = f'{base_name}_{counter}.json'
        filepath = os.path.join(RESULTS_DIR, filename)
        counter += 1

    result = {
        'run_id': run_id,
        'label': label,
        'project_path': project_path,
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'parameters': parameters,
        'statistics': statistics
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return jsonify({
        'success': True,
        'run_id': run_id,
        'filename': filename,
        'message': f'结果已保存为 {filename}'
    })


# ─── 红绿灯编辑器 API ─────────────────────────────────────────

@app.route('/api/editor/load', methods=['POST'])
def api_editor_load():
    """加载指定路口配置供编辑器使用"""
    data = request.get_json(silent=True) or {}
    project_path = data.get('project_path', '')
    intersection_id = data.get('intersection_id', '')

    if not project_path or not intersection_id:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    try:
        _, intersections_dir = validate_project(project_path)
        configs = load_intersection_configs(intersections_dir)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    # 尝试多种 ID 匹配
    cfg = configs.get(intersection_id) or configs.get('J' + intersection_id) or configs.get(intersection_id.replace('J', ''))
    if not cfg:
        return jsonify({'success': False, 'error': f'未找到路口: {intersection_id}'}), 404

    # 找到配置文件路径
    json_files = glob.glob(os.path.join(intersections_dir, '*_config.json'))
    config_file = None
    for jf in json_files:
        try:
            with open(jf, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if 'intersections' in d:
                ids = [i.get('id') for i in d['intersections']]
            else:
                ids = [d.get('id')]
            if intersection_id in ids or cfg.get('id') in ids:
                config_file = jf
                break
        except Exception:
            continue

    return jsonify({
        'success': True,
        'intersection': cfg,
        'config_file': config_file,
        'intersections_dir': intersections_dir,
        'project_path': project_path
    })


@app.route('/api/editor/save', methods=['POST'])
def api_editor_save():
    """保存编辑器修改后的路口配置到原文件"""
    data = request.get_json(silent=True) or {}
    config_file = data.get('config_file', '')
    intersection = data.get('intersection', {})

    if not config_file or not intersection:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    if not os.path.exists(config_file):
        return jsonify({'success': False, 'error': f'配置文件不存在: {config_file}'}), 404

    intersection_id = intersection.get('id', '')

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            original = json.load(f)

        if 'intersections' in original:
            # 多路口格式
            replaced = False
            for i, inter in enumerate(original['intersections']):
                if inter.get('id') == intersection_id:
                    original['intersections'][i] = intersection
                    replaced = True
                    break
            if not replaced:
                return jsonify({'success': False, 'error': f'在配置文件中未找到路口 {intersection_id}'}), 404
        else:
            # 单路口格式
            original = intersection

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(original, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'message': f'路口 {intersection_id} 已保存到 {os.path.basename(config_file)}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'保存失败: {str(e)}'}), 500


if __name__ == '__main__':
    print(f'[SUMO] 交通红绿灯算法评价平台启动')
    print(f'  项目根目录: {PROJECT_ROOT}')
    print(f'  结果保存目录: {RESULTS_DIR}')
    print(f'  访问地址: http://127.0.0.1:5000')
    app.run(host='127.0.0.1', port=5000, debug=False)
