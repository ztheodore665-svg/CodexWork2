"""路网 GeoJSON 导出与摘要统计。"""

from typing import Any


def _road_type(speed_limit: float) -> str:
    if speed_limit >= 16.7:      # >= 60 km/h
        return "arterial"
    if speed_limit >= 11.1:      # >= 40 km/h
        return "secondary"
    return "local"


def export_geojson(net_path: str) -> dict:
    """读取 .net.xml，导出 FeatureCollection：edge→LineString，node→Point。"""
    import sumolib

    net = sumolib.net.readNet(net_path)
    features: list[dict] = []
    for edge in net.getEdges():
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        coord = [(round(x, 2), round(y, 2)) for x, y in shape]
        # 真实车道几何（逐车道中心线，SUMO 原样，含渐变/弯曲），供前端精确绘制
        lane_shapes = []
        for lane in edge.getLanes():
            ls = lane.getShape()
            if len(ls) >= 2:
                lane_shapes.append([(round(x, 2), round(y, 2)) for x, y in ls])
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coord},
            "properties": {
                "edge_id": edge.getID(),
                "from_node": edge.getFromNode().getID(),
                "to_node": edge.getToNode().getID(),
                "lanes": len(edge.getLanes()),
                "speed_limit": round(float(edge.getSpeed()), 2),
                "length": round(float(edge.getLength()), 2),
                "road_type": _road_type(float(edge.getSpeed())),
                "lane_shapes": lane_shapes,
            },
        })
    for node in net.getNodes():
        x, y = node.getCoord()
        ntype = node.getType()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(x), 2), round(float(y), 2)]},
            "properties": {
                "node_id": node.getID(),
                "tls_id": node.getID() if ntype == "traffic_light" else "",
                "type": ntype,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def network_summary(net_path: str) -> dict:
    import sumolib

    net = sumolib.net.readNet(net_path)
    edges = net.getEdges()
    nodes = net.getNodes()
    lane_length = sum(l.getLength() for e in edges for l in e.getLanes())
    edge_count = len(edges)
    intersection_count = len(nodes)
    tls_count = sum(1 for n in nodes if n.getType() == "traffic_light")

    if nodes:
        xs = [n.getCoord()[0] for n in nodes]
        ys = [n.getCoord()[1] for n in nodes]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    else:
        area = 0.0
    total_lane_km = lane_length / 1000.0
    density = round(total_lane_km / (area / 1e6), 3) if area > 0 else 0.0

    return {
        "edge_count": edge_count,
        "intersection_count": intersection_count,
        "tls_count": tls_count,
        "total_lane_length_km": round(total_lane_km, 3),
        "network_density_km_per_km2": density,
    }
