class AppError(Exception):
    """Safe, user-presentable error carrying an HTTP status."""
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details

def bad(msg, details=None):      return AppError(422, "validation_error", msg, details)
def not_found(msg):              return AppError(404, "not_found", msg)
def forbidden(msg):              return AppError(403, "forbidden", msg)
def conflict(msg, details=None): return AppError(409, "conflict", msg, details)
