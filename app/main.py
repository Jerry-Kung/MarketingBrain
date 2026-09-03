"""Marketing Brain 应用入口。

启动方式：
    python -m app.main
    或 uvicorn app.main:app
"""
import os

from app.api.routes import create_app_from_settings

app = create_app_from_settings()


if __name__ == "__main__":
    import uvicorn

    # 从配置读取端口
    from app.core.config import load_settings

    s = load_settings()
    uvicorn.run(app, host="0.0.0.0", port=s.APP_PORT, log_level=s.APP_LOG_LEVEL)
