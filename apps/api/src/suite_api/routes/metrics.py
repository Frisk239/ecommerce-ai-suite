"""指标端点鉴权（第 47 刀）。

``/metrics`` 本体由 instrumentator 的 ``expose`` 注册（RED 与六个自定义指标同源
一处暴露，不自搓第二份实现）；本模块只提供挂在那个端点上的**鉴权依赖**。

口径（observability-intake 裁决 7）：`METRICS_TOKEN` 空 = **一律 401**
（fail-closed，默认栈不裸奔）；非空则要求 ``Authorization: Bearer <token>``，
`compare_digest` 恒定时间比对。**不接受操作者 cookie**——Prometheus 不会登录，
且指标面比治理面宽，单独凭证比复用会话干净。
"""

from hmac import compare_digest

from fastapi import Depends, HTTPException, Request, status

from suite_api.deps import request_settings
from suite_api.settings import Settings

_BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}


def require_metrics_token(
    request: Request, settings: Settings = Depends(request_settings)
) -> None:
    """Bearer 闸：未配置 = 关闭（401），配置了才比 token。

    settings 从 ``app.state`` 读（与全仓请求依赖同口径）——集成测试用自定义
    Settings 建 app 时就该按那份设置判权，而不是进程级 lru_cache。
    """
    expected = settings.metrics_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="指标端点未启用（METRICS_TOKEN 未配置）",
            headers=_BEARER_HEADERS,
        )
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not compare_digest(
        token.encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="指标端点鉴权失败",
            headers=_BEARER_HEADERS,
        )
