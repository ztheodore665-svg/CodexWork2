import os

import pytest

from app.core.geojson import export_geojson, network_summary

# 复用项目真实路网（20 路口雄安网络）
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
NET_PATH = os.path.join(REPO_ROOT, "network", "base_network.net.xml")


@pytest.mark.skipif(not os.path.exists(NET_PATH), reason="缺少真实路网文件")
def test_export_geojson_real_network():
    gj = export_geojson(NET_PATH)
    assert gj["type"] == "FeatureCollection"
    lines = [f for f in gj["features"] if f["geometry"]["type"] == "LineString"]
    points = [f for f in gj["features"] if f["geometry"]["type"] == "Point"]
    assert len(lines) > 0 and len(points) > 0
    e = lines[0]
    for key in ("edge_id", "from_node", "to_node", "lanes", "speed_limit", "length", "road_type"):
        assert key in e["properties"]
    assert len(e["geometry"]["coordinates"]) >= 2


@pytest.mark.skipif(not os.path.exists(NET_PATH), reason="缺少真实路网文件")
def test_network_summary_real_network():
    s = network_summary(NET_PATH)
    for key in ("edge_count", "intersection_count", "tls_count",
                "total_lane_length_km", "network_density_km_per_km2"):
        assert key in s
    assert s["edge_count"] > 0
    assert s["intersection_count"] > 0
    assert s["total_lane_length_km"] > 0
