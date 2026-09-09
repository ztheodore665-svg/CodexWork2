import pytest

from app.metrics.store import MetricsStore


def test_store_append_query_with_interval():
    st = MetricsStore(capacity=10000)
    for i in range(10):
        st.append(i, "avg_speed", float(i), "overall")
    rows = st.query("avg_speed", 0, 10, 2)
    assert [r["step"] for r in rows] == [0, 2, 4, 6, 8]
    assert [r["value"] for r in rows] == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_store_scoped_by_intersection():
    st = MetricsStore()
    st.append(1, "queue_length", 5.0, "T1")
    st.append(1, "queue_length", 3.0, "T2")
    assert st.query("queue_length", 0, 10, scope="T1")[0]["value"] == 5.0
    assert st.query("queue_length", 0, 10, scope="T2")[0]["value"] == 3.0


def test_store_unsupported_metric():
    st = MetricsStore()
    with pytest.raises(ValueError):
        st.append(1, "nope", 1.0)


def test_store_capacity_ring():
    st = MetricsStore(capacity=3)
    for i in range(5):
        st.append(i, "avg_speed", float(i))
    rows = st.query("avg_speed", 0, 10)
    assert rows == [{"step": 2, "value": 2.0},
                    {"step": 3, "value": 3.0},
                    {"step": 4, "value": 4.0}]
    assert st.latest("avg_speed") == 4.0
