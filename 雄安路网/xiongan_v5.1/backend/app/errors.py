"""业务异常基类。所有带 code/message 的异常在 API 层统一转为错误响应。"""


class AppError(Exception):
    code = 5000

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SchemeError(AppError):
    """方案相关错误：3001 不存在、3002 配置无效、3003 模型不存在。"""
