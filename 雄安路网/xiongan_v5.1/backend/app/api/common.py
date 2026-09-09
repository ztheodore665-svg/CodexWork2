"""统一成功响应包装：{"code": 0, "data": ...}。"""


def ok(data=None) -> dict:
    return {"code": 0, "data": data}
